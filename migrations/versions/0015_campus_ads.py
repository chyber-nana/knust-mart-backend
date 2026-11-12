"""Add admin-managed campus flyer ads."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_campus_ads"
down_revision = "0014_store_image_positions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "campus_ads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("image_url", sa.Text(), nullable=False),
        sa.Column("alt_text", sa.String(length=160), nullable=False),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("campus_ads")
