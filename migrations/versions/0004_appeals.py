"""Add banned-user appeals and admin access."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_appeals"
down_revision = "0003_email_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "appeals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rejection_reason", sa.Text()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="appeals_user_id_fkey"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"], name="appeals_reviewed_by_fkey"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="appeals_status_check",
        ),
    )
    op.create_index("ix_appeals_user_id", "appeals", ["user_id"])
    op.create_index(
        "appeals_one_pending_per_user_idx",
        "appeals",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("appeals_one_pending_per_user_idx", table_name="appeals")
    op.drop_index("ix_appeals_user_id", table_name="appeals")
    op.drop_table("appeals")
    op.drop_column("users", "is_admin")
