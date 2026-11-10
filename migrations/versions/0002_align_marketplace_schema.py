"""Align marketplace model columns while preserving existing records."""

from alembic import op
import sqlalchemy as sa

revision = "0002_align_schema"
down_revision = "0001_live_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("category", sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE products AS p
        SET category = c.name
        FROM categories AS c
        WHERE c.id = p.category_id
        """
    )
    op.alter_column("products", "category", nullable=False, existing_type=sa.Text())
    op.alter_column("products", "vendor_id", new_column_name="seller_id")
    op.execute(
        "ALTER TABLE products RENAME CONSTRAINT products_vendor_id_fkey TO products_seller_id_fkey"
    )
    op.create_index("ix_products_seller_id", "products", ["seller_id"])
    op.create_index(
        "products_status_category_location_idx",
        "products",
        ["status", "category", "hall_location"],
    )

    op.alter_column(
        "email_verifications",
        "attempts",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )
    op.create_index("ix_email_verifications_email", "email_verifications", ["email"])

    op.alter_column("product_images", "product_id", nullable=False, existing_type=sa.Uuid())
    op.create_index("ix_product_images_product_id", "product_images", ["product_id"])
    op.alter_column("reviews", "reviewer_id", nullable=False, existing_type=sa.Uuid())
    op.alter_column("reviews", "vendor_id", nullable=False, existing_type=sa.Uuid())
    op.create_index("ix_reviews_vendor_id", "reviews", ["vendor_id"])
    op.alter_column("chat_messages", "product_id", nullable=False, existing_type=sa.Uuid())
    op.alter_column("chat_messages", "sender_id", nullable=False, existing_type=sa.Uuid())
    op.alter_column("chat_messages", "receiver_id", nullable=False, existing_type=sa.Uuid())

    if not sa.inspect(op.get_bind()).has_table("auth_rate_limits"):
        op.create_table(
            "auth_rate_limits",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("subject", sa.String(length=320), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "action",
                "subject",
                "window_start",
                name="auth_rate_limits_bucket_key",
            ),
        )


def downgrade() -> None:
    op.drop_table("auth_rate_limits")
    op.alter_column("chat_messages", "receiver_id", nullable=True, existing_type=sa.Uuid())
    op.alter_column("chat_messages", "sender_id", nullable=True, existing_type=sa.Uuid())
    op.alter_column("chat_messages", "product_id", nullable=True, existing_type=sa.Uuid())
    op.drop_index("ix_reviews_vendor_id", table_name="reviews")
    op.alter_column("reviews", "vendor_id", nullable=True, existing_type=sa.Uuid())
    op.alter_column("reviews", "reviewer_id", nullable=True, existing_type=sa.Uuid())
    op.drop_index("ix_product_images_product_id", table_name="product_images")
    op.alter_column("product_images", "product_id", nullable=True, existing_type=sa.Uuid())
    op.drop_index("ix_email_verifications_email", table_name="email_verifications")
    op.alter_column(
        "email_verifications",
        "attempts",
        existing_type=sa.Integer(),
        nullable=True,
        server_default=sa.text("0"),
    )
    op.drop_index("products_status_category_location_idx", table_name="products")
    op.drop_index("ix_products_seller_id", table_name="products")
    op.alter_column("products", "seller_id", new_column_name="vendor_id")
    op.execute(
        "ALTER TABLE products RENAME CONSTRAINT products_seller_id_fkey TO products_vendor_id_fkey"
    )
    op.drop_column("products", "category")
