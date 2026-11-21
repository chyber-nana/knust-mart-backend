import os
import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from ..catalog import CAMPUS_PLACES, CATEGORIES, CONDITIONS, DELIVERY_FEE, FEES, LOCATIONS, OFF_CAMPUS, clean_location
from ..db import get_db
from ..billing import CURRENCY, PURPOSE_PROMOTION, new_reference, paystack_initialize, promotion_fee_pesewas
from ..models import ChatMessage, Delivery, Payment, Product, ProductImage, Review, User
from ..security import get_verified_user
from ..stock import sync_after_stock_edit
from .uploads import is_uploaded_image_url

router = APIRouter(prefix="/products", tags=["products"])

def clean_options(v: list[str] | None) -> list[str]:
    seen, out = set(), []
    for x in (v or []):
        x = x.strip()[:20]
        if x and x.lower() not in seen:
            seen.add(x.lower()); out.append(x)
    return out[:12]

class ProductIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    category: str
    condition: str | None = None
    hall_location: str | None = None
    image_urls: list[str] = Field(min_length=1, max_length=5)
    stock: int | None = Field(default=None, ge=0, le=100000)
    colors: list[str] = Field(default_factory=list, max_length=12)
    sizes: list[str] = Field(default_factory=list, max_length=12)

class ProductPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, gt=0, max_digits=10, decimal_places=2)
    category: str | None = None
    condition: str | None = None
    hall_location: str | None = None
    image_urls: list[str] | None = Field(default=None, min_length=1, max_length=5)
    stock: int | None = Field(default=None, ge=0, le=100000)
    colors: list[str] | None = Field(default=None, max_length=12)
    sizes: list[str] | None = Field(default=None, max_length=12)

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
            "hall_location": p.hall_location, "stock": p.stock, "image": imgs[0] if imgs else None, "is_promoted": bool(p.is_promoted),
            "seller": {
                "id": str(u.id), "name": u.store_name or u.full_name, "user_type": u.user_type,
                "pickup_location": u.hostel_or_business_address or p.hall_location,
            }}

@router.get("/meta")
async def meta():
    return {"categories": CATEGORIES, "conditions": CONDITIONS, "locations": LOCATIONS, "fees": {t: {**v, "promotion": promotion_fee_pesewas() / 100} for t, v in FEES.items()}, "delivery_fee": DELIVERY_FEE}

@router.get("")
async def browse(q: str | None = None, category: str | None = None, condition: str | None = None,
                 location: str | None = None, min_price: Decimal | None = None, max_price: Decimal | None = None,
                 sellers: str = "all", db: AsyncSession = Depends(get_db)):
    stmt = select(Product, User).join(User, User.id == Product.seller_id).where(
        Product.status == "active",
        User.is_banned.is_(False),
        User.store_suspended_at.is_(None),
    )
    if q: stmt = stmt.where(Product.title.ilike(f"%{q}%"))
    if category: stmt = stmt.where(Product.category == category)
    if condition: stmt = stmt.where(Product.condition == condition)
    if location == OFF_CAMPUS:
        stmt = stmt.where(Product.hall_location.is_not(None), Product.hall_location.not_in(CAMPUS_PLACES))
    elif location:
        stmt = stmt.where(Product.hall_location == location)
    if min_price is not None: stmt = stmt.where(Product.price >= min_price)
    if max_price is not None: stmt = stmt.where(Product.price <= max_price)
    if sellers == "student": stmt = stmt.where(User.user_type == "student")
    rows = (await db.execute(stmt.order_by(Product.is_promoted.desc(), Product.created_at.desc()).limit(60))).all()
    imgs = await images_for(db, [p.id for p, _ in rows])
    return [card(p, u, imgs.get(p.id, [])) for p, u in rows]

@router.get("/{product_id}")
async def detail(product_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(Product, User).join(User, User.id == Product.seller_id).where(Product.id == product_id))).first()
    if not row or row[1].is_banned or row[1].store_suspended_at is not None:
        raise HTTPException(404, "Listing not found.")
    p, u = row
    imgs = (await images_for(db, [p.id])).get(p.id, [])
    return {
        **card(p, u, imgs),
        "description": p.description,
        "images": imgs,
        "colors": p.colors or [],
        "sizes": p.sizes or [],
        "status": p.status,
        "seller": {
            "id": str(u.id), "name": u.store_name or u.full_name, "user_type": u.user_type,
            "pickup_location": u.hostel_or_business_address or p.hall_location,
            "phone_number": u.phone_number, "whatsapp_number": u.whatsapp_number,
        },
    }

@router.post("", status_code=201)
async def create(data: ProductIn, user: User = Depends(get_verified_user), db: AsyncSession = Depends(get_db)):
    if user.store_suspended_at is not None:
        raise HTTPException(403, "This business is currently suspended.")
    if user.user_type == "external" and user.store_activated_at is None:
        raise HTTPException(403, "Create your business to start selling.")
    if data.category not in CATEGORIES:
        raise HTTPException(422, "Choose a listed category.")
    is_service = data.category == "Services"
    location = clean_location(data.hall_location)
    if not is_service and (data.condition not in CONDITIONS or not location):
        raise HTTPException(422, "Items need a listed condition and a location.")
    if not is_service and (data.stock is None or data.stock < 1):
        raise HTTPException(422, "Enter how many units you have in stock.")
    if not all(is_uploaded_image_url(url) for url in data.image_urls):
        raise HTTPException(422, "Upload images through the app first.")
    
    p = Product(
        seller_id=user.id, 
        title=data.title.strip(), 
        description=data.description, 
        price=data.price,
        category=data.category,
        condition=None if is_service else data.condition,
        hall_location=None if is_service else location,
        stock=None if is_service else data.stock,
        colors=[] if is_service else clean_options(data.colors),
        sizes=[] if is_service else clean_options(data.sizes)
    )
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
    await db.refresh(product, with_for_update=True)
    old_stock = product.stock
    changes = data.model_dump(exclude_unset=True)
    for key in ("title", "price", "category"):
        if key in changes and changes[key] is None:
            raise HTTPException(422, f"{key} cannot be empty.")
    if "category" in changes and changes["category"] not in CATEGORIES:
        raise HTTPException(422, "Choose a listed category.")
    if "condition" in changes and changes["condition"] is not None and changes["condition"] not in CONDITIONS:
        raise HTTPException(422, "Choose a listed condition.")
    if "hall_location" in changes and changes["hall_location"] is not None:
        changes["hall_location"] = clean_location(changes["hall_location"])
    category = changes.get("category", product.category)
    if category not in CATEGORIES:
        raise HTTPException(422, "Choose a listed category.")
    if category == "Services":
        changes["condition"] = None
        changes["hall_location"] = None
        changes["stock"] = None
        changes["colors"] = []
        changes["sizes"] = []
    else:
        stock = changes["stock"] if "stock" in changes else product.stock
        if stock is None:
            raise HTTPException(422, "Items need a stock quantity.")
        changes["stock"] = stock
        condition = changes.get("condition", product.condition)
        location = changes.get("hall_location", product.hall_location)
        if condition not in CONDITIONS or not location:
            raise HTTPException(422, "Items need a listed condition and location.")
    for key in ("colors", "sizes"):
        if key in changes:
            changes[key] = clean_options(changes[key])
    image_urls = changes.pop("image_urls", None)
    if image_urls is not None:
        if not all(is_uploaded_image_url(url) for url in image_urls):
            raise HTTPException(422, "Upload images through the app first.")
        await db.execute(delete(ProductImage).where(ProductImage.product_id == product.id))
        db.add_all(ProductImage(product_id=product.id, image_url=url) for url in image_urls)
    for key, value in changes.items():
        setattr(product, key, value.strip() if key == "title" and value else value)
    if product.stock != old_stock:
        await sync_after_stock_edit(db, product)
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
    )).scalar_one_or_none()  # <-- Added .scalar_one_or_none() and closed the parenthesis properly
    
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
    if data.status == "active" and product.stock is not None and product.stock < 1:
        raise HTTPException(409, "Add stock before reopening this listing.")
    product.status = data.status
    await db.commit()
    return {"status": product.status}