"""Use the listing category string as the sole product category field."""

from alembic import op
import sqlalchemy as sa

revision = "0008_product_category_text"
down_revision = "0007_courier_payouts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("products_status_category_id_hall_location_idx", table_name="products")
    op.drop_constraint("products_category_id_fkey", "products", type_="foreignkey")
    op.drop_column("products", "category_id")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column("category_id", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        INSERT INTO categories (name, slug)
        SELECT DISTINCT p.category, lower(regexp_replace(p.category, '[^a-zA-Z0-9]+', '-', 'g'))
        FROM products AS p
        WHERE NOT EXISTS (SELECT 1 FROM categories AS c WHERE c.name = p.category)
        """
    )
    op.execute(
        """
        UPDATE products AS p
        SET category_id = c.id
        FROM categories AS c
        WHERE c.name = p.category
        """
    )
    op.alter_column("products", "category_id", nullable=False, existing_type=sa.Integer())
    op.create_foreign_key(
        "products_category_id_fkey",
        "products",
        "categories",
        ["category_id"],
        ["id"],
    )
    op.create_index(
        "products_status_category_id_hall_location_idx",
        "products",
        ["status", "category_id", "hall_location"],
    )
