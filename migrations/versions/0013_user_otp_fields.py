"""Store student verification codes on users."""

from alembic import op
import sqlalchemy as sa

revision = "0013_user_otp_fields"
down_revision = "154b96741451"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("otp_code", sa.String(length=6), nullable=True))
    op.add_column(
        "users",
        sa.Column("otp_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE users SET is_verified = is_email_verified")
    op.alter_column(
        "users",
        "is_verified",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )


def downgrade() -> None:
    op.alter_column(
        "users",
        "is_verified",
        existing_type=sa.Boolean(),
        nullable=True,
    )
    op.drop_column("users", "otp_expires_at")
    op.drop_column("users", "otp_code")
