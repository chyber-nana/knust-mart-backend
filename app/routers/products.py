import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..catalog import CATEGORIES, CONDITIONS, DELIVERY_FEE, FEES, LOCATIONS
from ..db import get_db
from ..models import ChatMessage, Delivery, Product, ProductImage, Review, User
from ..security import get_verified_user
from .uploads import is_uploaded_image_url

router = APIRouter(prefix="/products", tags=["products"])

class ProductIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    category: str
    condition: str | None = None
    hall_location: str | None = None
    image_urls: list[str] = Field(min_length=1, max_length=5)

class ProductPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    category: str | None = None
    condition: str | None = None
    hall_location: str | None = None
    image_urls: list[str] | None = Field(default=None, min_length=1, max_length=5)

class ProductStatusIn(BaseModel):
    status: str = Field(pattern="^(active|reserved|sold)$")

async def images_for(db: AsyncSession, ids: list) -> dict:
    out: dict = {}
    if ids:
        q = select(ProductImage.product_id, ProductImage.image_url).where(ProductImage.product_id.in_(ids)).order_by(ProductImage.id)
        for pid, url in (await db.execute(q)).all():
            out.setdefault(pid, []).append(url)
    return out

def card(p: Product, u: User, imgs: list[str]) -> dict:
    return {"id": str(p.id), "title": p.title, "price": float(p.price), "category": p.category, "condition": p.condition,
            "hall_location": p.hall_location, "image": imgs[0] if imgs else None, "is_promoted": bool(p.is_promoted),
            "seller": {"id": str(u.id), "name": u.store_name or u.full_name, "user_type": u.user_type}}

@router.get("/meta")
async def meta():
    return {"categories": CATEGORIES, "conditions": CONDITIONS, "locations": LOCATIONS, "fees": FEES, "delivery_fee": DELIVERY_FEE}

@router.get("")
async def browse(q: str | None = None, category: str | None = None, condition: str | None = None,
                 location: str | None = None, min_price: Decimal | None = None, max_price: Decimal | None = None,
                 sellers: str = "all", db: AsyncSession = Depends(get_db)):
    stmt = select(Product, User).join(User, User.id == Product.seller_id).where(Product.status == "active", User.is_banned.is_(False))
    if q: stmt = stmt.where(Product.title.ilike(f"%{q}%"))
    if category: stmt = stmt.where(Product.category == category)
    if condition: stmt = stmt.where(Product.condition == condition)
    if location: stmt = stmt.where(Product.hall_location == location)
    if min_price is not None: stmt = stmt.where(Product.price >= min_price)
    if max_price is not None: stmt = stmt.where(Product.price <= max_price)
    if sellers == "student": stmt = stmt.where(User.user_type == "student")
    rows = (await db.execute(stmt.order_by(Product.is_promoted.desc(), Product.created_at.desc()).limit(60))).all()
    imgs = await images_for(db, [p.id for p, _ in rows])
    return [card(p, u, imgs.get(p.id, [])) for p, u in rows]

@router.get("/{product_id}")
async def detail(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Product, User).join(User, User.id == Product.seller_id).where(Product.id == product_id))).first()
    if not row or row[1].is_banned:
        raise HTTPException(404, "Listing not found.")
    p, u = row
    imgs = (await images_for(db, [p.id])).get(p.id, [])
    return {**card(p, u, imgs), "description": p.description, "images": imgs, "status": p.status}

@router.post("", status_code=201)
async def create(data: ProductIn, user: User = Depends(get_verified_user), db: AsyncSession = Depends(get_db)):
    if user.user_type == "external" and user.store_activated_at is None:
        raise HTTPException(403, "Create your business to start selling.")
    if data.category not in CATEGORIES:
        raise HTTPException(422, "Choose a listed category.")
    is_service = data.category == "Services"
    if not is_service and (data.condition not in CONDITIONS or data.hall_location not in LOCATIONS):
        raise HTTPException(422, "Items need a listed condition and location.")
    if not all(is_uploaded_image_url(url) for url in data.image_urls):
        raise HTTPException(422, "Upload images through the app first.")
    p = Product(seller_id=user.id, title=data.title.strip(), description=data.description, price=data.price,
                category=data.category,
                condition=None if is_service else data.condition,
                hall_location=None if is_service else data.hall_location)
    db.add(p)
    await db.flush()
    db.add_all(ProductImage(product_id=p.id, image_url=u) for u in data.image_urls)
    await db.commit()
    return {"id": str(p.id)}


async def owned_product(product_id: uuid.UUID, user: User, db: AsyncSession) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Listing not found.")
    if product.seller_id != user.id:
        raise HTTPException(403, "You can only manage your own listings.")
    return product


@router.patch("/{product_id}")
async def edit_listing(
    product_id: uuid.UUID,
    data: ProductPatch,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    product = await owned_product(product_id, user, db)
    changes = data.model_dump(exclude_unset=True)
    for key in ("title", "price", "category"):
        if key in changes and changes[key] is None:
            raise HTTPException(422, f"{key} cannot be empty.")
    if "category" in changes and changes["category"] not in CATEGORIES:
        raise HTTPException(422, "Choose a listed category.")
    if "condition" in changes and changes["condition"] is not None and changes["condition"] not in CONDITIONS:
        raise HTTPException(422, "Choose a listed condition.")
    if "hall_location" in changes and changes["hall_location"] is not None and changes["hall_location"] not in LOCATIONS:
        raise HTTPException(422, "Choose a listed location.")
    category = changes.get("category", product.category)
    if category not in CATEGORIES:
        raise HTTPException(422, "Choose a listed category.")
    if category == "Services":
        changes["condition"] = None
        changes["hall_location"] = None
    else:
        condition = changes.get("condition", product.condition)
        location = changes.get("hall_location", product.hall_location)
        if condition not in CONDITIONS or location not in LOCATIONS:
            raise HTTPException(422, "Items need a listed condition and location.")
    image_urls = changes.pop("image_urls", None)
    if image_urls is not None:
        if not all(is_uploaded_image_url(url) for url in image_urls):
            raise HTTPException(422, "Upload images through the app first.")
        await db.execute(delete(ProductImage).where(ProductImage.product_id == product.id))
        db.add_all(ProductImage(product_id=product.id, image_url=url) for url in image_urls)
    for key, value in changes.items():
        setattr(product, key, value.strip() if key == "title" and value else value)
    await db.commit()
    return {"id": str(product.id)}


@router.patch("/{product_id}/status")
async def set_listing_status(
    product_id: uuid.UUID,
    data: ProductStatusIn,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    product = (await db.execute(
        select(Product).where(Product.id == product_id).with_for_update()
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(404, "Listing not found.")
    if product.seller_id != user.id:
        raise HTTPException(403, "You can only manage your own listings.")
    active_order = await db.scalar(
        select(Delivery.id)
        .where(
            Delivery.product_id == product.id,
            Delivery.status.in_(("awaiting_seller", "pending", "ready_for_pickup", "accepted", "in_transit")),
        )
        .limit(1)
    )
    if active_order and data.status != "reserved":
        raise HTTPException(409, "This listing has an active order and cannot be reopened or marked sold.")
    product.status = data.status
    await db.commit()
    return {"status": product.status}


@router.delete("/{product_id}", status_code=204)
async def delete_listing(
    product_id: uuid.UUID,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    product = await owned_product(product_id, user, db)
    activity = await db.scalar(
        select(
            select(Delivery.id).where(Delivery.product_id == product.id).exists()
            | select(ChatMessage.id).where(ChatMessage.product_id == product.id).exists()
            | select(Review.id).join(Delivery, Review.delivery_id == Delivery.id).where(Delivery.product_id == product.id).exists()
        )
    )
    if activity:
        raise HTTPException(409, "Listings with delivery, chat, or review history cannot be deleted.")
    await db.delete(product)
    await db.commit()
    return None


@router.post("/{product_id}/promote")
async def promote_listing(
    product_id: uuid.UUID,
    user: User = Depends(get_verified_user),
    db: AsyncSession = Depends(get_db),
):
    product = await owned_product(product_id, user, db)
    if product.status == "sold":
        raise HTTPException(409, "Sold listings cannot be promoted.")
    fee = FEES[user.user_type]["promotion"]
    product.is_promoted = True
    await db.commit()
    return {
        "is_promoted": product.is_promoted,
        "fee": fee,
        "currency": "GHS",
        "payment_status": "stubbed",
        "detail": "Promotion fee is displayed for transparency; payment is not connected.",
    }
