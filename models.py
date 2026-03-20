"""
Database models for the announcements system.
Defines User, Announcement, and AnnouncementReadStatus models.
"""
 
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
from enum import Enum
import uuid
import pytz

from app.db import Base
 
 
class AnnouncementCategoryEnum(str, Enum):
    """Announcement categories."""
    TECH_QUESTION = "TECH_QUESTION"
    CHANGE = "CHANGE"
    NEW = "NEW"
    REVOKED = "REVOKED"
 
def get_astana_time():
    return datetime.now(pytz.timezone('Asia/Almaty'))


class Announcement(Base):
    """Announcement model representing corporate announcements."""
    __tablename__ = "announcements"
    #__table_args__ = {"extend_existing": True}
   
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
    attachment_path = Column(String(500), nullable=True)  # MinIO object key, e.g. "announcements/{id}/{timestamp}_{filename}"
   
    # Status flags
    is_hidden = Column(Boolean, default=True)  # Not published until explicitly published
    is_revoked = Column(Boolean, default=False)
    revoked_link = Column(String(500), nullable=True)
   
    # Metadata
    created_by = Column(UUID(as_uuid=True), nullable=False)
    name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_astana_time, onupdate=get_astana_time, nullable=False)
   
    # Relationships
    read_statuses = relationship("AnnouncementReadStatus", back_populates="announcement", cascade="all, delete-orphan")
   
    def __repr__(self):
        return f"<Announcement(id={self.id}, title='{self.title}', category={self.category})>"
 
 
class AnnouncementReadStatus(Base):
    """Tracks which announcements have been read by which users."""
    __tablename__ = "announcement_read_status"
    __table_args__ = ( UniqueConstraint("user_id", "announcement_id", name="uq_user_announcement"),)
   
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True),  nullable=False, index=True)
    announcement_id = Column(UUID(as_uuid=True), ForeignKey("announcements.id", ondelete="CASCADE"), nullable=False)
    is_read = Column(Boolean, default=False)
   
    # Relationships
    announcement = relationship("Announcement", back_populates="read_statuses")
   
    def __repr__(self):
        return f"<AnnouncementReadStatus(user_id={self.user_id}, announcement_id={self.announcement_id}, is_read={self.is_read})>"
 