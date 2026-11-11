"""Persist crop positions for storefront images."""

from alembic import op
import sqlalchemy as sa

revision = "0014_store_image_positions"
down_revision = "0bbbd5ec120d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("logo_position_x", sa.Integer(), server_default="50", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("logo_position_y", sa.Integer(), server_default="50", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("banner_position_x", sa.Integer(), server_default="50", nullable=False),
    )
    op.create_check_constraint(
        "users_logo_position_x_check", "users", "logo_position_x BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        "users_logo_position_y_check", "users", "logo_position_y BETWEEN 0 AND 100"
    )
    op.create_check_constraint(
        "users_banner_position_x_check", "users", "banner_position_x BETWEEN 0 AND 100"
    )


def downgrade() -> None:
    op.drop_constraint("users_banner_position_x_check", "users", type_="check")
    op.drop_constraint("users_logo_position_y_check", "users", type_="check")
    op.drop_constraint("users_logo_position_x_check", "users", type_="check")
    op.drop_column("users", "banner_position_x")
    op.drop_column("users", "logo_position_y")
    op.drop_column("users", "logo_position_x")
