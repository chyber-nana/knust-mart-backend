"""Support link-based email verification for external vendors."""

from alembic import op
import sqlalchemy as sa

revision = "0003_email_links"
down_revision = "0002_align_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "email_verifications",
        "otp_code",
        existing_type=sa.String(length=10),
        nullable=True,
    )
    op.add_column(
        "email_verifications",
        sa.Column("verification_token", sa.String(length=128), nullable=True),
    )
    op.create_unique_constraint(
        "email_verifications_token_key",
        "email_verifications",
        ["verification_token"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "email_verifications_token_key",
        "email_verifications",
        type_="unique",
    )
    op.drop_column("email_verifications", "verification_token")
    op.alter_column(
        "email_verifications",
        "otp_code",
        existing_type=sa.String(length=10),
        nullable=False,
    )
