import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..billing import (
    CURRENCY, PURPOSE_STORE, claim_free_slot, free_slots_left, new_reference,
    paystack_initialize, store_fee_pesewas,
)
from ..db import get_db
from ..models import Payment, User
from ..notifications import add_notification
from ..security import get_verified_user

router = APIRouter(prefix="/stores", tags=["stores"])


class ActivateIn(BaseModel):
    store_name: str = Field(min_length=2, max_length=80)
    hostel_or_business_address: str | None = Field(default=None, max_length=120)


@router.get("/activation")
async def activation_status(user: User = Depends(get_verified_user), db: AsyncSession = Depends(get_db)):
    return {
        "active": user.store_activated_at is not None,
        "activation_type": user.store_activation_type,
        "free_slots_left": await free_slots_left(db),
        "fee": store_fee_pesewas(user.user_type) / 100,
        "currency": CURRENCY,
    }


@router.post("/activate")
async def activate_store(data: ActivateIn, user: User = Depends(get_verified_user), db: AsyncSession = Depends(get_db)):
    """Create the user's business: a free launch slot if one is left, otherwise a Paystack checkout."""
    if user.store_activated_at is not None:
        return {"status": "active", "activation_type": user.store_activation_type}
    name = data.store_name.strip()
    if len(name) < 2:
        raise HTTPException(422, "Enter a business name.")
    user.store_name = name
    if data.hostel_or_business_address is not None:
        user.hostel_or_business_address = data.hostel_or_business_address.strip() or None

    if await claim_free_slot(db, user):
        add_notification(
            db, user.id, "store_activated", "Your business is live",
            "You got one of the free launch spots. Add your logo and banner next.", "/store/edit",
        )
        await db.commit()
        return {"status": "active", "activation_type": "free_launch"}
    await db.commit()  # saves the name and releases the slot lock before calling Paystack

    amount = store_fee_pesewas(user.user_type)
    if amount <= 0:
        raise HTTPException(503, "Business activation is not available right now.")
    frontend_url = os.environ.get("FRONTEND_URL")
    if not frontend_url:
        raise HTTPException(503, "Payments are not available right now.")
    reference = new_reference()
    checkout = await paystack_initialize(
        email=user.email,
        amount=amount,
        reference=reference,
        callback_url=f"{frontend_url.rstrip('/')}/store/activated?reference={reference}",
        metadata={"user_id": str(user.id), "purpose": PURPOSE_STORE},
    )
    db.add(Payment(
        user_id=user.id, purpose=PURPOSE_STORE, reference=reference,
        amount_pesewas=amount, currency=CURRENCY, status="pending",
    ))
    await db.commit()
    return {
        "status": "payment_required",
        "authorization_url": checkout["authorization_url"],
        "reference": reference,
        "amount": amount / 100,
        "currency": CURRENCY,
    }
