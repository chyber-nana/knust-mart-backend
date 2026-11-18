import os
from dotenv import load_dotenv

# 1. Load environment variables first!
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from . import realtime  # noqa: F401  registers live notification/message publishing
from .routers import admin_users, ads, appeals, auth, deliveries, messages, notifications, payments, products, reviews, stores, uploads, vendors

# Add this right at the very bottom of your main.py file:
app = FastAPI(title="KNUST Mart API")

cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

@app.get("/")
async def root():
    return {"message": "Welcome to the KNUST Mart & Student Courier API", "status": "online"}

app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(appeals.router)
app.include_router(messages.router)
app.include_router(notifications.router)
app.include_router(deliveries.router)
app.include_router(products.router)
app.include_router(reviews.router)
app.include_router(vendors.router)
app.include_router(uploads.router)
app.include_router(ads.router)
app.include_router(stores.router)
app.include_router(payments.router)

@app.on_event("startup")
async def _ensure_schema() -> None:
    from .db import engine
    from .stock import ensure_stock_column
    await ensure_stock_column(engine)
    await _ensure_moderation_columns(engine)


async def _ensure_moderation_columns(engine) -> None:
    from sqlalchemy import text
    async with engine.begin() as conn:
        restriction = await conn.scalar(text(
            "select 1 from information_schema.columns "
            "where table_schema = current_schema() and table_name = 'users' and column_name = 'restriction'"))
        if not restriction:
            await conn.execute(text("alter table users add column restriction varchar(16)"))
            await conn.execute(text(
                "update users set restriction = 'suspended' where is_banned is true and restriction is null"))
            await conn.execute(text(
                "alter table users add constraint users_restriction_check "
                "check (restriction is null or restriction in ('suspended', 'banned'))"))
        suspended = await conn.scalar(text(
            "select 1 from information_schema.columns "
            "where table_schema = current_schema() and table_name = 'users' and column_name = 'store_suspended_at'"))
        if not suspended:
            await conn.execute(text("alter table users add column store_suspended_at timestamptz"))
