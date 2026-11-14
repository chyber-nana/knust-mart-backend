"""Store activation pricing, free-slot claiming and Paystack helpers.

Settings are read from the environment on every call so they can be changed in
.env without code edits (and monkeypatched in tests):
  FREE_STORE_SLOTS        default 20
  STORE_FEE_STUDENT_GHS   default 25
  STORE_FEE_EXTERNAL_GHS  default 30
  PAYSTACK_SECRET_KEY     required for paid activation and the webhook
"""
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Payment, User
from .notifications import add_notification

PAYSTACK_API = "https://api.paystack.co"
CURRENCY = "GHS"
PURPOSE_STORE = "store_activation"
_SLOT_LOCK_KEY = 726_001  # app-wide advisory lock id used when claiming a free slot


def free_store_slots() -> int:
    try:
        return max(0, int(os.environ.get("FREE_STORE_SLOTS", "20")))
    except ValueError:
        return 20


def store_fee_pesewas(user_type: str) -> int:
    name, default = (
        ("STORE_FEE_STUDENT_GHS", "25") if user_type == "student" else ("STORE_FEE_EXTERNAL_GHS", "30")
    )
    try:
        fee = Decimal(os.environ.get(name, default))
    except InvalidOperation:
        fee = Decimal(default)
    return max(0, int((fee * 100).to_integral_value()))


def paystack_secret() -> str | None:
    return os.environ.get("PAYSTACK_SECRET_KEY") or None


def new_reference() -> str:
    return f"kmart_{uuid.uuid4().hex}"


async def free_slots_left(db: AsyncSession) -> int:
    used = (await db.execute(
        select(func.count()).select_from(User).where(User.store_activation_type == "free_launch")
    )).scalar_one()
    return max(0, free_store_slots() - used)


async def claim_free_slot(db: AsyncSession, user: User) -> bool:
    """Take one free launch slot for `user` if any remain.

    The advisory lock serialises concurrent claims so the cap can never be
    exceeded. It is held until the caller commits or rolls back, so commit
    straight away and never call an external service while holding it.
    """
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SLOT_LOCK_KEY})
    if await free_slots_left(db) <= 0:
        return False
    user.store_activated_at = datetime.now(timezone.utc)
    user.store_activation_type = "free_launch"
    await db.flush()
    return True


def valid_signature(raw_body: bytes, signature: str | None) -> bool:
    """Paystack signs the raw request body with HMAC-SHA512 using the secret key."""
    secret = paystack_secret()
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature.strip().lower())


def _headers() -> dict[str, str]:
    secret = paystack_secret()
    if not secret:
        raise HTTPException(503, "Payments are not available right now.")
    return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}


async def _paystack(method: str, path: str, **kwargs) -> dict:
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(method, f"{PAYSTACK_API}{path}", headers=headers, **kwargs)
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Could not reach the payment provider. Try again.")
    if response.status_code != 200 or not body.get("status") or not isinstance(body.get("data"), dict):
        raise HTTPException(502, "The payment provider could not process this request. Try again.")
    return body["data"]


async def paystack_initialize(*, email: str, amount: int, reference: str, callback_url: str, metadata: dict) -> dict:
    return await _paystack("POST", "/transaction/initialize", json={
        "email": email, "amount": amount, "currency": CURRENCY,
        "reference": reference, "callback_url": callback_url, "metadata": metadata,
    })


async def paystack_verify(reference: str) -> dict:
    return await _paystack("GET", f"/transaction/verify/{reference}")


async def load_payment(db: AsyncSession, reference: str) -> Payment | None:
    # Row lock so a webhook and a verify call cannot finalise the same payment twice.
    return (await db.execute(
        select(Payment).where(Payment.reference == reference).with_for_update()
    )).scalar_one_or_none()


async def apply_successful_charge(db: AsyncSession, payment: Payment, data: dict) -> bool:
    """Idempotently settle a payment from a Paystack transaction object.

    Returns True when the payment is settled. Anything that does not match what
    we asked for (status, amount, currency) is flagged for review and does NOT
    activate the store.
    """
    if payment.status == "success":
        return True
    try:
        amount = int(data.get("amount"))
    except (TypeError, ValueError):
        amount = -1
    matches = (
        data.get("status") == "success"
        and amount == payment.amount_pesewas
        and str(data.get("currency", "")).upper() == payment.currency
    )
    payment.provider_payload = data
    if not matches:
        payment.status = "flagged"
        await db.flush()
        return False
    now = datetime.now(timezone.utc)
    payment.status = "success"
    payment.paid_at = now
    user = await db.get(User, payment.user_id)
    if user is not None and user.store_activated_at is None:
        user.store_activated_at = now
        user.store_activation_type = "paid"
        add_notification(
            db, user.id, "store_activated", "Your business is live",
            "Payment received. Your storefront is now active.", "/store/edit",
        )
    await db.flush()
    return True
