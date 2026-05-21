import email
from sqlalchemy.orm import relationship
from sqlalchemy import Column, Text, ForeignKey, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID

from models.announcement import get_astana_time
import uuid


from models.base import Base


class AIEmail(Base):
    __tablename__ = "ai_emails"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_id = Column(
        UUID(as_uuid=True),
        ForeignKey("incoming_emails.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ai_title = Column(Text, nullable=True)
    ai_email = Column(Text, nullable=True)
    script_ru = Column(Text, nullable=True)
    script_kz = Column(Text, nullable=True)
    ai_summary = Column(Text, nullable=True)
    in_knowledge_base = Column(Boolean, nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)
