import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.catalog import CATEGORIES, DELIVERY_FEE
from app.db import Session, engine
from app.models import CourierPayout, Delivery, Product, Review, User
from app.security import pwd

STORES = [
    {
        "name": "Campus Tech Hub",
        "owner": "Ama Mensah",
        "email": "campus-tech-hub@example.com",
        "type": "external",
        "address": "Commercial Area, near Main Library",
        "bio": "Student-friendly laptops, chargers, accessories, and quick tech support.",
        "products": [
            ("USB-C 65W Laptop Charger", "Compatible with most modern laptops. Tested and ready for pickup.", "Electronics", "Used - good", "Unity", Decimal("180.00")),
            ("Wireless Mouse", "Quiet clicks and a comfortable shape for long study sessions.", "Electronics", "New", "Republic", Decimal("55.00")),
        ],
    },
    {
        "name": "KNUST Textbooks",
        "owner": "Kojo Owusu",
        "email": "knust-textbooks@st.knust.edu.gh",
        "type": "student",
        "address": "Unity Hall",
        "bio": "Affordable course books passed on by KNUST students.",
        "products": [
            ("Engineering Mathematics 3 Textbook", "Second edition with clear notes in the margins. No missing pages.", "Textbooks", "Used - good", "Unity", Decimal("45.00")),
            ("Introductory Physics Lab Manual", "Complete lab manual, lightly used and ready for the next practical.", "Textbooks", "Like new", "Independence", Decimal("32.00")),
        ],
    },
    {
        "name": "Evandy Bites",
        "owner": "Esi Boateng",
        "email": "evandy-bites@example.com",
        "type": "external",
        "address": "Evandy Hostel",
        "bio": "Fresh snacks and useful late-study essentials around Evandy.",
        "products": [
            ("Late Study Snack Box", "A mix of locally made snacks for a long library session.", "Food & Snacks", "New", "Evandy", Decimal("28.00")),
            ("Reusable Water Bottle", "Lightweight bottle for lectures, the gym, and campus walks.", "Food & Snacks", "New", "Evandy", Decimal("35.00")),
        ],
    },
    {
        "name": "Unity Essentials",
        "owner": "Nana Adjei",
        "email": "unity-essentials@st.knust.edu.gh",
        "type": "student",
        "address": "Unity Hall, Block C",
        "bio": "Thoughtful room basics and practical finds for halls.",
        "products": [
            ("Adjustable Desk Lamp", "Three brightness levels and a flexible neck for your study desk.", "Room Essentials", "Used - good", "Unity", Decimal("85.00")),
            ("Compact Laundry Basket", "Foldable basket that fits neatly beside a hostel wardrobe.", "Room Essentials", "Like new", "Republic", Decimal("40.00")),
        ],
    },
    {
        "name": "Republic Threads",
        "owner": "Akua Asante",
        "email": "republic-threads@st.knust.edu.gh",
        "type": "student",
        "address": "Republic Hall",
        "bio": "Everyday campus style, sourced and resold by students.",
        "products": [
            ("KNUST Weekend Hoodie", "Forest-green fleece hoodie, size M, in very good condition.", "Fashion", "Used - good", "Republic", Decimal("95.00")),
            ("Canvas Lecture Tote", "Roomy canvas tote for notebooks, lunch, and a water bottle.", "Fashion", "New", "Independence", Decimal("38.00")),
        ],
    },
]


async def get_or_create_user(db, store: dict, password_hash: str) -> User:
    email = store["email"]
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(
            full_name=store["owner"],
            email=email,
            password_hash=password_hash,
            user_type=store["type"],
            is_email_verified=True,
            is_verified_student=store["type"] == "student",
            is_courier=False,
            is_banned=False,
            store_name=store["name"],
            store_bio=store["bio"],
            hostel_or_business_address=store["address"],
            banner_position_y=50,
        )
        db.add(user)
        await db.flush()
    return user


async def seed() -> None:
    password = os.environ.get("SEED_PASSWORD")
    if not password or len(password) < 12:
        raise RuntimeError("Set SEED_PASSWORD to a unique development password of at least 12 characters.")

    password_hash = pwd.hash(password)
    async with Session() as db:
        store_users = []
        for store in STORES:
            store_users.append(await get_or_create_user(db, store, password_hash))

        courier = (await db.execute(
            select(User).where(User.email == "campus-courier@st.knust.edu.gh")
        )).scalar_one_or_none()
        if courier is None:
            courier = User(
                full_name="Kofi Asare",
                email="campus-courier@st.knust.edu.gh",
                password_hash=password_hash,
                user_type="student",
                is_email_verified=True,
                is_verified_student=True,
                is_courier=True,
                is_banned=False,
                store_name=None,
                hostel_or_business_address="Independence Hall",
            )
            db.add(courier)
            await db.flush()

        products_by_store: list[list[Product]] = []
        for store, seller in zip(STORES, store_users):
            rows = []
            for title, description, category, condition, location, price in store["products"]:
                if category not in CATEGORIES:
                    raise ValueError(f"Unknown category configured for seeded listing: {category}")
                product = (await db.execute(
                    select(Product).where(Product.seller_id == seller.id, Product.title == title)
                )).scalar_one_or_none()
                if product is None:
                    product = Product(
                        seller_id=seller.id,
                        title=title,
                        description=description,
                        price=price,
                        category=category,
                        condition=condition,
                        hall_location=location,
                        status="active",
                    )
                    db.add(product)
                    await db.flush()
                rows.append(product)
            products_by_store.append(rows)

        for index, seller in enumerate(store_users):
            product = products_by_store[index][0]
            buyer = store_users[(index + 1) % len(store_users)]
            delivery = (await db.execute(
                select(Delivery).where(
                    Delivery.product_id == product.id,
                    Delivery.buyer_id == buyer.id,
                    Delivery.status == "delivered",
                )
            )).scalar_one_or_none()
            if delivery is None:
                delivery = Delivery(
                    product_id=product.id,
                    buyer_id=buyer.id,
                    courier_id=courier.id,
                    pickup_location=product.hall_location,
                    dropoff_location="Main Library",
                    delivery_fee=DELIVERY_FEE,
                    delivery_qr_token=f"seed-{product.id}",
                    pin_code="000000",
                    status="delivered",
                    completed_at=datetime.now(timezone.utc),
                )
                db.add(delivery)
                await db.flush()
            product.status = "sold"

            existing_review = (await db.execute(
                select(Review.id).where(Review.delivery_id == delivery.id)
            )).scalar_one_or_none()
            if existing_review is None:
                db.add(Review(
                    delivery_id=delivery.id,
                    reviewer_id=buyer.id,
                    vendor_id=seller.id,
                    rating=5 if index % 2 == 0 else 4,
                    comment="Clear communication and an easy campus handover.",
                ))
            payout = (await db.execute(
                select(CourierPayout.id).where(CourierPayout.delivery_id == delivery.id)
            )).scalar_one_or_none()
            if payout is None:
                db.add(CourierPayout(
                    courier_id=courier.id,
                    delivery_id=delivery.id,
                    amount=delivery.delivery_fee,
                    status="pending",
                ))
        await db.commit()
        print(f"Seeded or verified {len(STORES)} campus storefronts and {len(STORES) * 2} listings.")


async def main() -> None:
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
