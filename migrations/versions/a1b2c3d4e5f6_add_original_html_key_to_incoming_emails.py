"""add original_html_key to incoming_emails

Revision ID: a1b2c3d4e5f6
Revises: eb3c1ed522b8
Create Date: 2026-05-20 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'eb3c1ed522b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('incoming_emails', sa.Column('original_html_key', sa.String(length=500), nullable=True), schema='cchub_announcements')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('incoming_emails', 'original_html_key', schema='cchub_announcements')
