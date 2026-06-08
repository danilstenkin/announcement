"""add manager approval flow

Adds the ON_APPROVAL ticket status and the SENT_TO_APPROVAL history action used
by the "отправить на согласование → согласовать/вернуть" flow.

Revision ID: b1c2d3e4f5a6
Revises: aa11bb22cc33
Create Date: 2026-06-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "aa11bb22cc33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "cchub_announcements"


def upgrade() -> None:
    """Upgrade schema."""
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block, so use an
    # autocommit block (same pattern as aa11bb22cc33).
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE {SCHEMA}.ticketstatusenum ADD VALUE IF NOT EXISTS 'ON_APPROVAL'"
        )
        op.execute(
            f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'SENT_TO_APPROVAL'"
        )


def downgrade() -> None:
    """Downgrade schema.

    PostgreSQL cannot drop a single enum value, so this is intentionally a no-op
    (consistent with the other enum-extending migrations in this project).
    """
    pass
