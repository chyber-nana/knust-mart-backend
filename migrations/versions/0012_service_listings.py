"""Allow service listings without item condition or seller location."""

from alembic import op

revision = "0012_service_listings"
down_revision = "0011_order_fulfillment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("products", "condition", nullable=True)
    op.alter_column("products", "hall_location", nullable=True)


def downgrade() -> None:
    op.execute(
        "UPDATE products SET condition = 'Used - good' "
        "WHERE condition IS NULL"
    )
    op.execute(
        "UPDATE products SET hall_location = 'Main Library' "
        "WHERE hall_location IS NULL"
    )
    op.alter_column("products", "condition", nullable=False)
    op.alter_column("products", "hall_location", nullable=False)
