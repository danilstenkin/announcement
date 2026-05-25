"""merge review_tickets with add_column

Revision ID: 1df6cdada05a
Revises: cc041b291933, f1a2b3c4d5e6
Create Date: 2026-05-25 14:56:28.351585

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1df6cdada05a'
down_revision: Union[str, Sequence[str], None] = ('cc041b291933', 'f1a2b3c4d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
