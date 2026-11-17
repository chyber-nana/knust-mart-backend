"""Track anonymized user accounts."""

from alembic import op
import sqlalchemy as sa

revision = "0021_anonymize_accounts"
down_revision = "0020_seller_contacts_and_badges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "anonymized_at")
