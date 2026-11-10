"""Track unread marketplace chat messages."""

from alembic import op
import sqlalchemy as sa

revision = "0005_message_reads"
down_revision = "0004_appeals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_chat_messages_receiver_unread",
        "chat_messages",
        ["receiver_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_receiver_unread", table_name="chat_messages")
    op.drop_column("chat_messages", "read_at")
