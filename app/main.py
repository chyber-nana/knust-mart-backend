import os
from dotenv import load_dotenv

# 1. Load environment variables first!
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import ads, appeals, auth, deliveries, messages, notifications, payments, products, reviews, stores, uploads, vendors

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