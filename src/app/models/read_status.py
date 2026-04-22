from sqlalchemy.orm import relationship
from sqlalchemy import Column, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

import uuid

from models.base import Base


class AnnouncementReadStatus(Base):
    """Tracks which announcements have been read by which users."""
    __tablename__ = "announcement_read_status"
    __table_args__ = (
        UniqueConstraint("user_id", "announcement_id", name="uq_user_announcement"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    announcement_id = Column(
        UUID(as_uuid=True),
        ForeignKey("announcements.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_read = Column(Boolean, default=False)

    announcement = relationship("Announcement", back_populates="read_statuses")

    def __repr__(self):
        return (
            f"<AnnouncementReadStatus(user_id={self.user_id}, "
            f"announcement_id={self.announcement_id}, is_read={self.is_read})>"
        )
