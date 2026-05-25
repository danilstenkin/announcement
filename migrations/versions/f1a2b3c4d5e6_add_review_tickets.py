"""add review_tickets and review_history tables

Revision ID: f1a2b3c4d5e6
Revises: eb3c1ed522b8
Create Date: 2026-05-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'eb3c1ed522b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "cchub_announcements"


def upgrade() -> None:
    op.create_table(
        'review_tickets',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('email_id', UUID(as_uuid=True), sa.ForeignKey(f'{SCHEMA}.incoming_emails.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('status', sa.Enum('PENDING_REVIEW', 'IN_REVIEW', 'REVISION', 'APPROVED', 'REJECTED', name='ticketstatusenum', schema=SCHEMA), nullable=False),
        sa.Column('assignee_id', UUID(as_uuid=True), nullable=True),
        sa.Column('assignee_name', sa.String(255), nullable=True),
        sa.Column('title', sa.Text, nullable=True),
        sa.Column('body', sa.Text, nullable=True),
        sa.Column('script_ru', sa.Text, nullable=True),
        sa.Column('script_kz', sa.Text, nullable=True),
        sa.Column('ai_summary', sa.Text, nullable=True),
        sa.Column('source', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )

    op.create_table(
        'review_history',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('ticket_id', UUID(as_uuid=True), sa.ForeignKey(f'{SCHEMA}.review_tickets.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('action', sa.Enum('CREATED', 'ASSIGNED', 'TAKEN', 'SENT_TO_REVISION', 'EDITED', 'APPROVED', 'REJECTED', name='reviewactionenum', schema=SCHEMA), nullable=False),
        sa.Column('actor_id', UUID(as_uuid=True), nullable=True),
        sa.Column('actor_name', sa.String(255), nullable=True),
        sa.Column('comment', sa.Text, nullable=True),
        sa.Column('changes', JSON, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table('review_history', schema=SCHEMA)
    op.drop_table('review_tickets', schema=SCHEMA)
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.ticketstatusenum")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.reviewactionenum")
