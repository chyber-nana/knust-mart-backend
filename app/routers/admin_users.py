import secrets
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Delivery, EmailVerification, PasswordReset, Payment, Product, User, UserBadge
from ..security import admin_user, pwd
from ..stock import ACTIVE_ORDER

router = APIRouter(prefix="/admin/users", tags=["admin-users"])


class ModerationIn(BaseModel):
    action: Literal["suspend", "ban", "restore"]
    target: Literal["account", "business"]


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def user_card(user: User) -> dict:
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "name": user.store_name or user.full_name,
        "email": user.email,
        "user_type": user.user_type,
        "is_verified_student": user.is_verified_student,
        "is_email_verified": user.is_email_verified,
        "is_verified": user.is_verified,
        "is_courier": user.is_courier,
        "is_banned": user.is_banned,
        "restriction": user.restriction,
        "is_admin": user.is_admin,
        "hostel_or_business_address": user.hostel_or_business_address,
        "store_name": user.store_name,
        "store_bio": user.store_bio,
        "phone_number": user.phone_number,
        "whatsapp_number": user.whatsapp_number,
        "logo_url": user.logo_url,
        "banner_url": user.banner_url,
        "store_activated_at": _iso(user.store_activated_at),
        "store_activation_type": user.store_activation_type,
        "store_suspended_at": _iso(user.store_suspended_at),
        "store_active": user.store_live,
        "has_business": user.store_activated_at is not None or user.store_name is not None,
        "created_at": _iso(user.created_at),
        "terms_accepted_at": _iso(user.terms_accepted_at),
        "terms_version": user.terms_version,
        "business_terms_accepted_at": _iso(user.business_terms_accepted_at),
        "business_terms_version": user.business_terms_version,
        "anonymized_at": _iso(user.anonymized_at),
    }


def _search_filter(term: str):
    like = f"%{term}%"
    return or_(
        User.full_name.ilike(like),
        User.email.ilike(like),
        User.store_name.ilike(like),
        User.phone_number.ilike(like),
        User.whatsapp_number.ilike(like),
        User.hostel_or_business_address.ilike(like),
    )


@router.get("")
async def list_users(
    q: str | None = Query(default=None, max_length=120),
    kind: Literal["all", "users", "businesses"] = Query(default="all"),
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    visible = User.anonymized_at.is_(None)
    user_count = await db.scalar(select(func.count(User.id)).where(visible)) or 0
    business_count = await db.scalar(
        select(func.count(User.id)).where(visible, User.store_activated_at.is_not(None))
    ) or 0
    suspended_count = await db.scalar(
        select(func.count(User.id)).where(visible, User.restriction == "suspended")
    ) or 0
    banned_count = await db.scalar(
        select(func.count(User.id)).where(visible, User.restriction == "banned")
    ) or 0
    live_businesses = await db.scalar(
        select(func.count(User.id)).where(
            visible,
            User.store_activated_at.is_not(None),
            User.store_suspended_at.is_(None),
            User.is_banned.is_(False),
        )
    ) or 0

    stmt = select(User).where(visible).order_by(User.created_at.desc()).limit(150)
    if q and q.strip():
        stmt = stmt.where(_search_filter(q.strip()))
    if kind == "businesses":
        stmt = stmt.where(User.store_activated_at.is_not(None))
    users = (await db.scalars(stmt)).all()
    return {
        "stats": {
            "users": user_count,
            "businesses": business_count,
            "live_businesses": live_businesses,
            "suspended": suspended_count,
            "banned": banned_count,
        },
        "users": [user_card(user) for user in users],
    }


@router.get("/{user_id}")
async def user_detail(
    user_id: uuid.UUID,
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    products = (await db.scalars(
        select(Product).where(Product.seller_id == user.id).order_by(Product.created_at.desc()).limit(50)
    )).all()
    deliveries = (await db.execute(
        select(Delivery, Product)
        .join(Product, Product.id == Delivery.product_id)
        .where(
            or_(
                Delivery.buyer_id == user.id,
                Delivery.courier_id == user.id,
                Product.seller_id == user.id,
            )
        )
        .order_by(Delivery.created_at.desc())
        .limit(50)
    )).all()
    payments = (await db.scalars(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(30)
    )).all()
    badges = (await db.scalars(select(UserBadge.badge_key).where(UserBadge.user_id == user.id))).all()
    pending_orders = await db.scalar(
        select(func.count(Delivery.id))
        .join(Product, Product.id == Delivery.product_id)
        .where(
            Delivery.status.in_(ACTIVE_ORDER),
            or_(
                Delivery.buyer_id == user.id,
                Delivery.courier_id == user.id,
                Product.seller_id == user.id,
            ),
        )
    ) or 0
    return {
        **user_card(user),
        "badges": list(badges),
        "pending_orders": pending_orders,
        "listings": [
            {
                "id": str(product.id),
                "title": product.title,
                "price": float(product.price),
                "category": product.category,
                "status": product.status,
                "stock": product.stock,
                "created_at": _iso(product.created_at),
            }
            for product in products
        ],
        "orders": [
            {
                "id": str(delivery.id),
                "status": delivery.status,
                "fulfillment_type": delivery.fulfillment_type,
                "product_title": product.title,
                "role": (
                    "buyer" if delivery.buyer_id == user.id
                    else "courier" if delivery.courier_id == user.id
                    else "seller"
                ),
                "pickup": delivery.pickup_location,
                "dropoff": delivery.dropoff_location,
                "fee": float(delivery.delivery_fee),
                "created_at": _iso(delivery.created_at),
                "completed_at": _iso(delivery.completed_at),
            }
            for delivery, product in deliveries
        ],
        "payments": [
            {
                "id": str(payment.id),
                "purpose": payment.purpose,
                "reference": payment.reference,
                "amount": payment.amount_pesewas / 100,
                "currency": payment.currency,
                "status": payment.status,
                "created_at": _iso(payment.created_at),
                "paid_at": _iso(payment.paid_at),
            }
            for payment in payments
        ],
    }


@router.post("/{user_id}/moderation")
async def moderate_user(
    user_id: uuid.UUID,
    data: ModerationIn,
    admin: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(select(User).where(User.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found.")
    if user.anonymized_at is not None:
        raise HTTPException(409, "This account has already been anonymized.")
    if user.id == admin.id:
        raise HTTPException(400, "You cannot moderate your own admin account.")
    if user.is_admin:
        raise HTTPException(409, "Admin accounts cannot be moderated here.")

    if data.target == "account":
        if data.action == "restore":
            user.is_banned = False
            user.restriction = None
        else:
            user.is_banned = True
            user.restriction = data.action
            if data.action == "ban":
                user.is_courier = False
                if user.store_activated_at is not None:
                    user.store_suspended_at = user.store_suspended_at or datetime.now(timezone.utc)
    else:
        if user.store_activated_at is None and not user.store_name:
            raise HTTPException(404, "This user does not have a business.")
        if data.action == "restore":
            user.store_suspended_at = None
        else:
            user.store_suspended_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)
    return user_card(user)


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
            Delivery.status.in_(ACTIVE_ORDER),
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
    user.restriction = "banned"
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
    user.store_suspended_at = datetime.now(timezone.utc)
    user.anonymized_at = datetime.now(timezone.utc)

    await db.commit()
    return {"id": str(user.id), "anonymized": True}
