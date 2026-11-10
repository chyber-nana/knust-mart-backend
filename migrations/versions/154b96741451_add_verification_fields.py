"""add verification fields

Revision ID: 154b96741451
Revises: 0012_service_listings
Create Date: 2026-11-10 19:02:25.853678

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '154b96741451'
down_revision: Union[str, Sequence[str], None] = '0012_service_listings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
