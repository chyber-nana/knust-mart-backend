"""Add seller confirmation and pickup fulfillment states."""

from alembic import op
import sqlalchemy as sa

revision = "0011_order_fulfillment"
down_revision = "0010_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deliveries",
        sa.Column("fulfillment_type", sa.Text(), nullable=False, server_default="courier"),
    )
    op.add_column(
        "deliveries",
        sa.Column("courier_available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("deliveries_status_check", "deliveries", type_="check")
    op.create_check_constraint(
        "deliveries_status_check",
        "deliveries",
        "status IN ('awaiting_seller', 'pending', 'ready_for_pickup', 'accepted', 'in_transit', 'delivered', 'failed', 'disputed')",
    )
    op.create_check_constraint(
        "deliveries_fulfillment_type_check",
        "deliveries",
        "fulfillment_type IN ('courier', 'pickup')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE deliveries SET status = 'pending' "
        "WHERE status IN ('awaiting_seller', 'ready_for_pickup')"
    )
    op.drop_constraint("deliveries_fulfillment_type_check", "deliveries", type_="check")
    op.drop_constraint("deliveries_status_check", "deliveries", type_="check")
    op.create_check_constraint(
        "deliveries_status_check",
        "deliveries",
        "status IN ('pending', 'accepted', 'in_transit', 'delivered', 'failed', 'disputed')",
    )
    op.drop_column("deliveries", "courier_available_at")
    op.drop_column("deliveries", "fulfillment_type")
