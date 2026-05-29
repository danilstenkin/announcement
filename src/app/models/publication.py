from enum import Enum
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID

from models.announcement import get_astana_time
from models.base import Base


class PublicationKindEnum(str, Enum):
    PRIMARY = "PRIMARY"
    REPEAT = "REPEAT"


class PublicationStatusEnum(str, Enum):
    SCHEDULED = "SCHEDULED"
    PUBLISHED = "PUBLISHED"
    CANCELED = "CANCELED"


class AnnouncementPublication(Base):
    __tablename__ = "announcement_publications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    announcement_id = Column(
        UUID(as_uuid=True),
        ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    kind = Column(SQLEnum(PublicationKindEnum, schema="cchub_announcements"), nullable=False)
    publish_at = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(
        SQLEnum(PublicationStatusEnum, schema="cchub_announcements"),
        default=PublicationStatusEnum.SCHEDULED, nullable=False,
    )
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)
