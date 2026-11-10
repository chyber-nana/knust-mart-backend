"""Record courier payouts for completed deliveries."""

from alembic import op
import sqlalchemy as sa

revision = "0007_courier_payouts"
down_revision = "0006_delivery_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "courier_payouts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("courier_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(8, 2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["courier_id"], ["users.id"], name="courier_payouts_courier_id_fkey"),
        sa.ForeignKeyConstraint(["delivery_id"], ["deliveries.id"], name="courier_payouts_delivery_id_fkey"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id", name="courier_payouts_delivery_id_key"),
        sa.CheckConstraint("status IN ('pending', 'paid', 'failed')", name="courier_payouts_status_check"),
    )
    op.create_index("ix_courier_payouts_courier_id", "courier_payouts", ["courier_id"])


def downgrade() -> None:
    op.drop_index("ix_courier_payouts_courier_id", table_name="courier_payouts")
    op.drop_table("courier_payouts")
