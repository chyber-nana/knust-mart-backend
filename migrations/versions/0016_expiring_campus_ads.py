"""Expire flyers and remove previously uploaded ads."""

from alembic import op
import sqlalchemy as sa

revision = "0016_expiring_campus_ads"
down_revision = "0015_campus_ads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM campus_ads")
    op.add_column(
        "campus_ads",
        sa.Column("duration", sa.String(length=8), nullable=False, server_default="day"),
    )
    op.create_check_constraint(
        "campus_ads_duration_check",
        "campus_ads",
        "duration IN ('day', 'week', 'month')",
    )
    op.add_column(
        "campus_ads",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.alter_column("campus_ads", "duration", server_default=None)
    op.alter_column("campus_ads", "expires_at", server_default=None)


def downgrade() -> None:
    op.drop_column("campus_ads", "expires_at")
    op.drop_constraint("campus_ads_duration_check", "campus_ads", type_="check")
    op.drop_column("campus_ads", "duration")
