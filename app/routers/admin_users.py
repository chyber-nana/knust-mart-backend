import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Delivery, EmailVerification, PasswordReset, Product, User, UserBadge
from ..security import admin_user, pwd

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


@router.get("")
async def list_users(
    q: str | None = Query(default=None, max_length=120),
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(User)
        .where(User.anonymized_at.is_(None))
        .order_by(User.created_at.desc())
        .limit(100)
    )
    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            (User.full_name.ilike(term))
            | (User.email.ilike(term))
            | (User.store_name.ilike(term))
        )
    users = (await db.scalars(stmt)).all()
    return {
        "users": [
            {
                "id": str(user.id),
                "name": user.store_name or user.full_name,
                "email": user.email,
                "is_admin": user.is_admin,
                "is_banned": user.is_banned,
            }
            for user in users
        ]
    }


@router.delete("/{user_id}")
async def anonymize_user(
    user_id: uuid.UUID,
    admin: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(select(User).where(User.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot anonymize your own admin account.")
    if user.is_admin:
        raise HTTPException(409, "Admin accounts cannot be anonymized here.")
    if user.anonymized_at is not None:
        raise HTTPException(409, "This account has already been anonymized.")
    active_deliveries = await db.scalar(
        select(func.count(Delivery.id))
        .join(Product, Product.id == Delivery.product_id)
        .where(
            Delivery.status.in_(("awaiting_seller", "pending", "ready_for_pickup", "accepted", "in_transit")),
            or_(
                Delivery.buyer_id == user.id,
                Delivery.courier_id == user.id,
                Product.seller_id == user.id,
            ),
        )
    )
    if active_deliveries:
        raise HTTPException(409, "Resolve this user's active orders before anonymizing the account.")

    original_email = user.email
    await db.execute(delete(EmailVerification).where(EmailVerification.email == original_email))
    await db.execute(delete(PasswordReset).where(PasswordReset.email == original_email))
    await db.execute(delete(UserBadge).where(UserBadge.user_id == user.id))

    user.full_name = "Deleted user"
    user.email = f"deleted-{user.id}@anonymized.invalid"
    user.password_hash = pwd.hash(secrets.token_urlsafe(48))
    user.is_email_verified = False
    user.is_verified_student = False
    user.is_verified = False
    user.is_banned = True
    user.is_courier = False
    user.otp_code = None
    user.otp_expires_at = None
    user.hostel_or_business_address = None
    user.phone_number = None
    user.whatsapp_number = None
    user.store_name = None
    user.store_bio = None
    user.logo_url = None
    user.banner_url = None
    user.anonymized_at = datetime.now(timezone.utc)

    await db.commit()
    return {"id": str(user.id), "anonymized": True}
