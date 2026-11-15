"""Add seller contact details and managed badges."""

from alembic import op
import sqlalchemy as sa

revision = "0020_seller_contacts_and_badges"
down_revision = "0019_terms_and_cancellations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(length=32), nullable=True))
    op.add_column("users", sa.Column("whatsapp_number", sa.String(length=32), nullable=True))
    op.add_column("deliveries", sa.Column("seller_responded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "user_badges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("badge_key", sa.String(length=40), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "badge_key", name="user_badges_user_badge_key"),
    )
    op.create_index("ix_user_badges_user_id", "user_badges", ["user_id"])
    op.execute(
        "INSERT INTO user_badges (user_id, badge_key) "
        "SELECT id, 'pioneer' FROM ("
        "SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS position FROM users"
        ") AS ranked WHERE position <= 10"
    )


def downgrade() -> None:
    op.drop_index("ix_user_badges_user_id", table_name="user_badges")
    op.drop_table("user_badges")
    op.drop_column("deliveries", "seller_responded_at")
    op.drop_column("users", "whatsapp_number")
    op.drop_column("users", "phone_number")
