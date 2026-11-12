import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import engine, get_db
from app.main import app
from app.models import (
    CampusAd,
    CourierPayout,
    Delivery,
    EmailVerification,
    Notification,
    Product,
    Review,
    User,
)
from app.routers.uploads import PUBLIC_BASE, is_uploaded_image_url
from app.security import make_token, pwd


@pytest.fixture
def email_calls(monkeypatch):
    sent = []
    for module_name in (
        "app.routers.auth",
        "app.routers.deliveries",
        "app.routers.appeals",
    ):
        monkeypatch.setattr(
            f"{module_name}.send_email",
            lambda to, subject, body, **kwargs: sent.append((to, subject, body, kwargs)),
        )
    return sent


@pytest_asyncio.fixture
async def web_client(email_calls):
    async with engine.connect() as connection:
        outer_transaction = await connection.begin()
        db = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async def override_db():
            yield db

        app.dependency_overrides[get_db] = override_db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                yield client, db, email_calls
        finally:
            app.dependency_overrides.pop(get_db, None)
            await db.close()
            await outer_transaction.rollback()
            await engine.dispose()


async def add_user(
    db: AsyncSession,
    *,
    user_type: str = "student",
    banned: bool = False,
    courier: bool = False,
    admin: bool = False,
) -> User:
    suffix = uuid.uuid4().hex
    domain = "st.knust.edu.gh" if user_type == "student" else "example.com"
    user = User(
        full_name=f"Test User {suffix[:8]}",
        email=f"test-{suffix}@{domain}",
        password_hash=pwd.hash("StrongPass#123"),
        user_type=user_type,
        is_verified_student=user_type == "student",
        is_email_verified=True,
        is_banned=banned,
        is_courier=courier,
        is_admin=admin,
    )
    db.add(user)
    await db.flush()
    return user


async def add_product(db: AsyncSession, seller: User, *, status: str = "active") -> Product:
    product = Product(
        seller_id=seller.id,
        title="Engineering Mathematics 3 Textbook",
        description="Second edition, good condition.",
        price=Decimal("45.00"),
        category="Textbooks",
        condition="Used - good",
        hall_location="Unity",
        status=status,
    )
    db.add(product)
    await db.flush()
    return product


async def add_delivery(
    db: AsyncSession,
    product: Product,
    buyer: User,
    *,
    courier: User | None = None,
    status: str = "pending",
) -> Delivery:
    delivery = Delivery(
        product_id=product.id,
        buyer_id=buyer.id,
        courier_id=courier.id if courier else None,
        pickup_location="Unity",
        dropoff_location="Main Library",
        delivery_fee=Decimal("5.00"),
        delivery_qr_token=uuid.uuid4().hex,
        pin_code="123456",
        status=status,
    )
    db.add(delivery)
    await db.flush()
    return delivery


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user.email)}"}


@pytest.mark.asyncio
async def test_registration_enforces_student_domains_and_sends_otp(web_client):
    client, _, email_calls = web_client
    bad = await client.post("/auth/register", json={
        "full_name": "Student Example",
        "email": "student@example.test",
        "password": "StrongPass#123",
        "confirm_password": "StrongPass#123",
        "account_type": "student",
    })
    assert bad.status_code == 422

    success = await client.post("/auth/register", json={
        "full_name": "Student Example",
        "email": f"student-{uuid.uuid4().hex}@st.knust.edu.gh",
        "password": "StrongPass#123",
        "confirm_password": "StrongPass#123",
        "account_type": "student",
    })
    assert success.status_code == 201
    assert len(email_calls) == 1
    assert "6-digit" not in email_calls[0][2]
    assert "expires in 10 minutes" in email_calls[0][2]


@pytest.mark.asyncio
async def test_student_otp_is_stored_and_verify_email_alias_activates_account(web_client):
    client, db, email_calls = web_client
    email = f"verify-{uuid.uuid4().hex}@st.knust.edu.gh"
    response = await client.post("/auth/register", json={
        "full_name": "Student Example",
        "email": email,
        "password": "StrongPass#123",
        "confirm_password": "StrongPass#123",
        "account_type": "student",
    })
    assert response.status_code == 201

    user = await db.scalar(select(User).where(User.email == email))
    assert user is not None
    code = email_calls[0][2].split("code is: ", 1)[1][:6]
    assert user.otp_code == code
    assert user.otp_expires_at is not None
    assert user.is_verified is False

    verified = await client.post(
        "/auth/verify-email",
        params={"email": email, "code": code},
    )
    assert verified.status_code == 200
    await db.refresh(user)
    assert user.is_verified is True
    assert user.is_email_verified is True
    assert user.is_verified_student is True
    assert user.otp_code is None
    assert user.otp_expires_at is None


@pytest.mark.asyncio
async def test_storefront_accepts_uploaded_public_image_url(web_client):
    client, db, _ = web_client
    seller = await add_user(db)
    logo_url = f"{PUBLIC_BASE}/{'a' * 32}.jpg"

    response = await client.patch(
        "/vendors/me",
        headers=auth(seller),
        json={
            "logo_url": logo_url,
            "logo_position_x": 68,
            "logo_position_y": 27,
            "banner_position_x": 34,
            "banner_position_y": 72,
        },
    )

    assert response.status_code == 200
    assert is_uploaded_image_url(logo_url)
    assert not is_uploaded_image_url("https://example.com/not-uploaded.jpg")
    await db.refresh(seller)
    assert seller.logo_url == logo_url
    assert seller.logo_position_x == 68
    assert seller.logo_position_y == 27
    assert seller.banner_position_x == 34
    assert seller.banner_position_y == 72

    storefront = await client.get(f"/vendors/{seller.id}")
    assert storefront.status_code == 200
    assert storefront.json()["logo_position_x"] == 68
    assert storefront.json()["banner_position_x"] == 34


@pytest.mark.asyncio
async def test_registration_rejects_weak_or_mismatched_passwords(web_client):
    client, _, _ = web_client
    registration = {
        "full_name": "Student Example",
        "email": f"password-{uuid.uuid4().hex}@st.knust.edu.gh",
        "account_type": "student",
    }

    weak = await client.post("/auth/register", json={
        **registration,
        "password": "weakpassword",
        "confirm_password": "weakpassword",
    })
    assert weak.status_code == 422

    mismatch = await client.post("/auth/register", json={
        **registration,
        "password": "StrongPass#123",
        "confirm_password": "StrongPass#124",
    })
    assert mismatch.status_code == 422


@pytest.mark.asyncio
async def test_campus_flyers_are_public_and_admin_managed(web_client, monkeypatch):
    client, db, _ = web_client
    admin = await add_user(db, admin=True)
    member = await add_user(db)
    image_url = f"{PUBLIC_BASE}/{'b' * 32}.png"

    async def fake_store_image(file):
        assert file.filename == "flyer.png"
        return image_url

    monkeypatch.setattr("app.routers.ads.store_image", fake_store_image)

    denied = await client.post(
        "/ads/admin",
        headers=auth(member),
        data={"alt_text": "Club event"},
        files={"file": ("flyer.png", b"image", "image/png")},
    )
    assert denied.status_code == 403

    created = await client.post(
        "/ads/admin",
        headers=auth(admin),
        data={
            "alt_text": "Club event",
            "link_url": "https://example.com/event",
            "duration": "week",
        },
        files={"file": ("flyer.png", b"image", "image/png")},
    )
    assert created.status_code == 201
    created_data = created.json()
    assert created_data["image_url"] == image_url
    assert created_data["alt_text"] == "Club event"
    assert created_data["duration"] == "week"
    expires_at = datetime.fromisoformat(created_data["expires_at"])
    created_at = datetime.fromisoformat(created_data["created_at"])
    assert timedelta(days=6, hours=23) < expires_at - created_at < timedelta(days=7, minutes=1)

    public = await client.get("/ads")
    assert public.status_code == 200
    assert created_data["id"] in [item["id"] for item in public.json()]

    admin_list = await client.get("/ads/admin", headers=auth(admin))
    assert admin_list.status_code == 200
    assert created_data["id"] in [item["id"] for item in admin_list.json()]
    assert (await client.get("/ads/admin", headers=auth(member))).status_code == 403

    deleted = await client.delete(f"/ads/admin/{created_data['id']}", headers=auth(admin))
    assert deleted.status_code == 204
    assert await db.get(CampusAd, uuid.UUID(created_data["id"])) is None
    remaining = await client.get("/ads")
    assert created_data["id"] not in [item["id"] for item in remaining.json()]


@pytest.mark.asyncio
async def test_expired_ads_are_not_returned_publicly(web_client):
    client, db, _ = web_client
    admin = await add_user(db, admin=True)
    db.add(CampusAd(
        image_url=f"{PUBLIC_BASE}/{'d' * 32}.jpg",
        alt_text="Expired event",
        duration="day",
        is_active=True,
        created_by=admin.id,
        created_at=datetime.now(timezone.utc) - timedelta(days=2),
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    ))
    await db.flush()

    response = await client.get("/ads")

    assert response.status_code == 200
    assert all(item["alt_text"] != "Expired event" for item in response.json())


@pytest.mark.asyncio
async def test_regular_upload_endpoint_remains_available(web_client, monkeypatch):
    client, db, _ = web_client
    member = await add_user(db)
    image_url = f"{PUBLIC_BASE}/{'c' * 32}.jpg"

    async def fake_store_image(file):
        return image_url

    monkeypatch.setattr("app.routers.uploads.store_image", fake_store_image)
    response = await client.post(
        "/uploads",
        headers=auth(member),
        files={"file": ("profile.jpg", b"image", "image/jpeg")},
    )

    assert response.status_code == 201
    assert response.json() == {"url": image_url}


@pytest.mark.asyncio
async def test_otp_expiry_and_five_attempt_limit(web_client):
    client, db, _ = web_client
    user = await add_user(db)
    expired = EmailVerification(
        email=user.email,
        otp_code="123456",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(expired)
    await db.flush()
    result = await client.post("/auth/verify", json={"email": user.email, "otp_code": "123456"})
    assert result.status_code == 400
    assert "expired" in result.json()["detail"].lower()

    email = f"otp-{uuid.uuid4().hex}@st.knust.edu.gh"
    attempt = EmailVerification(
        email=email,
        otp_code="123456",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    db.add(attempt)
    await db.flush()
    for _ in range(5):
        response = await client.post("/auth/verify", json={"email": email, "otp_code": "000000"})
        assert response.status_code == 400
    limited = await client.post("/auth/verify", json={"email": email, "otp_code": "123456"})
    assert limited.status_code == 429
    await db.refresh(attempt)
    assert attempt.attempts == 5


@pytest.mark.asyncio
async def test_external_registration_uses_email_link(web_client):
    client, db, email_calls = web_client
    email = f"vendor-{uuid.uuid4().hex}@gmail.com"
    response = await client.post("/auth/register", json={
        "full_name": "Campus Tech Hub",
        "email": email,
        "password": "StrongPass#123",
        "confirm_password": "StrongPass#123",
        "account_type": "external",
        "store_name": "Campus Tech Hub",
    })
    assert response.status_code == 201
    assert len(email_calls) == 1
    assert email_calls[0][0] == email
    assert "/verify?token=" in email_calls[0][2]
    assert "/verify?token=" in email_calls[0][3]["html_body"]
    link = next(part for part in email_calls[0][2].split() if "/verify?token=" in part)
    verification_token = link.split("token=", 1)[1]
    verified = await client.post("/auth/verify-link", json={"verification_token": verification_token})
    assert verified.status_code == 200
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {verified.json()['access_token']}"})).status_code == 200
    user = await db.scalar(select(User).where(User.email == email))
    notice = await db.scalar(select(Notification).where(
        Notification.user_id == user.id,
        Notification.notification_type == "account_verified",
    ))
    assert notice is not None
    assert notice.notification_type == "account_verified"


@pytest.mark.asyncio
async def test_auth_resend_uses_json_body_and_is_rate_limited(web_client):
    client, _, _ = web_client
    email = f"missing-{uuid.uuid4().hex}@example.com"
    first = await client.post("/auth/resend", json={"email": email})
    assert first.status_code == 200
    assert "detail" in first.json()
    for _ in range(2):
        response = await client.post("/auth/resend", json={"email": email})
        assert response.status_code == 200
    limited = await client.post("/auth/resend", json={"email": email})
    assert limited.status_code == 429


@pytest.mark.asyncio
async def test_login_rate_limit_and_verified_account_guard(web_client):
    client, db, _ = web_client
    email = f"unknown-{uuid.uuid4().hex}@example.com"
    for _ in range(10):
        response = await client.post("/auth/login", json={"email": email, "password": "WrongPassword"})
        assert response.status_code == 401
    limited = await client.post("/auth/login", json={"email": email, "password": "WrongPassword"})
    assert limited.status_code == 429

    unverified = await add_user(db)
    unverified.is_verified_student = False
    await db.flush()
    guard = await client.get("/auth/me", headers=auth(unverified))
    assert guard.status_code == 403


@pytest.mark.asyncio
async def test_user_can_change_password_with_current_password(web_client):
    client, db, _ = web_client
    user = await add_user(db)
    wrong = await client.post("/auth/change-password", headers=auth(user), json={
        "current_password": "not-the-current-password",
        "new_password": "AnotherStrong#456",
    })
    assert wrong.status_code == 400

    changed = await client.post("/auth/change-password", headers=auth(user), json={
        "current_password": "StrongPass#123",
        "new_password": "AnotherStrong#456",
    })
    assert changed.status_code == 200
    old_login = await client.post("/auth/login", json={"email": user.email, "password": "StrongPass#123"})
    new_login = await client.post("/auth/login", json={"email": user.email, "password": "AnotherStrong#456"})
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    await db.refresh(user)
    assert pwd.verify("AnotherStrong#456", user.password_hash)


@pytest.mark.asyncio
async def test_delivery_fee_is_server_set_and_product_is_reserved(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    other_buyer = await add_user(db)
    product = await add_product(db, seller)

    response = await client.post("/deliveries", headers=auth(buyer), json={
        "product_id": str(product.id),
        "pickup_location": "Unity",
        "dropoff_location": "Main Library",
        "delivery_fee": 0.01,
    })
    assert response.status_code == 201
    assert Decimal(response.json()["delivery_fee"]) == Decimal("5.00")
    await db.refresh(product)
    assert product.status == "reserved"

    second = await client.post("/deliveries", headers=auth(other_buyer), json={
        "product_id": str(product.id),
        "pickup_location": "Unity",
        "dropoff_location": "Main Library",
    })
    assert second.status_code == 409

    own_product = await add_product(db, buyer)
    own = await client.post("/deliveries", headers=auth(buyer), json={
        "product_id": str(own_product.id),
        "pickup_location": "Unity",
        "dropoff_location": "Main Library",
    })
    assert own.status_code == 409


@pytest.mark.asyncio
async def test_seller_confirms_courier_order_and_enforces_cooldown(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    courier = await add_user(db, courier=True)
    product = await add_product(db, seller)

    created = await client.post("/deliveries", headers=auth(buyer), json={
        "product_id": str(product.id),
        "pickup_location": "Unity",
        "dropoff_location": "Main Library",
        "fulfillment_type": "courier",
    })
    assert created.status_code == 201
    delivery_id = created.json()["id"]
    assert created.json()["status"] == "awaiting_seller"
    reopen = await client.patch(
        f"/products/{product.id}/status",
        headers=auth(seller),
        json={"status": "active"},
    )
    assert reopen.status_code == 409
    courier_notices = (await client.get("/notifications", headers=auth(courier))).json()["items"]
    assert any(item["type"] == "delivery_requested" for item in courier_notices)

    confirmed = await client.post(
        f"/deliveries/{delivery_id}/seller-response",
        headers=auth(seller),
        json={"available": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "pending"
    available_at = datetime.fromisoformat(confirmed.json()["courier_available_at"])
    assert available_at > datetime.now(timezone.utc)

    board = await client.get("/deliveries/board", headers=auth(courier))
    assert board.status_code == 200
    assert any(job["id"] == delivery_id for job in board.json())
    blocked = await client.post(f"/deliveries/{delivery_id}/accept", headers=auth(courier))
    assert blocked.status_code == 409
    assert "10-second" in blocked.json()["detail"]

    delivery = await db.get(Delivery, uuid.UUID(delivery_id))
    delivery.courier_available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.flush()
    accepted = await client.post(f"/deliveries/{delivery_id}/accept", headers=auth(courier))
    assert accepted.status_code == 200


@pytest.mark.asyncio
async def test_pickup_order_has_zero_fee_and_buyer_confirms_handover(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    product = await add_product(db, seller)

    created = await client.post("/deliveries", headers=auth(buyer), json={
        "product_id": str(product.id),
        "pickup_location": "Unity",
        "dropoff_location": "Student Union Building",
        "fulfillment_type": "pickup",
    })
    assert created.status_code == 201
    assert Decimal(created.json()["delivery_fee"]) == Decimal("0.00")
    delivery_id = created.json()["id"]

    seller_orders = await client.get("/deliveries/mine", headers=auth(seller))
    assert any(row["id"] == delivery_id and row["role"] == "seller" for row in seller_orders.json())
    confirmed = await client.post(
        f"/deliveries/{delivery_id}/seller-response",
        headers=auth(seller),
        json={"available": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "ready_for_pickup"

    buyer_receipt = await client.get(f"/deliveries/{delivery_id}/receipt", headers=auth(buyer))
    seller_receipt = await client.get(f"/deliveries/{delivery_id}/receipt", headers=auth(seller))
    assert buyer_receipt.json()["qr_token"] is None
    assert seller_receipt.json()["role"] == "seller"
    assert seller_receipt.json()["qr_token"] is None

    completed = await client.post(f"/deliveries/{delivery_id}/confirm-pickup", headers=auth(buyer))
    assert completed.status_code == 200
    await db.refresh(product)
    assert completed.json()["status"] == "delivered"
    assert product.status == "sold"


@pytest.mark.asyncio
async def test_seller_can_mark_item_unavailable_and_order_is_released(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    product = await add_product(db, seller)

    created = await client.post("/deliveries", headers=auth(buyer), json={
        "product_id": str(product.id),
        "pickup_location": "Unity",
        "dropoff_location": "Main Library",
        "fulfillment_type": "pickup",
    })
    rejected = await client.post(
        f"/deliveries/{created.json()['id']}/seller-response",
        headers=auth(seller),
        json={"available": False},
    )
    assert rejected.status_code == 200
    await db.refresh(product)
    assert product.status == "active"
    buyer_notices = (await client.get("/notifications", headers=auth(buyer))).json()["items"]
    assert any(item["type"] == "order_unavailable" for item in buyer_notices)


@pytest.mark.asyncio
async def test_delivery_scan_sells_product_and_records_payout(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    courier = await add_user(db, courier=True)
    product = await add_product(db, seller, status="reserved")
    delivery = await add_delivery(db, product, buyer, courier=courier, status="in_transit")

    response = await client.post(
        f"/deliveries/{delivery.id}/scan",
        headers=auth(courier),
        json={"qr_token": delivery.delivery_qr_token},
    )
    assert response.status_code == 200
    await db.refresh(product)
    await db.refresh(delivery)
    payout = (await db.execute(
        select(CourierPayout).where(CourierPayout.delivery_id == delivery.id)
    )).scalar_one()
    assert delivery.status == "delivered"
    assert product.status == "sold"
    assert payout.amount == Decimal("5.00")
    assert payout.status == "pending"
    buyer_notices = (await client.get("/notifications", headers=auth(buyer))).json()["items"]
    seller_notices = (await client.get("/notifications", headers=auth(seller))).json()["items"]
    courier_notices = (await client.get("/notifications", headers=auth(courier))).json()["items"]
    assert any(item["type"] == "order_delivered" and item["title"] == "Order received" for item in buyer_notices)
    assert any(item["type"] == "order_delivered" and item["title"] == "Order complete" for item in seller_notices)
    assert any(item["type"] == "order_delivered" and item["title"] == "Delivery complete" for item in courier_notices)


@pytest.mark.asyncio
async def test_failed_or_disputed_delivery_restores_reservation_and_bans_courier(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    courier = await add_user(db, courier=True)
    product = await add_product(db, seller, status="reserved")
    delivery = await add_delivery(db, product, buyer, courier=courier, status="accepted")

    failed = await client.post(f"/deliveries/{delivery.id}/fail", headers=auth(courier))
    assert failed.status_code == 200
    await db.refresh(product)
    await db.refresh(courier)
    assert product.status == "active"
    assert courier.is_banned is True
    assert courier.is_courier is False
    assert (await client.get("/auth/me", headers=auth(courier))).status_code == 403

    disputed = await client.post(f"/deliveries/{delivery.id}/report-stolen", headers=auth(buyer))
    assert disputed.status_code == 200
    await db.refresh(product)
    assert product.status == "reserved"


@pytest.mark.asyncio
async def test_banned_login_gets_appeal_only_token(web_client):
    client, db, _ = web_client
    user = await add_user(db, banned=True)
    login = await client.post("/auth/login", json={"email": user.email, "password": "StrongPass#123"})
    assert login.status_code == 200
    assert login.json()["scope"] == "appeal"
    token_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/appeals", headers=token_headers)).status_code == 200
    assert (await client.get("/auth/me", headers=token_headers)).status_code == 401
    assert (await client.get("/deliveries/mine", headers=token_headers)).status_code == 401


@pytest.mark.asyncio
async def test_appeal_approval_reinstates_user_but_not_courier(web_client):
    client, db, _ = web_client
    banned = await add_user(db, banned=True, courier=False)
    banned.is_courier = True
    await db.flush()
    appeal_token = make_token({"sub": banned.email, "scope": "appeal"})
    created = await client.post(
        "/appeals",
        headers={"Authorization": f"Bearer {appeal_token}"},
        json={"message": "My suspension was a mistake; please review the delivery record."},
    )
    assert created.status_code == 201

    admin = await add_user(db, admin=True)
    approved = await client.post(
        f"/admin/appeals/{created.json()['id']}/review",
        headers=auth(admin),
        json={"status": "approved"},
    )
    assert approved.status_code == 200
    await db.refresh(banned)
    assert banned.is_banned is False
    assert banned.is_courier is False


@pytest.mark.asyncio
async def test_active_job_limit_is_three(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    courier = await add_user(db, courier=True)
    product = await add_product(db, seller, status="reserved")
    for _ in range(3):
        await add_delivery(db, product, buyer, courier=courier, status="accepted")
    pending = await add_delivery(db, product, buyer)

    response = await client.post(f"/deliveries/{pending.id}/accept", headers=auth(courier))
    assert response.status_code == 409
    assert "3 active" in response.json()["detail"]


@pytest.mark.asyncio
async def test_review_requires_delivered_buyer_order_and_is_unique(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    buyer = await add_user(db)
    other_buyer = await add_user(db)
    product = await add_product(db, seller, status="sold")
    seller_id = seller.id
    delivery = await add_delivery(db, product, buyer, status="delivered")
    body = {"delivery_id": str(delivery.id), "rating": 5, "comment": "Quick and friendly."}
    other_buyer_headers = auth(other_buyer)

    pending_receipt = await client.get(
        f"/deliveries/{delivery.id}/receipt",
        headers=auth(buyer),
    )
    assert pending_receipt.status_code == 200
    assert pending_receipt.json()["reviewed"] is False
    assert pending_receipt.json()["vendor_name"] == seller.store_name or pending_receipt.json()["vendor_name"] == seller.full_name

    first = await client.post("/reviews", headers=auth(buyer), json=body)
    assert first.status_code == 201
    reviewed_receipt = await client.get(
        f"/deliveries/{delivery.id}/receipt",
        headers=auth(buyer),
    )
    assert reviewed_receipt.json()["reviewed"] is True
    buyer_orders = await client.get("/deliveries/mine", headers=auth(buyer))
    reviewed_order = next(row for row in buyer_orders.json() if row["id"] == str(delivery.id))
    assert reviewed_order["reviewed"] is True
    duplicate = await client.post("/reviews", headers=auth(buyer), json=body)
    assert duplicate.status_code == 409
    wrong_buyer = await client.post("/reviews", headers=other_buyer_headers, json=body)
    assert wrong_buyer.status_code == 404
    stored = (await db.execute(select(Review).where(Review.delivery_id == delivery.id))).scalar_one()
    assert stored.vendor_id == seller_id


@pytest.mark.asyncio
async def test_promotion_uses_vendor_fee_without_changing_product_price(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    product = await add_product(db, seller)

    response = await client.post(f"/products/{product.id}/promote", headers=auth(seller))
    assert response.status_code == 200
    assert response.json()["fee"] == 30
    await db.refresh(product)
    assert product.price == Decimal("45.00")
    assert product.is_promoted is True


@pytest.mark.asyncio
async def test_seller_can_update_status_and_delete_unreferenced_listing(web_client):
    client, db, _ = web_client
    seller = await add_user(db)
    product = await add_product(db, seller)
    product_id = product.id

    changed = await client.patch(
        f"/products/{product_id}/status",
        headers=auth(seller),
        json={"status": "reserved"},
    )
    assert changed.status_code == 200
    assert changed.json()["status"] == "reserved"
    invalid = await client.patch(
        f"/products/{product_id}/status",
        headers=auth(seller),
        json={"status": "hidden"},
    )
    assert invalid.status_code == 422
    deleted = await client.delete(f"/products/{product_id}", headers=auth(seller))
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_public_listing_responses_do_not_expose_email(web_client):
    client, db, _ = web_client
    seller = await add_user(db, user_type="external")
    product = await add_product(db, seller)
    listing = await client.get("/products")
    detail = await client.get(f"/products/{product.id}")
    store = await client.get(f"/vendors/{seller.id}")

    assert listing.status_code == detail.status_code == store.status_code == 200
    assert seller.email not in repr(listing.json())
    assert seller.email not in repr(detail.json())
    assert seller.email not in repr(store.json())


@pytest.mark.asyncio
async def test_message_threads_return_latest_message_and_unread_count(web_client):
    client, db, _ = web_client
    buyer = await add_user(db)
    seller = await add_user(db, user_type="external")
    product = await add_product(db, seller)

    sent = await client.post(
        "/messages",
        headers=auth(buyer),
        json={
            "product_id": str(product.id),
            "receiver_id": str(seller.id),
            "message": "Is this textbook still available?",
        },
    )
    assert sent.status_code == 201
    replied = await client.post(
        "/messages",
        headers=auth(seller),
        json={
            "product_id": str(product.id),
            "receiver_id": str(buyer.id),
            "message": "Yes, it is available.",
        },
    )
    assert replied.status_code == 201

    inbox = await client.get("/messages/threads", headers=auth(buyer))
    assert inbox.status_code == 200
    assert inbox.json() == [{
        "product_id": str(product.id),
        "product_title": product.title,
        "counterpart_id": str(seller.id),
        "counterpart_name": seller.store_name or seller.full_name,
        "last_message": "Yes, it is available.",
        "sent_at": replied.json()["sent_at"],
        "unread_count": 1,
    }]
    unread = await client.get("/messages/unread-count", headers=auth(buyer))
    assert unread.status_code == 200
    assert unread.json() == {"count": 1}
    inbox_notices = await client.get("/notifications", headers=auth(buyer))
    assert inbox_notices.status_code == 200
    assert inbox_notices.json()["unread_count"] == 1
    notice = inbox_notices.json()["items"][0]
    assert notice["type"] == "message_received"
    assert notice["link"] == f"/chat/{product.id}/{seller.id}"

    read = await client.patch(f"/notifications/{notice['id']}/read", headers=auth(buyer))
    assert read.status_code == 200
    assert (await client.get("/notifications", headers=auth(buyer))).json()["unread_count"] == 0


@pytest.mark.asyncio
async def test_seller_can_start_chat_for_existing_order(web_client):
    client, db, _ = web_client
    buyer = await add_user(db)
    seller = await add_user(db, user_type="external")
    product = await add_product(db, seller)
    await add_delivery(db, product, buyer)

    conversation = await client.get(
        f"/messages/{product.id}/{buyer.id}",
        headers=auth(seller),
    )
    assert conversation.status_code == 200
    assert conversation.json()["messages"] == []

    sent = await client.post(
        "/messages",
        headers=auth(seller),
        json={
            "product_id": str(product.id),
            "receiver_id": str(buyer.id),
            "message": "I have your item ready.",
        },
    )
    assert sent.status_code == 201
    assert sent.json()["message"] == "I have your item ready."


@pytest.mark.asyncio
async def test_service_listing_does_not_require_item_condition_or_location(web_client):
    client, db, _ = web_client
    seller = await add_user(db)
    response = await client.post(
        "/products",
        headers=auth(seller),
        json={
            "title": "Hair styling",
            "description": "Braids and styling",
            "price": "80.00",
            "category": "Services",
            "image_urls": ["/media/service.webp"],
        },
    )
    assert response.status_code == 201
    product = await db.get(Product, uuid.UUID(response.json()["id"]))
    assert product is not None
    assert product.condition is None
    assert product.hall_location is None

    invalid_item = await client.post(
        "/products",
        headers=auth(seller),
        json={
            "title": "Used textbook",
            "price": "40.00",
            "category": "Textbooks",
            "image_urls": ["/media/book.webp"],
        },
    )
    assert invalid_item.status_code == 422


@pytest.mark.asyncio
async def test_item_cannot_be_changed_to_service_without_required_item_fields(web_client):
    client, db, _ = web_client
    seller = await add_user(db)
    product = await add_product(db, seller)
    service = await client.patch(
        f"/products/{product.id}",
        headers=auth(seller),
        json={"category": "Services"},
    )
    assert service.status_code == 200
    await db.refresh(product)
    assert product.condition is None
    assert product.hall_location is None

    invalid_item = await client.patch(
        f"/products/{product.id}",
        headers=auth(seller),
        json={"category": "Textbooks"},
    )
    assert invalid_item.status_code == 422
