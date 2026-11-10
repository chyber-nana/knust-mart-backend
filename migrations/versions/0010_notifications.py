"""Add persistent in-app notifications."""

from alembic import op
import sqlalchemy as sa

revision = "0010_notifications"
down_revision = "0009_banner_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("notification_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="notifications_user_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_user_unread_created",
        "notifications",
        ["user_id", "read_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_user_unread_created", table_name="notifications")
    op.drop_table("notifications")
