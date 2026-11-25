import json
import uuid

from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing import apply_successful_charge, load_payment, paystack_secret, paystack_verify, valid_signature, mark_failed, FAILED_PAYSTACK, new_reference, paystack_initialize, CURRENCY
from ..db import get_db
from ..escrow import pay_courier_batch, refund_order, release_to_seller, send_announcement, verify_and_save_momo
from ..billing import _paystack
from ..models import CourierPayout, Delivery, Payment, User
from ..security import admin_user, current_user

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/paystack/webhook")
async def paystack_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Server-to-server call from Paystack. Set this URL in the Paystack dashboard."""
    if paystack_secret() is None:
        raise HTTPException(503, "Payments are not configured.")
    raw = await request.body()
    if not valid_signature(raw, request.headers.get("x-paystack-signature")):
        raise HTTPException(401, "Invalid signature.")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid payload.")
    data = event.get("data") if isinstance(event, dict) else None
    if isinstance(data, dict) and event.get("event") in ("transfer.failed", "transfer.reversed"):
        ref = str(data.get("reference") or "")
        if ref.startswith("kmart_po_"):
            from sqlalchemy import select
            d = (await db.execute(select(Delivery).where(Delivery.id == uuid.UUID(ref[9:])))).scalar_one_or_none() if len(ref) == 41 else None
            if d is not None:
                d.seller_paid_at = None  # the sweeper retries it
                await db.commit()
        return {"received": True}
    if not isinstance(data, dict) or event.get("event") != "charge.success":
        return {"received": True}
    reference = data.get("reference")
    payment = await load_payment(db, reference) if isinstance(reference, str) else None
    if payment is not None:
        await apply_successful_charge(db, payment, data)
        await db.commit()
    return {"received": True}


@router.get("/verify/{reference}")
async def verify_payment(reference: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Called by the frontend after the Paystack redirect; confirms with Paystack directly."""
    payment = await load_payment(db, reference)
    if payment is None or payment.user_id != user.id:
        raise HTTPException(404, "Payment not found.")
    if payment.status == "pending":
        data = await paystack_verify(reference)
        if data.get("status") == "success":
            await apply_successful_charge(db, payment, data)
        elif data.get("status") in FAILED_PAYSTACK:
            mark_failed(db, payment)
        await db.commit()
    return {"status": payment.status, "store_active": user.store_live, "courier_active": user.is_courier,
            "momo_confirmed": bool(user.momo_recipient_code), "purpose": payment.purpose,
            "delivery_id": str(payment.delivery_id) if payment.delivery_id else None}


class MomoIn(BaseModel):
    network: Literal["mtn", "vodafone", "airteltigo"]
    number: str = Field(min_length=9, max_length=16)


@router.get("/momo")
async def get_momo(user: User = Depends(current_user)):
    return {"momo_network": user.momo_network, "momo_number": user.momo_number, "momo_name": user.momo_name,
            "confirmed": bool(user.momo_recipient_code)}


@router.post("/momo/setup")
async def start_momo_setup(data: MomoIn, request: Request, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Seller pays GH₵10 before KNUST Mart creates or replaces their Paystack payout recipient.
    Admins can use the same endpoint for controlled test setup without the fee.
    """
    from ..escrow import valid_momo
    number = valid_momo(data.number)
    frontend_url = __import__("os").environ.get("FRONTEND_URL")
    if not frontend_url:
        raise HTTPException(503, "Payments are not available right now.")
    if user.is_admin:
        result = await verify_and_save_momo(user, data.network, number)
        await db.commit()
        return {**result, "admin_test_setup": True}
    reference = new_reference()
    amount = 1000  # GH₵10.00 in pesewas
    checkout = await paystack_initialize(email=user.email, amount=amount, reference=reference,
        callback_url=f"{frontend_url.rstrip('/')}/store/activated?reference={reference}&purpose=momo_setup",
        metadata={"user_id": str(user.id), "purpose": "momo_setup"})
    db.add(Payment(user_id=user.id, purpose="momo_setup", reference=reference,
        amount_pesewas=amount, currency=CURRENCY, status="pending",
        provider_payload={"pending_momo": {"network": data.network, "number": number}}))
    await db.commit()
    return {"authorization_url": checkout["authorization_url"], "reference": reference, "amount": 10}


@router.put("/momo")
async def save_momo(data: MomoIn, user: User = Depends(current_user)):
    raise HTTPException(405, "Use the GH₵10 MoMo setup checkout. Existing payout accounts cannot be removed or overwritten here.")


@router.delete("/momo/admin-test")
async def delete_admin_test_momo(user: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    """Admin-only cleanup for the administrator's own test recipient details."""
    if user.momo_recipient_code:
        await _paystack("DELETE", f"/transferrecipient/{user.momo_recipient_code}")
    user.momo_network = None
    user.momo_number = None
    user.momo_name = None
    user.momo_recipient_code = None
    await db.commit()
    return {"status": "deleted"}


@router.get("/admin/held")
async def admin_held(admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Delivery).where(
        Delivery.paid_at.is_not(None), Delivery.refunded_at.is_(None), Delivery.seller_paid_at.is_(None)))).scalars().all()
    held = sum((Decimal(d.amount_paid_pesewas or 0) / 100 for d in rows), Decimal("0"))
    owed_sellers = sum((d.item_amount or Decimal("0") for d in rows), Decimal("0"))
    owed_couriers = (await db.scalar(select(func.coalesce(func.sum(CourierPayout.amount), 0)).where(CourierPayout.status == "pending"))) or 0
    return {"orders_held": len(rows), "money_held": held, "owed_to_sellers": owed_sellers, "owed_to_couriers": owed_couriers,
            "disputed": [{"id": str(d.id), "item_amount": d.item_amount, "paid": Decimal(d.amount_paid_pesewas or 0) / 100}
                         for d in rows if d.status == "disputed"]}


async def _disputed(db: AsyncSession, delivery_id: str) -> Delivery:
    d = await db.get(Delivery, uuid.UUID(delivery_id))
    if d is None or d.status != "disputed" or d.paid_at is None:
        raise HTTPException(409, "Not a paid, disputed order.")
    return d


@router.post("/admin/orders/{delivery_id}/refund")
async def admin_refund(delivery_id: str, admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    d = await _disputed(db, delivery_id)
    await refund_order(db, d, "Dispute resolved in buyer's favour")
    d.status = "cancelled"
    await db.commit()
    return {"status": d.status}


@router.post("/admin/orders/{delivery_id}/release")
async def admin_release(delivery_id: str, admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    d = await _disputed(db, delivery_id)
    d.status = "delivered"
    await release_to_seller(db, d)
    await db.commit()
    return {"status": d.status}


@router.post("/admin/courier-payouts/run")
async def admin_courier_batch(admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    return {"couriers_paid": await pay_courier_batch(db)}


@router.post("/admin/announce")
async def admin_announce(admin: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    return {"sent": await send_announcement(db)}
