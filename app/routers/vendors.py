import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import Delivery, Product, Review, User, UserBadge
from ..security import admin_user, current_user
from .products import card, images_for
from .uploads import is_uploaded_image_url

router = APIRouter(prefix="/vendors", tags=["vendors"])
BADGE_LABELS = {
    "pioneer": "Pioneer",
    "top_rated": "Top Rated",
    "fast_responder": "Fast Responder",
    "verified_student": "Verified Student",
    "campus_legend": "Campus Legend",
    "trusted_vendor": "Trusted Vendor",
    "sales_20": "20+ Sales",
    "power_seller": "Power Seller",
}

class StoreIn(BaseModel):
    store_name: str | None = Field(default=None, max_length=80)
    store_bio: str | None = Field(default=None, max_length=500)
    hostel_or_business_address: str | None = Field(default=None, max_length=120)
    phone_number: str | None = Field(default=None, max_length=32, pattern=r"^(?:\+?[0-9][0-9 ()-]{5,30})?$")
    whatsapp_number: str | None = Field(default=None, max_length=32, pattern=r"^(?:\+?[0-9][0-9 ()-]{5,30})?$")
    logo_url: str | None = None
    logo_position_x: int = Field(default=50, ge=0, le=100)
    logo_position_y: int = Field(default=50, ge=0, le=100)
    banner_url: str | None = None
    banner_position_x: int = Field(default=50, ge=0, le=100)
    banner_position_y: int = Field(default=50, ge=0, le=100)

@router.patch("/me")
async def update_store(data: StoreIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    changes = data.model_dump(exclude_unset=True)
    store_changes = set(changes) - {"phone_number", "whatsapp_number"}
    if store_changes and user.store_activated_at is None:
        raise HTTPException(403, "Create your business first.")
    for k in ("logo_url", "banner_url"):
        if changes.get(k) and not is_uploaded_image_url(changes[k]):
            raise HTTPException(422, "Upload images through the app first.")
    for k, v in changes.items():
        setattr(user, k, v.strip() or None if isinstance(v, str) else v)
    await db.commit()
    return {"id": str(user.id)}


class ContactIn(BaseModel):
    phone_number: str | None = Field(default=None, max_length=32, pattern=r"^(?:\+?[0-9][0-9 ()-]{5,30})?$")
    whatsapp_number: str | None = Field(default=None, max_length=32, pattern=r"^(?:\+?[0-9][0-9 ()-]{5,30})?$")
    hostel_or_business_address: str | None = Field(default=None, max_length=120)


@router.patch("/me/contacts")
async def update_contacts(
    data: ContactIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    sent = data.model_fields_set  # only touch fields the client actually sent
    if "phone_number" in sent:
        user.phone_number = (data.phone_number or "").strip() or None
    if "whatsapp_number" in sent:
        user.whatsapp_number = (data.whatsapp_number or "").strip() or None
    if "hostel_or_business_address" in sent:
        user.hostel_or_business_address = (data.hostel_or_business_address or "").strip() or None
    await db.commit()
    return {
        "phone_number": user.phone_number,
        "whatsapp_number": user.whatsapp_number,
        "hostel_or_business_address": user.hostel_or_business_address,
    }


async def public_badges(db: AsyncSession, user: User) -> list[str]:
    stored = set((await db.scalars(select(UserBadge.badge_key).where(UserBadge.user_id == user.id))).all())
    if user.is_verified_student:
        stored.add("verified_student")

    completed = select(func.count(Delivery.id)).join(Product, Product.id == Delivery.product_id).where(
        Product.seller_id == user.id, Delivery.status == "delivered"
    )
    count = await db.scalar(completed) or 0
    if count >= 20:
        stored.add("sales_20")
    if count > 50:
        stored.add("power_seller")

    rating_row = (await db.execute(
        select(func.avg(Review.rating), func.count(Review.id)).where(Review.vendor_id == user.id)
    )).one()
    average, review_count = rating_row[0], rating_row[1] or 0
    if review_count >= 10 and average is not None and float(average) >= 4.8:
        stored.add("top_rated")

    open_disputes = await db.scalar(
        select(func.count(Delivery.id)).join(Product, Product.id == Delivery.product_id).where(
            Product.seller_id == user.id, Delivery.status == "disputed"
        )
    ) or 0
    if count >= 5 and review_count >= 5 and average is not None and float(average) >= 4.5 and open_disputes == 0:
        stored.add("trusted_vendor")

    responder_times = select(
        func.avg(func.extract("epoch", Delivery.seller_responded_at - Delivery.created_at) / 60)
    ).join(Product, Product.id == Delivery.product_id).where(
        Product.seller_id == user.id, Delivery.seller_responded_at.is_not(None)
    )
    avg_response = await db.scalar(responder_times)
    response_count = await db.scalar(
        select(func.count(Delivery.id)).join(Product, Product.id == Delivery.product_id).where(
            Product.seller_id == user.id, Delivery.seller_responded_at.is_not(None)
        )
    )
    if response_count and response_count >= 5 and avg_response is not None and float(avg_response) < 15:
        stored.add("fast_responder")
    return sorted(stored)


@router.get("")
async def discover_vendors(
    sort: str = "popular",
    db: AsyncSession = Depends(get_db),
):
    if sort not in {"popular", "trending"}:
        raise HTTPException(422, "Choose popular or trending.")
    review_average = select(func.avg(Review.rating)).where(Review.vendor_id == User.id).scalar_subquery()
    review_count = select(func.count(Review.id)).where(Review.vendor_id == User.id).scalar_subquery()
    active_count = select(func.count(Product.id)).where(
        Product.seller_id == User.id,
        Product.status == "active",
    ).scalar_subquery()
    recent_activity = select(func.count(Delivery.id)).join(
        Product, Product.id == Delivery.product_id
    ).where(
        Product.seller_id == User.id,
        Delivery.status == "delivered",
        Delivery.completed_at >= func.now() - func.make_interval(0, 0, 0, 30),
    ).scalar_subquery()
    popularity = select(func.count(Delivery.id)).join(
        Product, Product.id == Delivery.product_id
    ).where(Product.seller_id == User.id, Delivery.status == "delivered").scalar_subquery()
    score = recent_activity if sort == "trending" else popularity
    rows = (await db.execute(
        select(
            User,
            review_average.label("rating"),
            review_count.label("review_count"),
            active_count.label("active_listings"),
        )
        .where(
            User.is_banned.is_(False),
            User.is_email_verified.is_(True),
            User.store_activated_at.is_not(None),
            User.store_suspended_at.is_(None),
        )
        .order_by(
            score.desc(),
            review_count.desc(),
            active_count.desc(),
            User.created_at.desc(),
        )
        .limit(12)
    )).all()
    return [{
        "id": str(user.id),
        "name": user.store_name or user.full_name,
        "user_type": user.user_type,
        "bio": user.store_bio,
        "address": user.hostel_or_business_address,
        "logo_url": user.logo_url,
        "logo_position_x": user.logo_position_x,
        "logo_position_y": user.logo_position_y,
        "banner_url": user.banner_url,
        "banner_position_x": user.banner_position_x,
        "banner_position_y": user.banner_position_y,
        "rating": float(rating) if rating else None,
        "review_count": review_count,
        "active_listings": active_listings,
    } for user, rating, review_count, active_listings in rows]


@router.get("/{vendor_id}")
async def storefront(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    u = await db.get(User, vendor_id)
    if not u or u.is_banned or not u.is_email_verified or u.store_suspended_at is not None:
        raise HTTPException(404, "Store not found.")
    avg, n = (await db.execute(select(func.avg(Review.rating), func.count(Review.id)).where(Review.vendor_id == u.id))).one()
    reviews = (await db.execute(
        select(Review, User)
        .join(User, User.id == Review.reviewer_id)
        .where(Review.vendor_id == u.id)
        .order_by(Review.id.desc())
        .limit(100)
    )).all()
    # Sales = completed orders (listings with stock stay active, so counting "sold" listings undercounts).
    sales = (await db.execute(select(func.count(Delivery.id)).join(Product, Product.id == Delivery.product_id).where(Product.seller_id == u.id, Delivery.status == "delivered"))).scalar_one()
    rows = (await db.execute(select(Product).where(Product.seller_id == u.id, Product.status == "active").order_by(Product.created_at.desc()))).scalars().all()
    imgs = await images_for(db, [p.id for p in rows])
    # Public fields only: never expose the vendor's email.
    return {"id": str(u.id), "name": u.store_name or u.full_name, "user_type": u.user_type, "bio": u.store_bio,
            "address": u.hostel_or_business_address, "phone_number": u.phone_number, "whatsapp_number": u.whatsapp_number,
            "badges": await public_badges(db, u), "logo_url": u.logo_url, "banner_url": u.banner_url,
            "logo_position_x": u.logo_position_x, "logo_position_y": u.logo_position_y,
            "banner_position_x": u.banner_position_x,
            "banner_position_y": u.banner_position_y,
            "sales": sales, "rating": float(avg) if avg is not None else None, "review_count": n,
            "reviews": [{"id": r.id, "reviewer_name": reviewer.full_name, "rating": r.rating, "comment": r.comment}
                        for r, reviewer in reviews],
            "products": [card(p, u, imgs.get(p.id, [])) for p in rows]}


@router.get("/admin/badges/users")
async def badge_users(
    q: str | None = None,
    _: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(User).where(User.is_banned.is_(False)).order_by(User.created_at.desc()).limit(100)
    if q and q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where((User.full_name.ilike(term)) | (User.email.ilike(term)) | (User.store_name.ilike(term)))
    users = (await db.scalars(stmt)).all()
    badges_by_user: dict[uuid.UUID, list[str]] = {user.id: [] for user in users}
    if users:
        for user_id, badge_key in (await db.execute(
            select(UserBadge.user_id, UserBadge.badge_key).where(
                UserBadge.user_id.in_(badges_by_user)
            )
        )).all():
            badges_by_user[user_id].append(badge_key)
    custom_badges = {
        key: key.removeprefix("custom:")
        for values in badges_by_user.values()
        for key in values
        if key.startswith("custom:")
    }
    result = []
    for user in users:
        result.append({
            "id": str(user.id), "name": user.store_name or user.full_name, "email": user.email,
            "badges": sorted(badges_by_user[user.id]), "verified_student": user.is_verified_student,
        })
    return {"available_badges": {**BADGE_LABELS, **custom_badges}, "users": result}


class BadgeUpdateIn(BaseModel):
    badges: list[str] = Field(max_length=6)


@router.put("/admin/badges/users/{user_id}")
async def update_badges(
    user_id: uuid.UUID,
    data: BadgeUpdateIn,
    admin: User = Depends(admin_user),
    db: AsyncSession = Depends(get_db),
):
    badges = [
        f"custom:{key.removeprefix('custom:').strip()}"
        if key.startswith("custom:") else key
        for key in data.badges
    ]
    valid = all(
        key in BADGE_LABELS
        or (
            key.startswith("custom:")
            and 1 <= len(key.removeprefix("custom:")) <= 33
        )
        for key in badges
    )
    if len(set(badges)) != len(badges) or not valid:
        raise HTTPException(422, "Choose valid badges without duplicates.")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found.")
    await db.execute(delete(UserBadge).where(UserBadge.user_id == user.id))
    db.add_all(
        UserBadge(user_id=user.id, badge_key=key, assigned_by=admin.id)
        for key in badges
    )
    await db.commit()
    return {"badges": badges}
