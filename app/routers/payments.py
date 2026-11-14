import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing import apply_successful_charge, load_payment, paystack_secret, paystack_verify, valid_signature
from ..db import get_db
from ..models import User
from ..security import current_user

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
        await db.commit()
    return {"status": payment.status, "store_active": user.store_activated_at is not None}
