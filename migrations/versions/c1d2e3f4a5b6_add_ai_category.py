"""add ai_category to ai_emails, review_tickets, announcements

Revision ID: c1d2e3f4a5b6
Revises: f2a3b4c5d6e7
Create Date: 2026-07-01 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "cchub_announcements"

# ИИ-категория темы анонса (Изменения/Инциденты/Качество работы). Хранится как
# строка (см. AiCategoryEnum), nullable — сбой ИИ и старые строки не падают.
_TABLES = ("ai_emails", "review_tickets", "announcements")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("ai_category", sa.String(length=50), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    for table in _TABLES:
        op.drop_column(table, "ai_category", schema=SCHEMA)
