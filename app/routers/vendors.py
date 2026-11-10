import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import Delivery, Product, Review, User
from ..security import current_user
from .products import card, images_for

router = APIRouter(prefix="/vendors", tags=["vendors"])

class StoreIn(BaseModel):
    store_name: str | None = Field(default=None, max_length=80)
    store_bio: str | None = Field(default=None, max_length=500)
    hostel_or_business_address: str | None = Field(default=None, max_length=120)
    logo_url: str | None = None
    banner_url: str | None = None
    banner_position_y: int = Field(default=50, ge=0, le=100)

@router.patch("/me")
async def update_store(data: StoreIn, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    changes = data.model_dump(exclude_unset=True)
    for k in ("logo_url", "banner_url"):
        if changes.get(k) and not changes[k].startswith("/media/"):
            raise HTTPException(422, "Upload images through the app first.")
    for k, v in changes.items():
        setattr(user, k, v)
    await db.commit()
    return {"id": str(user.id)}


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
            User.store_name.is_not(None),
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
        "banner_url": user.banner_url,
        "rating": float(rating) if rating else None,
        "review_count": review_count,
        "active_listings": active_listings,
    } for user, rating, review_count, active_listings in rows]


@router.get("/{vendor_id}")
async def storefront(vendor_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    u = await db.get(User, vendor_id)
    if not u or u.is_banned or not u.is_email_verified:
        raise HTTPException(404, "Store not found.")
    avg, n = (await db.execute(select(func.avg(Review.rating), func.count(Review.id)).where(Review.vendor_id == u.id))).one()
    reviews = (await db.execute(
        select(Review, User)
        .join(User, User.id == Review.reviewer_id)
        .where(Review.vendor_id == u.id)
        .order_by(Review.id.desc())
        .limit(100)
    )).all()
    sales = (await db.execute(select(func.count(Product.id)).where(Product.seller_id == u.id, Product.status == "sold"))).scalar_one()
    rows = (await db.execute(select(Product).where(Product.seller_id == u.id, Product.status == "active").order_by(Product.created_at.desc()))).scalars().all()
    imgs = await images_for(db, [p.id for p in rows])
    # Public fields only: never expose the vendor's email.
    return {"id": str(u.id), "name": u.store_name or u.full_name, "user_type": u.user_type, "bio": u.store_bio,
            "address": u.hostel_or_business_address, "logo_url": u.logo_url, "banner_url": u.banner_url,
            "banner_position_y": u.banner_position_y,
            "sales": sales, "rating": float(avg) if avg is not None else None, "review_count": n,
            "reviews": [{"id": r.id, "reviewer_name": reviewer.full_name, "rating": r.rating, "comment": r.comment}
                        for r, reviewer in reviews],
            "products": [card(p, u, imgs.get(p.id, [])) for p in rows]}
