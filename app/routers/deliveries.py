import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import aliased
from sqlalchemy.ext.asyncio import AsyncSession
from ..catalog import DELIVERY_FEE
from ..db import get_db
from ..mailer import send_email
from ..models import CourierPayout, Delivery, Product, Review, User
from ..notifications import add_notification
from ..security import current_user

router = APIRouter(prefix="/deliveries", tags=["deliveries"])
HALLS = {
    "Unity",
    "Independence",
    "Evandy",
    "Republic",
    "Queen Elizabeth II",
    "Africa",
    "Off-Campus Commercial Hub",
    "Main Library",
    "Student Union Building",
}

class DeliveryIn(BaseModel):
    product_id: uuid.UUID
    pickup_location: str
    dropoff_location: str
    fulfillment_type: Literal["courier", "pickup"] = "courier"
    delivery_fee: Decimal | None = None


class SellerResponseIn(BaseModel):
    available: bool


class ScanIn(BaseModel):
    qr_token: str | None = None
    pin_code: str | None = None

async def ban_courier(db: AsyncSession, courier_id, reason: str) -> None:
    c = await db.get(User, courier_id)
    if not c or c.is_banned:
        return
    c.is_banned, c.is_courier = True, False
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
    user.is_courier = not user.is_courier
    await db.commit()
    return {"is_courier": user.is_courier}

@router.post("", status_code=201)
async def request_delivery(data: DeliveryIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if data.pickup_location not in HALLS or data.dropoff_location not in HALLS:
        raise HTTPException(422, "Choose a listed hall or hub.")
    result = await db.execute(
        select(Product).where(Product.id == data.product_id).with_for_update()
    )
    product = result.scalar_one_or_none()
    if product is None or product.status != "active":
        raise HTTPException(409, "This listing is not available.")
    if product.seller_id == user.id:
        raise HTTPException(409, "You cannot request delivery for your own listing.")

    product.status = "reserved"
    d = Delivery(
        product_id=product.id,
        buyer_id=user.id,
        fulfillment_type=data.fulfillment_type,
        pickup_location=data.pickup_location,
        dropoff_location=data.dropoff_location,
        delivery_fee=DELIVERY_FEE if data.fulfillment_type == "courier" else Decimal("0.00"),
        delivery_qr_token=secrets.token_urlsafe(32),
        pin_code=f"{secrets.randbelow(10**6):06d}",
        status="awaiting_seller",
    )
    db.add(d)
    await db.flush()
    add_notification(
        db, user.id, "order_requested", "Order requested",
        f"Your delivery request for {product.title} was placed.",
        f"/orders/{d.id}",
    )
    add_notification(
        db, product.seller_id, "order_requested", "New order",
        f"A buyer requested delivery for {product.title}.",
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

    if not data.available:
        d.status = "failed"
        product.status = "active"
        add_notification(
            db, d.buyer_id, "order_unavailable", "Item unavailable",
            f"The seller could not fulfill your order for {product.title}.",
            f"/orders/{d.id}",
        )
        await db.commit()
        return {"status": d.status}

    now = datetime.now(timezone.utc)
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


@router.get("/board")
async def job_board(pickup: str | None = None, dropoff: str | None = None,
                    user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not user.is_courier:
        raise HTTPException(403, "Turn on courier mode to see jobs.")
    q = (
        select(Delivery, Product)
        .join(Product, Product.id == Delivery.product_id)
        .where(
            Delivery.status == "pending",
            Delivery.fulfillment_type == "courier",
            Delivery.buyer_id != user.id,
        )
    )
    if pickup: q = q.where(Delivery.pickup_location == pickup)
    if dropoff: q = q.where(Delivery.dropoff_location == dropoff)
    rows = (await db.execute(q.order_by(Delivery.created_at))).all()
    return [{
        "id": str(r.id),
        "pickup": r.pickup_location,
        "dropoff": r.dropoff_location,
        "fee": r.delivery_fee,
        "available_at": r.courier_available_at.isoformat() if r.courier_available_at else None,
        "product_title": product.title,
    } for r, product in rows]

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
    reviewed = False
    if is_buyer and d.status == "delivered":
        reviewed = await db.scalar(
            select(Review.id).where(Review.delivery_id == d.id).limit(1)
        ) is not None
    return {
        "status": d.status,
        "role": "buyer" if is_buyer else "seller",
        "fulfillment_type": d.fulfillment_type,
        "product_id": str(product.id),
        "product_title": product.title,
        "vendor_name": vendor.store_name or vendor.full_name,
        "pickup": d.pickup_location,
        "dropoff": d.dropoff_location,
        "fee": d.delivery_fee,
        "buyer_id": str(d.buyer_id) if is_seller else None,
        "qr_token": d.delivery_qr_token if is_buyer and d.fulfillment_type == "courier" and d.status == "in_transit" else None,
        "pin_code": d.pin_code if is_buyer and d.fulfillment_type == "courier" and d.status == "in_transit" else None,
        "reviewed": reviewed if is_buyer else None,
    }

@router.post("/{delivery_id}/accept")
async def accept(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    courier = (await db.execute(
        select(User).where(User.id == user.id).with_for_update()
    )).scalar_one()
    if not courier.is_courier or d.status != "pending" or d.buyer_id == user.id:
        raise HTTPException(409, "This job can't be accepted.")
    if d.courier_available_at and d.courier_available_at > datetime.now(timezone.utc):
        raise HTTPException(409, "Please wait for the 10-second acceptance cooldown.")
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
            f"A courier accepted your order for {product.title}.",
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
    product.status = "sold"
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
    return {"status": d.status}

@router.post("/{delivery_id}/pickup")
async def picked_up(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    d = await get_delivery(db, delivery_id)
    if d.courier_id != user.id or d.status != "accepted":
        raise HTTPException(409, "Not your active job.")
    d.status = "in_transit"
    product = await db.get(Product, d.product_id)
    if product:
        add_notification(
            db, d.buyer_id, "order_picked_up", "Order picked up",
            f"Your order for {product.title} is on its way.",
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
    product.status = "sold"
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
        status="pending",
    ))
    await db.commit()
    return {"status": d.status, "payout": d.delivery_fee, "payout_status": "pending"}

@router.post("/{delivery_id}/fail")
async def fail(delivery_id: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """A courier abandoning an accepted job without a valid scan is banned."""
    d = await get_delivery(db, delivery_id)
    if d.courier_id != user.id or d.status not in ("accepted", "in_transit"):
        raise HTTPException(409, "Not your active job.")
    d.status = "failed"
    product = await db.get(Product, d.product_id)
    if product is not None and product.status == "reserved":
        product.status = "active"
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
    if product is not None and product.status == "active":
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
            "pickup": d.pickup_location,
            "dropoff": d.dropoff_location,
            "fee": d.delivery_fee,
            "product_id": str(p.id),
            "product_title": p.title,
            "vendor_id": str(vendor.id),
            "vendor_name": vendor.store_name or vendor.full_name,
        }
        if role == "buyer" and d.status == "delivered":
            item["reviewed"] = reviewed
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
