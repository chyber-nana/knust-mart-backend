import uuid
from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, CHAR, ForeignKey, String, Text, Numeric, DateTime, Boolean, func, CheckConstraint, UniqueConstraint, Index, SmallInteger, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("user_type IN ('student', 'external')", name="users_user_type_check"),
        CheckConstraint(
            "store_activation_type IN ('free_launch', 'paid', 'legacy')",
            name="users_store_activation_type_check",
        ),
        CheckConstraint("banner_position_y BETWEEN 0 AND 100", name="users_banner_position_y_check"),
        CheckConstraint("logo_position_x BETWEEN 0 AND 100", name="users_logo_position_x_check"),
        CheckConstraint("logo_position_y BETWEEN 0 AND 100", name="users_logo_position_y_check"),
        CheckConstraint("banner_position_x BETWEEN 0 AND 100", name="users_banner_position_x_check"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_type: Mapped[str] = mapped_column(Text, default="external", nullable=False)
    is_verified_student: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    otp_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    otp_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_courier: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    is_banned: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    is_admin: Mapped[bool] = mapped_column(default=False, server_default="false", nullable=False)
    hostel_or_business_address: Mapped[str | None] = mapped_column(Text)
    store_name: Mapped[str | None] = mapped_column(Text)
    store_bio: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    logo_position_x: Mapped[int] = mapped_column(default=50, server_default="50", nullable=False)
    logo_position_y: Mapped[int] = mapped_column(default=50, server_default="50", nullable=False)
    banner_url: Mapped[str | None] = mapped_column(Text)
    banner_position_x: Mapped[int] = mapped_column(default=50, server_default="50", nullable=False)
    banner_position_y: Mapped[int] = mapped_column(default=50, server_default="50", nullable=False)
    store_activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    store_activation_type: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class EmailVerification(Base):
    __tablename__ = "email_verifications"
    __table_args__ = (
        UniqueConstraint("verification_token", name="email_verifications_token_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    otp_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    verification_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'sold', 'reserved')", name="products_status_check"),
        Index("products_status_category_location_idx", "status", "category", "hall_location"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    hall_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="active", nullable=False)
    is_promoted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ProductImage(Base):
    __tablename__ = "product_images"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)

class CampusAd(Base):
    __tablename__ = "campus_ads"
    __table_args__ = (
        CheckConstraint(
            "duration IN ('day', 'week', 'month')",
            name="campus_ads_duration_check",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    alt_text: Mapped[str] = mapped_column(String(160), nullable=False)
    link_url: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[str] = mapped_column(String(8), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_receiver_unread", "receiver_id", "read_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    receiver_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread_created", "user_id", "read_at", "created_at"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    notification_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Review(Base):
    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="reviews_rating_check"),
        UniqueConstraint("delivery_id", name="reviews_delivery_id_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deliveries.id"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_seller', 'pending', 'ready_for_pickup', 'accepted', 'in_transit', 'delivered', 'failed', 'disputed')",
            name="deliveries_status_check",
        ),
        CheckConstraint(
            "fulfillment_type IN ('courier', 'pickup')",
            name="deliveries_fulfillment_type_check",
        ),
        Index(
            "deliveries_status_pickup_location_dropoff_location_idx",
            "status",
            "pickup_location",
            "dropoff_location",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), nullable=False)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    courier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    fulfillment_type: Mapped[str] = mapped_column(Text, default="courier", server_default="'courier'", nullable=False)
    pickup_location: Mapped[str] = mapped_column(Text, nullable=False)
    dropoff_location: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    courier_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivery_qr_token: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    pin_code: Mapped[str] = mapped_column(CHAR(6), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="'pending'", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (
        UniqueConstraint("action", "subject", "window_start", name="auth_rate_limits_bucket_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(320), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)


class Appeal(Base):
    __tablename__ = "appeals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="appeals_status_check",
        ),
        Index(
            "appeals_one_pending_per_user_idx",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="'pending'", nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class CourierPayout(Base):
    __tablename__ = "courier_payouts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'failed')",
            name="courier_payouts_status_check",
        ),
        UniqueConstraint("delivery_id", name="courier_payouts_delivery_id_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    courier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    delivery_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deliveries.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="'pending'", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_pesewas > 0", name="payments_amount_check"),
        CheckConstraint("status IN ('pending', 'success', 'flagged', 'failed')", name="payments_status_check"),
        UniqueConstraint("reference", name="payments_reference_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    amount_pesewas: Mapped[int] = mapped_column(nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GHS", server_default="GHS", nullable=False)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending", nullable=False)
    provider_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordReset(Base):
    __tablename__ = "password_resets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
