"""Account restriction and business suspension fields."""

from alembic import op
import sqlalchemy as sa

revision = "0022_admin_moderation"
down_revision = "0021_anonymize_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("restriction", sa.String(length=16), nullable=True))
    op.add_column("users", sa.Column("store_suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "users_restriction_check",
        "users",
        "restriction IS NULL OR restriction IN ('suspended', 'banned')",
    )
    op.execute("UPDATE users SET restriction = 'suspended' WHERE is_banned IS TRUE AND restriction IS NULL")


def downgrade() -> None:
    op.drop_constraint("users_restriction_check", "users", type_="check")
    op.drop_column("users", "store_suspended_at")
    op.drop_column("users", "restriction")
