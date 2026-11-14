"""Record terms acceptance and allow pre-handoff order cancellation."""

from alembic import op
import sqlalchemy as sa

revision = "0019_terms_and_cancellations"
down_revision = "0018_password_resets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("terms_version", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("business_terms_accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("business_terms_version", sa.String(length=32), nullable=True))
    op.drop_constraint("deliveries_status_check", "deliveries", type_="check")
    op.create_check_constraint(
        "deliveries_status_check",
        "deliveries",
        "status IN ('awaiting_seller', 'pending', 'ready_for_pickup', 'accepted', "
        "'in_transit', 'delivered', 'failed', 'disputed', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE deliveries SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint("deliveries_status_check", "deliveries", type_="check")
    op.create_check_constraint(
        "deliveries_status_check",
        "deliveries",
        "status IN ('awaiting_seller', 'pending', 'ready_for_pickup', 'accepted', "
        "'in_transit', 'delivered', 'failed', 'disputed')",
    )
    op.drop_column("users", "business_terms_version")
    op.drop_column("users", "business_terms_accepted_at")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "terms_accepted_at")
