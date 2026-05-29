"""add publication scheduling

Revision ID: aa11bb22cc33
Revises: 1df6cdada05a
Create Date: 2026-05-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ENUM as PGEnum


# revision identifiers, used by Alembic.
revision: str = "aa11bb22cc33"
down_revision: Union[str, Sequence[str], None] = "1df6cdada05a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "cchub_announcements"


def upgrade() -> None:
    """Upgrade schema."""
    # 1. Create the two NEW enum types in the managed schema.
    publication_kind = PGEnum(
        "PRIMARY", "REPEAT", name="publicationkindenum", schema=SCHEMA
    )
    publication_status = PGEnum(
        "SCHEDULED", "PUBLISHED", "CANCELED", name="publicationstatusenum", schema=SCHEMA
    )
    bind = op.get_bind()
    publication_kind.create(bind, checkfirst=True)
    publication_status.create(bind, checkfirst=True)

    # 2. Add new VALUES to EXISTING enum types. ALTER TYPE ... ADD VALUE cannot run
    # inside a transaction block, so use an autocommit block.
    with op.get_context().autocommit_block():
        op.execute(f"ALTER TYPE {SCHEMA}.ticketstatusenum ADD VALUE IF NOT EXISTS 'AGREED'")
        op.execute(f"ALTER TYPE {SCHEMA}.ticketstatusenum ADD VALUE IF NOT EXISTS 'PUBLISHED'")
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'SCHEDULED'")
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'RESCHEDULED'")
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'PUBLICATION_CANCELED'")
        op.execute(f"ALTER TYPE {SCHEMA}.reviewactionenum ADD VALUE IF NOT EXISTS 'PUBLISHED'")

    # 3. Add columns to review_tickets.
    op.add_column(
        "review_tickets",
        sa.Column("recommended_publish_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "review_tickets",
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "review_tickets",
        sa.Column(
            "publish_confirmed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        schema=SCHEMA,
    )
    # announcement_id is needed by a later task (Task 5) which links ticket->announcement.
    # Added here ahead of the model so the schema is complete.
    op.add_column(
        "review_tickets",
        sa.Column("announcement_id", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )

    # 4. Add published_at to announcements.
    op.add_column(
        "announcements",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # 5. Add recommended_publish_date to ai_emails.
    op.add_column(
        "ai_emails",
        sa.Column("recommended_publish_date", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )

    # 6. Create announcement_publications table. The enum columns reference the types
    # created in step 1 (create_type=False), so they are not re-created here.
    op.create_table(
        "announcement_publications",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("announcement_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            PGEnum(name="publicationkindenum", schema=SCHEMA, create_type=False),
            nullable=False,
        ),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            PGEnum(name="publicationstatusenum", schema=SCHEMA, create_type=False),
            nullable=False,
        ),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["announcement_id"],
            [f"{SCHEMA}.announcements.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_cchub_announcements_announcement_publications_id"),
        "announcement_publications",
        ["id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_cchub_announcements_announcement_publications_announcement_id"),
        "announcement_publications",
        ["announcement_id"],
        unique=False,
        schema=SCHEMA,
    )
    op.create_index(
        op.f("ix_cchub_announcements_announcement_publications_publish_at"),
        "announcement_publications",
        ["publish_at"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        op.f("ix_cchub_announcements_announcement_publications_publish_at"),
        table_name="announcement_publications",
        schema=SCHEMA,
    )
    op.drop_index(
        op.f("ix_cchub_announcements_announcement_publications_announcement_id"),
        table_name="announcement_publications",
        schema=SCHEMA,
    )
    op.drop_index(
        op.f("ix_cchub_announcements_announcement_publications_id"),
        table_name="announcement_publications",
        schema=SCHEMA,
    )
    op.drop_table("announcement_publications", schema=SCHEMA)

    op.drop_column("ai_emails", "recommended_publish_date", schema=SCHEMA)
    op.drop_column("announcements", "published_at", schema=SCHEMA)

    op.drop_column("review_tickets", "announcement_id", schema=SCHEMA)
    op.drop_column("review_tickets", "publish_confirmed", schema=SCHEMA)
    op.drop_column("review_tickets", "publish_at", schema=SCHEMA)
    op.drop_column("review_tickets", "recommended_publish_at", schema=SCHEMA)

    # Drop the two NEW enum types created in upgrade().
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.publicationstatusenum")
    op.execute(f"DROP TYPE IF EXISTS {SCHEMA}.publicationkindenum")

    # NOTE: Postgres cannot remove enum VALUES from an existing type. The values added
    # to ticketstatusenum ('AGREED', 'PUBLISHED') and reviewactionenum ('SCHEDULED',
    # 'RESCHEDULED', 'PUBLICATION_CANCELED', 'PUBLISHED') are intentionally NOT removed
    # here; they remain on the existing enum types after downgrade.
