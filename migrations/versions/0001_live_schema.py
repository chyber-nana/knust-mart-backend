"""Baseline for the original marketplace database."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_live_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("user_type", sa.Text(), nullable=False),
        sa.Column("is_verified_student", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_courier", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hostel_or_business_address", sa.Text()),
        sa.Column("store_name", sa.Text()),
        sa.Column("store_bio", sa.Text()),
        sa.Column("logo_url", sa.Text()),
        sa.Column("banner_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.false()),
        sa.UniqueConstraint("email", name="users_email_key"),
        sa.CheckConstraint("user_type IN ('student', 'external')", name="users_user_type_check"),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.UniqueConstraint("slug", name="categories_slug_key"),
    )
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("condition", sa.Text(), nullable=False),
        sa.Column("hall_location", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("is_promoted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["vendor_id"], ["users.id"], name="products_vendor_id_fkey"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name="products_category_id_fkey"),
        sa.CheckConstraint(
            "status IN ('active', 'sold', 'reserved')",
            name="products_status_check",
        ),
    )
    op.create_index(
        "products_status_category_id_hall_location_idx",
        "products",
        ["status", "category_id", "hall_location"],
    )
    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True)),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE", name="product_images_product_id_fkey"),
    )
    op.create_table(
        "email_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("otp_code", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0")),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True)),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True)),
        sa.Column("receiver_id", postgresql.UUID(as_uuid=True)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="chat_messages_product_id_fkey"),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], name="chat_messages_sender_id_fkey"),
        sa.ForeignKeyConstraint(["receiver_id"], ["users.id"], name="chat_messages_receiver_id_fkey"),
    )
    op.create_table(
        "deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("courier_id", postgresql.UUID(as_uuid=True)),
        sa.Column("pickup_location", sa.Text(), nullable=False),
        sa.Column("dropoff_location", sa.Text(), nullable=False),
        sa.Column("delivery_fee", sa.Numeric(8, 2), nullable=False),
        sa.Column("delivery_qr_token", sa.Text(), nullable=False),
        sa.Column("pin_code", sa.CHAR(6), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="deliveries_product_id_fkey"),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"], name="deliveries_buyer_id_fkey"),
        sa.ForeignKeyConstraint(["courier_id"], ["users.id"], name="deliveries_courier_id_fkey"),
        sa.UniqueConstraint("delivery_qr_token", name="deliveries_delivery_qr_token_key"),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'in_transit', 'delivered', 'failed', 'disputed')",
            name="deliveries_status_check",
        ),
    )
    op.create_index(
        "deliveries_status_pickup_location_dropoff_location_idx",
        "deliveries",
        ["status", "pickup_location", "dropoff_location"],
    )
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True)),
        sa.Column("vendor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"], name="reviews_reviewer_id_fkey"),
        sa.ForeignKeyConstraint(["vendor_id"], ["users.id"], name="reviews_vendor_id_fkey"),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="reviews_rating_check"),
    )


def downgrade() -> None:
    op.drop_table("reviews")
    op.drop_index("deliveries_status_pickup_location_dropoff_location_idx", table_name="deliveries")
    op.drop_table("deliveries")
    op.drop_table("chat_messages")
    op.drop_table("email_verifications")
    op.drop_table("product_images")
    op.drop_index("products_status_category_id_hall_location_idx", table_name="products")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_table("users")
