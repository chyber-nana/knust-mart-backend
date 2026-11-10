import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Delivery, Product, Review, User
from ..security import current_user

router = APIRouter(prefix="/reviews", tags=["reviews"])


class ReviewIn(BaseModel):
    delivery_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


@router.post("", status_code=201)
async def create_review(
    data: ReviewIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    delivery = await db.get(Delivery, data.delivery_id)
    if delivery is None or delivery.buyer_id != user.id:
        raise HTTPException(404, "Delivered order not found.")
    if delivery.status != "delivered":
        raise HTTPException(409, "You can review only a delivered order.")
    product = await db.get(Product, delivery.product_id)
    if product is None or product.seller_id == user.id:
        raise HTTPException(409, "You cannot review this seller.")

    review = Review(
        delivery_id=delivery.id,
        reviewer_id=user.id,
        vendor_id=product.seller_id,
        rating=data.rating,
        comment=data.comment.strip() if data.comment else None,
    )
    db.add(review)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This delivery already has a review.")
    return {"id": review.id}


@router.get("/vendors/{vendor_id}")
async def vendor_reviews(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Review, User)
        .join(User, User.id == Review.reviewer_id)
        .where(Review.vendor_id == vendor_id)
        .order_by(Review.id.desc())
        .limit(100)
    )).all()
    return [
        {
            "id": review.id,
            "reviewer_name": reviewer.full_name,
            "rating": review.rating,
            "comment": review.comment,
        }
        for review, reviewer in rows
    ]
