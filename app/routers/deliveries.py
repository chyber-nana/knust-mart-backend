import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession
import os
from ..billing import (
    CURRENCY, PURPOSE_COURIER, claim_free_courier_slot, courier_fee_pesewas, courier_slots_left,
    new_reference, paystack_initialize,
)
from ..catalog import (
    DELIVERY_FEE, FIXED_ACCEPT_AFTER_MINUTES, MAX_OPEN_OFFERS, OFFER_MIN_STEP, OFFER_TTL_MINUTES, courier_price_range,
)
from ..db import get_db
from ..mailer import send_email
from ..models import CourierPayout, Delivery, DeliveryOffer, Payment, Product, Review, User, DirectMessage
from ..stock import complete_unit, release_unit, take_unit
from ..switches import courier_allowed, escrow_allowed
from ..escrow import PURPOSE_ORDER, quote, release_to_seller, safe_refund
from ..billing import CURRENCY as PAY_CURRENCY
from ..notifications import add_notification
from ..security import current_user, require_student
from ..catalog import clean_location

router = APIRouter(prefix="/deliveries", tags=["deliveries"])


class DeliveryIn(BaseModel):
    product_id: uuid.UUID
    pickup_location: str | None = None
    dropoff_location: str | None = None
    hint_id: int | None = None
    fulfillment_type: Literal["courier", "pickup"] = "courier"
    delivery_fee: Decimal | None = None
    choose_courier: bool = False
    color: str | None = Field(default=None, max_length=20)
    dropoff_note: str | None = Field(default=None, max_length=120)
    size: str | None = Field(default=None, max_length=20)


class SellerResponseIn(BaseModel):
    available: bool


class OfferIn(BaseModel):
    price: Decimal


def _options(d: Delivery) -> str:
    return " · ".join(x for x in (d.chosen_color, d.chosen_size and f"Size {d.chosen_size}") if x)


def _fresh_offer_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=OFFER_TTL_MINUTES)


def _fixed_accept_open(d: Delivery, open_offers: int) -> bool:
    """Fallback: after a quiet period with no offers, the old fixed-price first-come accept opens."""
    if not d.choose_courier:
        return True  # default: standard price, first courier to accept
    if open_offers or d.courier_available_at is None:
        return False
    return datetime.now(timezone.utc) >= d.courier_available_at + timedelta(minutes=FIXED_ACCEPT_AFTER_MINUTES)


async def _pay_seller(db: AsyncSession, d: Delivery) -> None:
    """Best effort right after handover; the hourly sweeper retries anything that fails."""
    try:
        if await release_to_seller(db, d):
            await db.commit()
    except Exception:
        await db.rollback()


class ScanIn(BaseModel):
    qr_token: str | None = None
    pin_code: str | None = None

async def ban_courier(db: AsyncSession, courier_id, reason: str) -> None:
    c = await db.get(User, courier_id)
    if not c or c.is_banned:
        return
    c.is_banned, c.is_courier, c.restriction = True, False, "suspended"
    send_email(c.email, "Your KNUST Mart account is suspended",
               f"Your account was suspended and courier access revoked. Reason: {reason}. Contact support to appeal.")

async def get_delivery(db: AsyncSession, delivery_id: str) -> Delivery:
    try:
        key = uuid.UUID(delivery_id)
    except ValueError:
        raise HTTPException(404, "Delivery not found.")
    result = await db.execute(
        select(Delivery).where(Delivery.id == key).with_for_update()
    )
    d = result.scalar_one_or_none()
    if d is None:
        raise HTTPException(404, "Delivery not found.")
    return d

@router.post("/couriers/toggle")
async def toggle_courier(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not user.is_verified_student:
        raise HTTPException(403, "Only verified students can deliver.")
    if not user.is_courier:
        if user.is_banned:
            raise HTTPException(403, "Your account is suspended.")
        if user.courier_activated_at is None:
            raise HTTPException(402, "Courier registration is required first.")
    user.is_courier = not user.is_courier
    await db.commit()
    return {"is_courier": user.is_courier}


@router.get("/couriers/activation")
async def courier_activation(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return {
        "active": user.courier_activated_at is not None,
        "activation_type": user.courier_activation_type,
        "free_slots_left": await courier_slots_left(db),
        "fee": courier_fee_pesewas() / 100,
        "currency": CURRENCY,
    }


@router.post("/couriers/activate")
async def activate_courier(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Register as a courier: free for the first few couriers, otherwise a Paystack checkout."""
    if not courier_allowed(user):
        raise HTTPException(403, "Courier registration opens soon.")
    if not user.is_verified_student:
        raise HTTPException(403, "Only verified students can deliver.")
    if user.is_banned:
        raise HTTPException(403, "Your account is suspended.")
    if user.courier_activated_at is not None:
        user.is_courier = True
        await db.commit()
        return {"status": "active", "activation_type": user.courier_activation_type}
    if await claim_free_courier_slot(db, user):
        add_notification(db, user.id, "courier_activated", "You're a KNUST Mart courier",
                         "You got one of the free launch spots. Courier mode is on.", "/courier")
        await db.commit()
        return {"status": "active", "activation_type": "free_launch"}
    await db.rollback()  # release the slot lock before calling Paystack
    amount = courier_fee_pesewas()
    if amount <= 0:
        raise HTTPException(503, "Courier registration is not available right now.")
    frontend_url = os.environ.get("FRONTEND_URL")
    if not frontend_url:
        raise HTTPException(503, "Payments are not available right now.")
    reference = new_reference()
    checkout = await paystack_initialize(
        email=user.email, amount=amount, reference=reference,
        callback_url=f"{frontend_url.rstrip('/')}/store/activated?reference={reference}&purpose=courier",
        metadata={"user_id": str(user.id), "purpose": PURPOSE_COURIER},
    )
    db.add(Payment(user_id=user.id, purpose=PURPOSE_COURIER, reference=reference,
                   amount_pesewas=amount, currency=CURRENCY, status="pending"))
    await db.commit()
    return {"status": "payment_required", "authorization_url": checkout["authorization_url"],
            "reference": reference, "amount": amount / 100, "currency": CURRENCY}

@router.post("", status_code=201)
async def request_delivery(data: DeliveryIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    require_student(user)
    if data.fulfillment_type == "courier" and not courier_allowed(user):
        raise HTTPException(403, "Courier delivery isn't available yet. Please choose Pickup.")
    if data.fulfillment_type == "courier":
        data.dropoff_location = clean_location(data.dropoff_location)
        if not data.dropoff_location:
            raise HTTPException(422, "Choose or type a delivery location.")
    result = await db.execute(
        select(Product).where(Product.id == data.product_id).with_for_update()
    )
    product = result.scalar_one_or_none()
    if product is None or product.status != "active":
        raise HTTPException(409, "This listing is not available.")
    if product.stock is not None and product.stock < 1:
        raise HTTPException(409, "This listing is out of stock.")
    if product.seller_id == user.id:
        raise HTTPException(409, "You cannot request delivery for your own listing.")

    seller = await db.get(User, product.seller_id)
    if seller is None:
        raise HTTPException(404, "Seller not found.")
    pickup_location = seller.hostel_or_business_address or product.hall_location
    if not pickup_location:
        raise HTTPException(409, "The seller has not added a pickup location yet.")
    if data.fulfillment_type == "pickup":
        dropoff_location = pickup_location
    else:
        dropoff_location = data.dropoff_location

    if data.fulfillment_type == "courier" and courier_price_range(pickup_location, dropoff_location) is None:
        raise HTTPException(409, "Courier delivery is only available between on-campus halls and hostels. Choose pickup instead.")

    color = (data.color or "").strip() or None
    size = (data.size or "").strip() or None
    for label, value, allowed in (("colour", color, product.colors or []), ("size", size, product.sizes or [])):
        if allowed and value not in allowed:
            raise HTTPException(422, f"Choose a {label} for this item.")
        if not allowed and value:
            raise HTTPException(422, f"This item has no {label} options.")
    picked = " (" + " · ".join(x for x in (color, size) if x) + ")" if (color or size) else ""
    hint = None
    if data.hint_id is not None:
        hint = await db.get(DirectMessage, data.hint_id)
        if (hint is None or hint.kind != "hint" or hint.receiver_id != user.id
                or hint.product_id != product.id or hint.hint_status != "pending"):
            raise HTTPException(409, "This Hint is no longer available.")    
    take_unit(product)
    d = Delivery(
        dropoff_note=(data.dropoff_note or "").strip() or None if data.fulfillment_type == "courier" else None,
        chosen_color=color,
        chosen_size=size,
        product_id=product.id,
        buyer_id=user.id,
        fulfillment_type=data.fulfillment_type,
        pickup_location=pickup_location,
        dropoff_location=dropoff_location,
        delivery_fee=Decimal(str(DELIVERY_FEE)) if data.fulfillment_type == "courier" else Decimal("0.00"),
        choose_courier=False,  # fixed delivery fee: the buyer pays before a courier is chosen
        delivery_qr_token=secrets.token_urlsafe(32),
        hint_id=hint.id if hint else None,
        pin_code=f"{secrets.randbelow(10**6):06d}",
        status="awaiting_seller",
    )
    db.add(d)
    await db.flush()
    if hint:
        hint.hint_status = "bought"
        add_notification(db, hint.sender_id, "hint_bought", "Your Hint was picked up",
                         f"@{user.username or user.full_name} is buying {product.title} for you.",
                         "/inbox?tab=friends")    
    add_notification(
        db, user.id, "order_requested", "Order requested",
        f"Your delivery request for {product.title} was placed.",
        f"/orders/{d.id}",
    )
    add_notification(
        db, product.seller_id, "order_requested", "New order: open it to confirm you have the item",
        f"A buyer requested delivery for {product.title}{picked}.",
        f"/orders/{d.id}",
    )
    if data.fulfillment_type == "courier":
        courier_ids = await db.scalars(
            select(User.id).where(
                User.is_courier.is_(True),
                User.is_verified_student.is_(True),
                User.is_banned.is_(False),
                User.id != user.id,
            )
        )
        for courier_id in courier_ids:
            add_notification(
                db, courier_id, "delivery_requested", "New delivery request",
                f"{product.title} was ordered and is waiting for the seller to confirm.",
                "/courier",
            )
    await db.commit()
    return {"id": str(d.id), "status": d.status, "delivery_fee": str(d.delivery_fee)}


@router.post("/{delivery_id}/cancel")
async def cancel_delivery(
    delivery_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await get_delivery(db, delivery_id)
    if d.buyer_id != user.id:
        raise HTTPException(403, "Only the buyer can cancel this order.")
    cancellable = (
        {"awaiting_seller", "awaiting_payment", "pending", "accepted"}
        if d.fulfillment_type == "courier"
        else {"awaiting_seller", "awaiting_payment", "ready_for_pickup"}
    )
    if d.status not in cancellable:
        raise HTTPException(409, "This order can no longer be cancelled after handoff.")

    product = (await db.execute(
        select(Product).where(Product.id == d.product_id).with_for_update()
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(409, "The listing for this order no longer exists.")
    d.status = "cancelled"
    release_unit(product)
    await safe_refund(db, d, "Buyer cancelled before handover")
    add_notification(
        db, user.id, "order_cancelled", "Order cancelled",
        f"Your order for {product.title} was cancelled.",
        f"/orders/{d.id}",
    )
    add_notification(
        db, product.seller_id, "order_cancelled", "Order cancelled",
        f"The buyer cancelled their request for {product.title}.",
        f"/orders/{d.id}",
    )
    if d.courier_id:
        add_notification(
            db, d.courier_id, "order_cancelled", "Delivery request cancelled",
            f"The buyer cancelled the delivery for {product.title} before handoff.",
            "/courier",
        )
    elif d.fulfillment_type == "courier":
        courier_ids = await db.scalars(
            select(User.id).where(
                User.is_courier.is_(True),
                User.is_banned.is_(False),
                User.id != user.id,
            )
        )
        for courier_id in courier_ids:
            add_notification(
                db, courier_id, "order_cancelled", "Delivery request cancelled",
                f"The buyer cancelled the delivery for {product.title} before a courier picked it up.",
                "/courier",
            )
    await db.commit()
    return {"status": d.status}


@router.post("/{delivery_id}/seller-response")
async def seller_response(
    delivery_id: str,
    data: SellerResponseIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await get_delivery(db, delivery_id)
    product = await db.get(Product, d.product_id)
    if product is None or product.seller_id != user.id:
        raise HTTPException(403, "Only the seller can respond to this order.")
    if d.status != "awaiting_seller":
        raise HTTPException(409, "This order is no longer waiting for the seller.")

    now = datetime.now(timezone.utc)
    d.seller_responded_at = now
    if not data.available:
        d.status = "failed"
        release_unit(product)
        add_notification(
            db, d.buyer_id, "order_unavailable", "Item unavailable",
            f"The seller could not fulfill your order for {product.title}.",
            f"/orders/{d.id}",
        )
        await db.commit()
        return {"status": d.status}

    buyer = await db.get(User, d.buyer_id)
    if not (buyer and escrow_allowed(buyer)):
        # Escrow is off: the original flow. The buyer pays the seller in person at handover.
        if d.fulfillment_type == "pickup":
            d.status = "ready_for_pickup"
            add_notification(
                db, d.buyer_id, "order_ready_for_pickup", "Ready for pickup",
                f"{product.title} is ready for you at {d.dropoff_location}.",
                f"/orders/{d.id}",
            )
        else:
            d.status = "pending"
            d.courier_available_at = now + timedelta(seconds=10)
            courier_ids = await db.scalars(
                select(User.id).where(
                    User.is_courier.is_(True),
                    User.is_verified_student.is_(True),
                    User.is_banned.is_(False),
                    User.id != d.buyer_id,
                )
            )
            for courier_id in courier_ids:
                add_notification(
                    db, courier_id, "delivery_available", "Delivery ready to accept",
                    f"The seller confirmed {product.title}. You can accept it in 10 seconds.",
                    "/courier",
                )
            add_notification(
                db, d.buyer_id, "order_confirmed", "Seller confirmed your order",
                f"{product.title} is confirmed. A courier can accept it shortly.",
                f"/orders/{d.id}",
            )
        await db.commit()
        return {
            "status": d.status,
            "courier_available_at": d.courier_available_at.isoformat() if d.courier_available_at else None,
        }
    if not user.momo_recipient_code:
        await db.rollback()
        raise HTTPException(409, "Add and confirm your MoMo number in Settings before you accept orders, so we can pay you.")
    q = quote(product.price, d.delivery_fee, d.fulfillment_type == "courier")
    d.item_amount, d.service_fee = product.price, q["service_fee"]
    d.status = "awaiting_payment"
    add_notification(
        db, d.buyer_id, "order_confirmed", "Seller confirmed: pay to secure your order",
        f"{product.title} is available. Pay GH\u20b5{q['total']:.2f} (item GH\u20b5{q['item']:.2f}, delivery GH\u20b5{q['delivery']:.2f}, service fee GH\u20b5{q['service_fee']:.2f}). Your money is held until the handover is confirmed.",
        f"/orders/{d.id}",
    )
    await db.commit()
    return {"status": d.status}


@router.post("/{delivery_id}/pay")
async def pay_for_order(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Buyer pays item + delivery + service fee in one Paystack checkout. The money is held until handover."""
    d = await get_delivery(db, delivery_id)
    if d.buyer_id != user.id or d.status != "awaiting_payment":
        raise HTTPException(409, "This order isn't waiting for payment.")
    product = await db.get(Product, d.product_id)
    if product is None:
        raise HTTPException(409, "The listing for this order no longer exists.")
    frontend_url = os.environ.get("FRONTEND_URL")
    if not frontend_url:
        raise HTTPException(503, "Payments are not available right now.")
    q = quote(d.item_amount, d.delivery_fee, d.fulfillment_type == "courier")
    reference = new_reference()
    checkout = await paystack_initialize(
        email=user.email, amount=q["total_pesewas"], reference=reference,
        callback_url=f"{frontend_url.rstrip('/')}/store/activated?reference={reference}&purpose=order",
        metadata={"user_id": str(user.id), "purpose": PURPOSE_ORDER, "delivery_id": str(d.id)},
    )
    db.add(Payment(user_id=user.id, purpose=PURPOSE_ORDER, delivery_id=d.id, product_id=product.id, reference=reference,
                   amount_pesewas=q["total_pesewas"], currency=PAY_CURRENCY, status="pending"))
    await db.commit()
    return {"authorization_url": checkout["authorization_url"], "reference": reference, "amount": q["total"]}


@router.get("/board")
async def job_board(pickup: str | None = None, dropoff: str | None = None,
                    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not courier_allowed(user):
        raise HTTPException(403, "Courier deliveries open soon.")
    if not user.is_courier:
        raise HTTPException(403, "Turn on courier mode to see jobs.")
    q = (
        select(Delivery, Product)
        .join(Product, Product.id == Delivery.product_id)
        .where(
            Delivery.status == "pending",
            Delivery.fulfillment_type == "courier",
            Delivery.buyer_id != user.id,
            Product.seller_id != user.id,  # sellers can't courier their own goods
        )
    )
    if pickup: q = q.where(Delivery.pickup_location == pickup)
    if dropoff: q = q.where(Delivery.dropoff_location == dropoff)
    rows = (await db.execute(q.order_by(Delivery.created_at))).all()
    ids = [r.id for r, _ in rows]
    offers = (await db.execute(select(DeliveryOffer).where(
        DeliveryOffer.delivery_id.in_(ids), DeliveryOffer.status == "open",
        DeliveryOffer.updated_at >= _fresh_offer_cutoff()))).scalars().all() if ids else []
    out = []
    for r, product in rows:
        rng = courier_price_range(r.pickup_location, r.dropoff_location)
        if rng is None:
            continue
        mine = next((o for o in offers if o.delivery_id == r.id and o.courier_id == user.id), None)
        count = sum(1 for o in offers if o.delivery_id == r.id)
        out.append({
            "id": str(r.id),
            "pickup": r.pickup_location,
            "dropoff": r.dropoff_location,
            "fee": r.delivery_fee,
            "price_min": rng[0], "price_max": rng[1],
            "choose_courier": r.choose_courier,
            "my_offer": mine.price if mine else None,
            "open_offers": count,
            "fixed_accept_open": _fixed_accept_open(r, count),
            "available_at": r.courier_available_at.isoformat() if r.courier_available_at else None,
            "product_title": product.title,
            "options": _options(r),
        })
    return out


@router.post("/{delivery_id}/offer")
async def make_offer(delivery_id: str, data: OfferIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A courier offers to deliver at a price within the allowed range for the trip."""
    d = await get_delivery(db, delivery_id)
    if not user.is_courier or user.is_banned or d.status != "pending" or d.fulfillment_type != "courier" or d.buyer_id == user.id or not d.choose_courier:
        raise HTTPException(409, "You can't make an offer on this job.")
    if await db.scalar(select(Product.id).where(Product.id == d.product_id, Product.seller_id == user.id)):
        raise HTTPException(409, "You can't deliver your own listing.")
    rng = courier_price_range(d.pickup_location, d.dropoff_location)
    if rng is None:
        raise HTTPException(409, "This trip is outside the courier service area.")
    price = data.price
    if price < Decimal(str(rng[0])) or price > Decimal(str(rng[1])):
        raise HTTPException(422, f"Your price must be between GH₵{rng[0]:.0f} and GH₵{rng[1]:.0f} for this trip.")
    if (price * 2) % 1 != 0:
        raise HTTPException(422, f"Prices go up in steps of GH₵{OFFER_MIN_STEP:.2f}.")
    active_jobs = await db.scalar(select(func.count(Delivery.id)).where(
        Delivery.courier_id == user.id, Delivery.status.in_(("accepted", "in_transit"))))
    if (active_jobs or 0) >= 3:
        raise HTTPException(409, "Couriers can have at most 3 active deliveries.")
    now = datetime.now(timezone.utc)
    existing = (await db.execute(select(DeliveryOffer).where(
        DeliveryOffer.delivery_id == d.id, DeliveryOffer.courier_id == user.id))).scalar_one_or_none()
    open_count = await db.scalar(select(func.count(DeliveryOffer.id)).where(
        DeliveryOffer.delivery_id == d.id, DeliveryOffer.status == "open", DeliveryOffer.updated_at >= _fresh_offer_cutoff()))
    is_new = existing is None or existing.status != "open"
    if is_new and (open_count or 0) >= MAX_OPEN_OFFERS:
        raise HTTPException(409, "This request already has the maximum number of offers.")
    if existing:
        existing.price, existing.status, existing.updated_at = price, "open", now
    else:
        db.add(DeliveryOffer(delivery_id=d.id, courier_id=user.id, price=price, status="open", updated_at=now))
    if is_new:
        product = await db.get(Product, d.product_id)
        add_notification(db, d.buyer_id, "courier_offer", "New courier offer",
                         f"A courier offered to deliver {product.title if product else 'your order'}. Choose your courier.",
                         "/orders")
    await db.commit()
    return {"status": "offered", "price": price}


@router.delete("/{delivery_id}/offer")
async def withdraw_offer(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    offer = (await db.execute(select(DeliveryOffer).where(
        DeliveryOffer.delivery_id == d.id, DeliveryOffer.courier_id == user.id, DeliveryOffer.status == "open"))).scalar_one_or_none()
    if offer is None:
        raise HTTPException(404, "You have no open offer on this job.")
    offer.status = "withdrawn"
    await db.commit()
    return {"status": "withdrawn"}


@router.get("/{delivery_id}/offers")
async def list_offers(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """The buyer's view of courier offers. Shows who is offering, never contact details."""
    try:
        key = uuid.UUID(delivery_id)
    except ValueError:
        raise HTTPException(404, "Delivery not found.")
    d = await db.get(Delivery, key)
    if d is None or d.buyer_id != user.id:
        raise HTTPException(404, "Delivery not found.")
    if d.status != "pending":
        return []
    rows = (await db.execute(
        select(DeliveryOffer, User).join(User, User.id == DeliveryOffer.courier_id).where(
            DeliveryOffer.delivery_id == d.id, DeliveryOffer.status == "open",
            DeliveryOffer.updated_at >= _fresh_offer_cutoff(), User.is_courier.is_(True), User.is_banned.is_(False),
        ).order_by(func.random())  # no built-in ranking, so newer couriers aren't buried
    )).all()
    out = []
    for offer, courier in rows:
        done = await db.scalar(select(func.count(Delivery.id)).where(Delivery.courier_id == courier.id, Delivery.status == "delivered"))
        out.append({"id": str(offer.id), "courier_name": courier.full_name.split(" ")[0], "price": offer.price,
                    "completed": done or 0, "is_new": (done or 0) < 3, "verified_student": courier.is_verified_student})
    return out


@router.post("/{delivery_id}/choose-mode")
async def enable_choose_mode(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Buyer opts in to comparing courier offers instead of the standard-price first-come courier."""
    d = await get_delivery(db, delivery_id)
    if d.buyer_id != user.id or d.status != "pending" or d.fulfillment_type != "courier":
        raise HTTPException(409, "You can't change this order right now.")
    d.choose_courier = True
    await db.commit()
    return {"choose_courier": True}


@router.post("/{delivery_id}/offers/{offer_id}/choose")
async def choose_offer(delivery_id: str, offer_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    if d.buyer_id != user.id or d.status != "pending":
        raise HTTPException(409, "You can't choose a courier for this order right now.")
    try:
        oid = uuid.UUID(offer_id)
    except ValueError:
        raise HTTPException(404, "Offer not found.")
    offer = (await db.execute(select(DeliveryOffer).where(DeliveryOffer.id == oid, DeliveryOffer.delivery_id == d.id).with_for_update())).scalar_one_or_none()
    if offer is None or offer.status != "open" or offer.updated_at < _fresh_offer_cutoff():
        raise HTTPException(409, "That offer is no longer available. Pick another courier.")
    courier = await db.get(User, offer.courier_id)
    if courier is None or not courier.is_courier or courier.is_banned:
        raise HTTPException(409, "That courier is no longer available. Pick another courier.")
    active_jobs = await db.scalar(select(func.count(Delivery.id)).where(
        Delivery.courier_id == courier.id, Delivery.status.in_(("accepted", "in_transit"))))
    if (active_jobs or 0) >= 3:
        raise HTTPException(409, "That courier is busy right now. Pick another courier.")
    d.courier_id, d.delivery_fee, d.status = courier.id, offer.price, "accepted"
    offer.status = "chosen"
    others = (await db.execute(select(DeliveryOffer).where(
        DeliveryOffer.delivery_id == d.id, DeliveryOffer.id != offer.id, DeliveryOffer.status == "open"))).scalars().all()
    product = await db.get(Product, d.product_id)
    title = product.title if product else "an order"
    for other in others:
        other.status = "rejected"
        add_notification(db, other.courier_id, "offer_not_chosen", "Another courier was chosen",
                         f"The buyer picked a different courier for {title}.", "/courier")
    add_notification(db, courier.id, "courier_chosen", "You were chosen!",
                     f"The buyer chose you to deliver {title} for GH₵{offer.price:.2f}. Go to {d.pickup_location} and meet the seller. Open the job for their phone number.", "/courier")
    add_notification(db, d.buyer_id, "order_accepted", "Courier assigned",
                     f"{courier.full_name.split(' ')[0]} will deliver {title} for GH₵{offer.price:.2f}. Pay them in person (cash or MoMo).",
                     f"/orders/{d.id}")
    if product:
        add_notification(db, product.seller_id, "order_accepted", "Courier is coming to collect",
                         f"{courier.full_name} is coming for {title}. When you hand it over, tap 'I handed it to the courier'.", f"/orders/{d.id}")
    await db.commit()
    return {"status": d.status, "delivery_fee": d.delivery_fee}


@router.get("/{delivery_id}/receipt")
async def receipt(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    product = await db.get(Product, d.product_id)
    if product is None:
        raise HTTPException(404, "Listing not found.")
    vendor = await db.get(User, product.seller_id)
    if vendor is None:
        raise HTTPException(404, "Seller not found.")
    is_buyer = d.buyer_id == user.id
    is_seller = product.seller_id == user.id
    if not is_buyer and not is_seller:
        raise HTTPException(403, "Only the buyer can view this receipt.")
    courier = await db.get(User, d.courier_id) if d.courier_id else None
    reviewed = False
    if is_buyer and d.status == "delivered":
        reviewed = await db.scalar(
            select(Review.id).where(Review.delivery_id == d.id).limit(1)
        ) is not None
    return {
        "status": d.status,
        "role": "buyer" if is_buyer else "seller",
        "fulfillment_type": d.fulfillment_type,
        "choose_courier": d.choose_courier,
        "product_id": str(product.id),
        "product_title": product.title,
        "options": _options(d),
        "vendor_name": vendor.store_name or vendor.full_name,
        "pickup": d.pickup_location,
        "dropoff": d.dropoff_location,
        "fee": d.delivery_fee,
        "item_amount": d.item_amount,
        "service_fee": d.service_fee,
        "total": Decimal(d.amount_paid_pesewas) / 100 if d.amount_paid_pesewas else None,
        "paid": d.paid_at is not None,
        "refunded": d.refunded_at is not None,
        "released": d.seller_released_at is not None,
        "dropoff_note": d.dropoff_note,
        "courier_name": courier.full_name if courier else None,
        "courier_phone": courier.phone_number if courier and d.status in ("accepted", "in_transit") else None,
        "buyer_id": str(d.buyer_id) if is_seller else None,
        "qr_token": d.delivery_qr_token if is_buyer and d.fulfillment_type == "courier" and d.status == "in_transit" else None,
        "pin_code": d.pin_code if is_buyer and d.fulfillment_type == "courier" and d.status == "in_transit" else None,
        "reviewed": reviewed if is_buyer else None,
    }

@router.post("/{delivery_id}/accept")
async def accept(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not courier_allowed(user):
        raise HTTPException(403, "Courier deliveries open soon.")
    d = await get_delivery(db, delivery_id)
    courier = (await db.execute(
        select(User).where(User.id == user.id).with_for_update()
    )).scalar_one()
    if not courier.is_courier or d.status != "pending" or d.buyer_id == user.id:
        raise HTTPException(409, "This job can't be accepted.")
    if not courier.momo_recipient_code:
        raise HTTPException(409, "Add and confirm your MoMo number in Settings so we can pay you.")
    if await db.scalar(select(Product.id).where(Product.id == d.product_id, Product.seller_id == user.id)):
        raise HTTPException(409, "You can't deliver your own listing.")
    if d.courier_available_at and d.courier_available_at > datetime.now(timezone.utc):
        raise HTTPException(409, "Please wait for the 10-second acceptance cooldown.")
    open_offers = await db.scalar(select(func.count(DeliveryOffer.id)).where(
        DeliveryOffer.delivery_id == d.id, DeliveryOffer.status == "open", DeliveryOffer.updated_at >= _fresh_offer_cutoff()))
    if not _fixed_accept_open(d, open_offers or 0):
        raise HTTPException(409, "Send an offer with your price. The buyer picks a courier.")
    active_jobs = (await db.execute(
        select(func.count(Delivery.id)).where(
            Delivery.courier_id == user.id,
            Delivery.status.in_(("accepted", "in_transit")),
        )
    )).scalar_one()
    if active_jobs >= 3:
        raise HTTPException(409, "Couriers can have at most 3 active deliveries.")
    d.courier_id, d.status = user.id, "accepted"
    product = await db.get(Product, d.product_id)
    if product:
        add_notification(
            db, d.buyer_id, "order_accepted", "Courier assigned",
            f"{courier.full_name} will collect {product.title} from the seller, then bring it to {d.dropoff_location}. Keep your phone on.",
            f"/orders/{d.id}",
        )
        add_notification(
            db, product.seller_id, "order_accepted", "Courier is coming to collect",
            f"{courier.full_name} is coming for {product.title}. When you hand it over, tap 'I handed it to the courier'.",
            f"/orders/{d.id}",
        )
    await db.commit()
    return {"status": d.status}


@router.post("/{delivery_id}/confirm-pickup")
async def confirm_pickup(
    delivery_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    d = await get_delivery(db, delivery_id)
    if d.buyer_id != user.id or d.fulfillment_type != "pickup" or d.status != "ready_for_pickup":
        raise HTTPException(409, "This order is not ready for you to confirm pickup.")
    product = await db.get(Product, d.product_id)
    if product is None:
        raise HTTPException(409, "The listing for this order no longer exists.")
    d.status = "delivered"
    d.completed_at = datetime.now(timezone.utc)
    await complete_unit(db, product, d.id)
    add_notification(
        db, d.buyer_id, "order_delivered", "Pickup confirmed",
        f"You confirmed pickup of {product.title}.",
        f"/orders/{d.id}",
    )
    add_notification(
        db, product.seller_id, "order_delivered", "Order complete",
        f"The buyer confirmed pickup of {product.title}.",
        f"/orders/{d.id}",
    )
    await db.commit()
    await _pay_seller(db, d)
    return {"status": d.status}

@router.get("/sales")
async def sales(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Completed sales for the seller's listings, with a small summary for the dashboard."""
    rows = (await db.execute(
        select(Delivery, Product, User)
        .join(Product, Product.id == Delivery.product_id)
        .join(User, User.id == Delivery.buyer_id)
        .where(Product.seller_id == user.id, Delivery.status == "delivered")
        .order_by(Delivery.completed_at.desc().nullslast()).limit(500)
    )).all()
    now = datetime.now(timezone.utc)
    items, total, month, by_product = [], Decimal("0"), 0, {}
    for d, p, buyer in rows:
        total += p.price
        if d.completed_at and d.completed_at.year == now.year and d.completed_at.month == now.month:
            month += 1
        by_product[p.title] = by_product.get(p.title, 0) + 1
        items.append({
            "id": str(d.id), "product_title": p.title, "options": _options(d), "price": p.price,
            "buyer_name": buyer.full_name, "fulfillment_type": d.fulfillment_type,
            "completed_at": d.completed_at.isoformat() if d.completed_at else None,
        })
    open_orders = await db.scalar(
        select(func.count(Delivery.id)).join(Product, Product.id == Delivery.product_id).where(
            Product.seller_id == user.id, Delivery.status.in_(("awaiting_seller", "awaiting_payment", "pending", "ready_for_pickup", "accepted", "in_transit"))))
    top = max(by_product.items(), key=lambda kv: kv[1]) if by_product else None
    return {"sold": len(items), "earned": total, "this_month": month, "open_orders": open_orders or 0,
            "top_product": {"title": top[0], "count": top[1]} if top else None, "items": items}


@router.get("/active-count")
async def active_count(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Orders still in progress for this user (as buyer, seller or courier): drives the red dot on Orders."""
    n = await db.scalar(
        select(func.count(Delivery.id)).join(Product, Product.id == Delivery.product_id).where(
            Delivery.status.in_(("awaiting_seller", "awaiting_payment", "pending", "ready_for_pickup", "accepted", "in_transit")),
            (Delivery.buyer_id == user.id) | (Delivery.courier_id == user.id) | (Product.seller_id == user.id),
        )
    )
    return {"count": n or 0}


@router.post("/{delivery_id}/release")
async def release_to_courier(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Seller confirms the item was physically handed to the courier. The courier can only start the trip after this."""
    d = await get_delivery(db, delivery_id)
    product = await db.get(Product, d.product_id)
    if product is None or product.seller_id != user.id or d.status != "accepted" or d.fulfillment_type != "courier":
        raise HTTPException(409, "Nothing to hand over right now.")
    if d.seller_released_at is None:
        d.seller_released_at = datetime.now(timezone.utc)
        add_notification(
            db, d.courier_id, "order_released", "Seller confirmed handover",
            f"The seller says {product.title} is in your hands. Tap 'I have it, start delivery' and head to {d.dropoff_location}.",
            "/courier",
        )
    await db.commit()
    return {"released": True}


@router.post("/{delivery_id}/pickup")
async def picked_up(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    if d.courier_id != user.id or d.status != "accepted":
        raise HTTPException(409, "Not your active job.")
    if d.seller_released_at is None:
        raise HTTPException(409, "Wait until the seller confirms they handed you the item.")
    d.status = "in_transit"
    product = await db.get(Product, d.product_id)
    if product:
        add_notification(
            db, d.buyer_id, "order_picked_up", "Order picked up",
            f"{product.title} is with your courier and on its way to {d.dropoff_location}. Have your PIN ready and meet them there.",
            f"/orders/{d.id}",
        )
    await db.commit()
    return {"status": d.status}

@router.post("/{delivery_id}/scan")
async def scan(delivery_id: str, data: ScanIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    if d.courier_id != user.id or d.status != "in_transit":
        raise HTTPException(409, "Not your active job.")
    ok = (data.qr_token and secrets.compare_digest(data.qr_token, d.delivery_qr_token)) or \
         (data.pin_code and secrets.compare_digest(data.pin_code, d.pin_code))
    if not ok:
        raise HTTPException(400, "Code doesn't match this order.")
    d.status, d.completed_at = "delivered", datetime.now(timezone.utc)
    product = await db.get(Product, d.product_id)
    if product is None:
        raise HTTPException(409, "The listing for this delivery no longer exists.")
    await complete_unit(db, product, d.id)
    add_notification(
        db, d.buyer_id, "order_delivered", "Order received",
        f"{product.title} was marked as delivered. You can now review the seller.",
        f"/orders/{d.id}",
    )
    add_notification(
        db, product.seller_id, "order_delivered", "Order complete",
        f"{product.title} was handed over to the buyer.",
        f"/vendors/{product.seller_id}",
    )
    add_notification(
        db, user.id, "order_delivered", "Delivery complete",
        f"You completed the delivery for {product.title}.",
        "/courier",
    )
    db.add(CourierPayout(
        courier_id=user.id,
        delivery_id=d.id,
        amount=d.delivery_fee,
        status="pending" if d.paid_at else "paid",  # paid orders: weekly MoMo batch; older orders were paid in person
    ))
    await db.commit()
    await _pay_seller(db, d)
    return {"status": d.status, "payout": d.delivery_fee, "payout_status": "pending" if d.paid_at else "paid"}

@router.post("/{delivery_id}/fail")
async def fail(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A courier abandoning an accepted job without a valid scan is banned."""
    d = await get_delivery(db, delivery_id)
    if d.courier_id != user.id or d.status not in ("accepted", "in_transit"):
        raise HTTPException(409, "Not your active job.")
    d.status = "failed"
    product = await db.get(Product, d.product_id)
    if product is not None:
        release_unit(product)
    await safe_refund(db, d, "Delivery not completed")
    if product is not None:
        add_notification(
            db, d.buyer_id, "order_failed", "Delivery not completed",
            f"The delivery for {product.title} was not completed.",
            f"/orders/{d.id}",
        )
        add_notification(
            db, product.seller_id, "order_failed", "Delivery issue",
            f"The delivery for {product.title} was not completed.",
            f"/products/{product.id}",
        )
    await ban_courier(db, user.id, "delivery cancelled or failed without buyer confirmation")
    await db.commit()
    return {"status": d.status, "banned": True}

@router.post("/{delivery_id}/report-stolen")
async def report_stolen(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    if d.buyer_id != user.id or d.status not in ("in_transit", "delivered", "failed"):
        raise HTTPException(409, "This order can't be reported.")
    d.status = "disputed"
    product = await db.get(Product, d.product_id)
    if product is not None and product.stock is None and product.status == "active":
        product.status = "reserved"
    if product is not None:
        add_notification(
            db, product.seller_id, "order_disputed", "Order disputed",
            f"The buyer reported an issue with {product.title}.",
            f"/products/{product.id}",
        )
    if d.courier_id:
        add_notification(
            db, d.courier_id, "order_disputed", "Delivery reported",
            "The buyer reported an issue with this delivery.",
            "/courier",
        )
        await ban_courier(db, d.courier_id, "critical fraud flag: buyer reported stolen goods")
    await db.commit()
    return {"status": d.status}


@router.get("/mine")
async def mine(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    buyer = aliased(User)
    q = (
        select(
            Delivery,
            Product,
            User,
            buyer,
            select(Review.id)
            .where(Review.delivery_id == Delivery.id)
            .exists()
            .label("reviewed"),
        )
        .join(Product, Product.id == Delivery.product_id)
        .join(User, User.id == Product.seller_id)
        .join(buyer, buyer.id == Delivery.buyer_id)
        .where(
            (Delivery.buyer_id == user.id)
            | (Delivery.courier_id == user.id)
            | (Product.seller_id == user.id)
        )
        .order_by(Delivery.created_at.desc())
    )
    rows = (await db.execute(q)).all()
    result = []
    for d, p, vendor, order_buyer, reviewed in rows:
        role = "buyer" if d.buyer_id == user.id else "courier" if d.courier_id == user.id else "seller"
        item = {
            "id": str(d.id),
            "role": role,
            "status": d.status,
            "fulfillment_type": d.fulfillment_type,
            "choose_courier": d.choose_courier,
            "pickup": d.pickup_location,
            "dropoff": d.dropoff_location,
            "fee": d.delivery_fee,
            "item_amount": d.item_amount,
            "service_fee": d.service_fee,
            "paid": d.paid_at is not None,
            "refunded": d.refunded_at is not None,
            "product_id": str(p.id),
            "product_title": p.title,
            "options": _options(d),
            "vendor_id": str(vendor.id),
            "vendor_name": vendor.store_name or vendor.full_name,
        }
        if role == "buyer" and d.status == "delivered":
            item["reviewed"] = reviewed
        item["released"] = d.seller_released_at is not None
        item["dropoff_note"] = d.dropoff_note
        if role == "courier" and d.status in ("accepted", "in_transit"):
            item["seller_phone"] = vendor.phone_number
            item["seller_name"] = vendor.store_name or vendor.full_name
            item["buyer_name"] = order_buyer.full_name
            item["buyer_phone"] = order_buyer.phone_number
        if role == "seller":
            item["buyer_id"] = str(order_buyer.id)
        result.append(item)
    return result


@router.get("/payouts")
async def payouts(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not user.is_courier and not await db.scalar(
        select(CourierPayout.id).where(CourierPayout.courier_id == user.id).limit(1)
    ):
        return []
    rows = (await db.execute(
        select(CourierPayout, Delivery, Product)
        .join(Delivery, Delivery.id == CourierPayout.delivery_id)
        .join(Product, Product.id == Delivery.product_id)
        .where(CourierPayout.courier_id == user.id)
        .order_by(CourierPayout.created_at.desc())
    )).all()
    return [{
        "delivery_id": str(delivery.id),
        "product_title": product.title,
        "amount": payout.amount,
        "status": payout.status,
        "created_at": payout.created_at.isoformat(),
    } for payout, delivery, product in rows]
