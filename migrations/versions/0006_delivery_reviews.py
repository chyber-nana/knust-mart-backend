"""Link reviews to a delivered order and enforce one review per order."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_delivery_reviews"
down_revision = "0005_message_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("delivery_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM reviews WHERE delivery_id IS NULL) THEN
                RAISE EXCEPTION 'Cannot link existing reviews to deliveries automatically';
            END IF;
        END
        $$
        """
    )
    op.alter_column("reviews", "delivery_id", nullable=False, existing_type=postgresql.UUID(as_uuid=True))
    op.create_foreign_key(
        "reviews_delivery_id_fkey",
        "reviews",
        "deliveries",
        ["delivery_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "reviews_delivery_id_key",
        "reviews",
        ["delivery_id"],
    )


def downgrade() -> None:
    op.drop_constraint("reviews_delivery_id_key", "reviews", type_="unique")
    op.drop_constraint("reviews_delivery_id_fkey", "reviews", type_="foreignkey")
    op.drop_column("reviews", "delivery_id")
