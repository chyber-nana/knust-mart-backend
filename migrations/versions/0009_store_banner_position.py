"""Store vertical focal point for vendor cover banners."""

from alembic import op
import sqlalchemy as sa

revision = "0009_banner_position"
down_revision = "0008_product_category_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("banner_position_y", sa.Integer(), nullable=False, server_default="50"),
    )
    op.create_check_constraint(
        "users_banner_position_y_check",
        "users",
        "banner_position_y BETWEEN 0 AND 100",
    )


def downgrade() -> None:
    op.drop_constraint("users_banner_position_y_check", "users", type_="check")
    op.drop_column("users", "banner_position_y")
