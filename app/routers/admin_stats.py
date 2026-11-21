from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Delivery, Payment, Product, User
from ..security import admin_user

router = APIRouter(prefix="/admin/stats", tags=["admin-stats"])


async def _counts(db: AsyncSession, column, *where) -> dict[str, int]:
    stmt = select(column, func.count()).group_by(column)
    for clause in where:
        stmt = stmt.where(clause)
    return {str(k): n for k, n in (await db.execute(stmt)).all()}


@router.get("")
async def stats(_: User = Depends(admin_user), db: AsyncSession = Depends(get_db)):
    count = lambda *w: db.scalar(select(func.count()).select_from(User).where(*w))  # noqa: E731
    users = await count()
    product_status = await _counts(db, Product.status)
    services = await db.scalar(select(func.count()).select_from(Product).where(Product.category == "Services"))
    promoted = await db.scalar(select(func.count()).select_from(Product).where(Product.is_promoted.is_(True)))
    revenue = await db.execute(
        select(Payment.purpose, func.coalesce(func.sum(Payment.amount_pesewas), 0))
        .where(Payment.status == "success").group_by(Payment.purpose)
    )
    revenue_by_purpose = {p: int(v) / 100 for p, v in revenue.all()}
    total_products = sum(product_status.values())
    return {
        "users": {
            "total": users,
            "verified_students": await count(User.is_verified_student.is_(True)),
            "live_businesses": await count(User.store_activated_at.is_not(None), User.store_suspended_at.is_(None)),
            "suspended_or_banned": await count(User.is_banned.is_(True)),
        },
        "couriers": {
            "active": await count(User.is_courier.is_(True)),
            "registered": await count(User.courier_activated_at.is_not(None)),
            "free_launch": await count(User.courier_activation_type == "free_launch"),
            "paid": await count(User.courier_activation_type == "paid"),
        },
        "products": {
            "total": total_products,
            "by_status": product_status,
            "services": services or 0,
            "physical": total_products - (services or 0),
            "promoted": promoted or 0,
        },
        "deliveries": await _counts(db, Delivery.status),
        "revenue": {"total": sum(revenue_by_purpose.values()), "by_purpose": revenue_by_purpose},
    }
