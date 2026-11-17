"""Stock rules. products.stock is the number of units still available to order.
NULL means "not tracked" (services). One order always takes exactly one unit."""
from sqlalchemy import func, select

from .models import Delivery, Product

ACTIVE_ORDER = ("awaiting_seller", "pending", "ready_for_pickup", "accepted", "in_transit")


async def active_orders(db, product_id, exclude=None) -> int:
    q = select(func.count(Delivery.id)).where(Delivery.product_id == product_id, Delivery.status.in_(ACTIVE_ORDER))
    if exclude is not None:
        q = q.where(Delivery.id != exclude)
    return await db.scalar(q) or 0


def take_unit(product: Product) -> None:
    """An order was placed."""
    if product.stock is None:
        product.status = "reserved"
        return
    product.stock -= 1
    if product.stock <= 0:
        product.status = "reserved"


def release_unit(product: Product) -> None:
    """An order was cancelled, rejected or failed: the unit goes back on the shelf."""
    if product.stock is not None:
        product.stock += 1
    if product.status == "reserved":
        product.status = "active"


async def complete_unit(db, product: Product, delivery_id) -> None:
    """An order was delivered. Mark sold only when nothing is left to sell."""
    if product.stock is None:
        product.status = "sold"
    elif product.stock <= 0 and not await active_orders(db, product.id, delivery_id):
        product.status = "sold"


async def sync_after_stock_edit(db, product: Product) -> None:
    """The seller changed the number by hand: keep the listing status consistent."""
    if product.stock is None:
        return
    if product.stock > 0:
        if product.status in ("sold", "reserved"):
            product.status = "active"
    elif product.status == "active":
        product.status = "reserved" if await active_orders(db, product.id) else "sold"


async def ensure_stock_column(engine) -> None:
    """Idempotent one-time migration, run at startup so the API never queries a missing column."""
    from sqlalchemy import text
    async with engine.begin() as conn:
        exists = await conn.scalar(text(
            "select 1 from information_schema.columns "
            "where table_schema = current_schema() and table_name = 'products' and column_name = 'stock'"))
        if exists:
            return
        await conn.execute(text("alter table products add column stock integer"))
        await conn.execute(text(
            "update products p set stock = case "
            "when p.category = 'Services' then null "
            "when p.status = 'active' then 1 "
            "when p.status = 'reserved' and not exists (select 1 from deliveries d where d.product_id = p.id "
            "and d.status in ('awaiting_seller','pending','ready_for_pickup','accepted','in_transit')) then 1 "
            "else 0 end"))
        await conn.execute(text(
            "alter table products add constraint products_stock_check check (stock is null or stock >= 0)"))
