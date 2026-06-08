"""add extended announcement fields to review_tickets

Phase 2 — category / product / instruction / topic / links / documents that the
business trainer edits on the ticket workspace.

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-06-08 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("review_tickets", sa.Column("category", sa.String(length=100), nullable=True))
    op.add_column("review_tickets", sa.Column("product", sa.String(length=255), nullable=True))
    op.add_column("review_tickets", sa.Column("instruction", sa.Text(), nullable=True))
    op.add_column("review_tickets", sa.Column("topic", sa.String(length=255), nullable=True))
    op.add_column("review_tickets", sa.Column("links", JSON(), nullable=True))
    op.add_column("review_tickets", sa.Column("documents", JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("review_tickets", "documents")
    op.drop_column("review_tickets", "links")
    op.drop_column("review_tickets", "topic")
    op.drop_column("review_tickets", "instruction")
    op.drop_column("review_tickets", "product")
    op.drop_column("review_tickets", "category")
