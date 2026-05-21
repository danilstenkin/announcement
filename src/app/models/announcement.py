from email.policy import default
from sqlalchemy.orm import relationship
from sqlalchemy import Column, Nullable, String, Text, Boolean, DateTime, Enum as SQLEnum, column
from sqlalchemy.dialects.postgresql import UUID

from datetime import datetime
from enum import Enum
import uuid
import pytz

from models.base import Base


class AnnouncementCategoryEnum(str, Enum):
    """Announcement categories."""
    TECH_QUESTION = "TECH_QUESTION"
    CHANGE = "CHANGE"
    NEW = "NEW"
    REVOKED = "REVOKED"



def get_astana_time():
    return datetime.now(pytz.timezone("Asia/Almaty"))


class Announcement(Base):
    """Announcement model representing corporate announcements."""
    __tablename__ = "announcements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    title = Column(String(255), nullable=False)
    category = Column(SQLEnum(AnnouncementCategoryEnum), nullable=False)
    product = Column(String(255), nullable=True)
    text = Column(Text, nullable=False)
    instruction = Column(Text, nullable=True)
    topic = Column(String(255), nullable=True)
    resource_link = Column(String(500), nullable=True)
    script_kz = Column(Text, nullable=True)
    script_ru = Column(Text, nullable=True)
    attachment_path = Column(String(500), nullable=True)

    is_hidden = Column(Boolean, default=True)
    is_revoked = Column(Boolean, default=False)
    revoked_link = Column(String(500), nullable=True)

    is_ai = Column(Boolean, default=False)
    source = Column(String(50), nullable=True)

    created_by = Column(UUID(as_uuid=True), nullable=False)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_astana_time, onupdate=get_astana_time, nullable=False)

    attachments = relationship(
        "Attachments",
        back_populates="announcement",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    read_statuses = relationship(
        "AnnouncementReadStatus",
        back_populates="announcement",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Announcement(id={self.id}, title='{self.title}', category={self.category})>"
