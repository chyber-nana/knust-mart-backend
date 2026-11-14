"""Store activation (free launch slots / paid) and payments ledger."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_store_activation"
down_revision = "0016_expiring_campus_ads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("store_activated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("store_activation_type", sa.Text(), nullable=True))
    op.create_check_constraint(
        "users_store_activation_type_check",
        "users",
        "store_activation_type IN ('free_launch', 'paid', 'legacy')",
    )
    op.create_index("ix_users_store_activation_type", "users", ["store_activation_type"])
    # Existing stores keep working. They are marked 'legacy' so they never use up
    # the free launch slots.
    op.execute(
        """
        UPDATE users
        SET store_activated_at = COALESCE(created_at, now()),
            store_activation_type = 'legacy'
        WHERE (store_name IS NOT NULL AND btrim(store_name) <> '')
           OR (user_type = 'external'
               AND EXISTS (SELECT 1 FROM products p WHERE p.seller_id = users.id))
        """
    )
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("amount_pesewas", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="GHS"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("provider_payload", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("amount_pesewas > 0", name="payments_amount_check"),
        sa.CheckConstraint(
            "status IN ('pending', 'success', 'flagged', 'failed')",
            name="payments_status_check",
        ),
        sa.UniqueConstraint("reference", name="payments_reference_key"),
    )
    op.create_index("ix_payments_user_id", "payments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_user_id", table_name="payments")
    op.drop_table("payments")
    op.drop_index("ix_users_store_activation_type", table_name="users")
    op.drop_constraint("users_store_activation_type_check", "users", type_="check")
    op.drop_column("users", "store_activation_type")
    op.drop_column("users", "store_activated_at")
