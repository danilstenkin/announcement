"""add ticket transfer (reassign with head approval)

Revision ID: e1f2a3b4c5d6
Revises: aa11bb22cc33
Create Date: 2026-06-10 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "aa11bb22cc33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "cchub_announcements"


def upgrade() -> None:
    """Upgrade schema."""
    # 1. New history action values. ALTER TYPE ... ADD VALUE can't run inside a
    # transaction block, so use an autocommit block.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'TRANSFER_REQUESTED'")
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'TRANSFER_APPROVED'")
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'TRANSFER_REJECTED'")

    # 2. Pending-transfer columns on review_tickets.
    op.add_column("review_tickets", sa.Column("pending_assignee_id", UUID(as_uuid=True), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("pending_assignee_name", sa.String(length=255), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("transfer_requested_by_id", UUID(as_uuid=True), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("transfer_requested_by_name", sa.String(length=255), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("transfer_requested_at", sa.DateTime(timezone=True), nullable=True), schema=SCHEMA)
    op.add_column("review_tickets", sa.Column("transfer_reason", sa.Text(), nullable=True), schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("review_tickets", "transfer_reason", schema=SCHEMA)
    op.drop_column("review_tickets", "transfer_requested_at", schema=SCHEMA)
    op.drop_column("review_tickets", "transfer_requested_by_name", schema=SCHEMA)
    op.drop_column("review_tickets", "transfer_requested_by_id", schema=SCHEMA)
    op.drop_column("review_tickets", "pending_assignee_name", schema=SCHEMA)
    op.drop_column("review_tickets", "pending_assignee_id", schema=SCHEMA)
    # PostgreSQL cannot drop individual enum values; leaving them is harmless
    # (consistent with the other enum-extending migrations in this project).
