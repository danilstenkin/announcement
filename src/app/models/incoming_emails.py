from enum import Enum
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
import uuid
from models.announcement import get_astana_time
from models.base import Base


class EmailStatusEnum(str, Enum):
    PROCESSING = "PROCESSING"
    GREEN = "GREEN"
    RED = "RED"
    YELLOW = "YELLOW"
    GRAY = "GRAY"
    DONE = "DONE"

class IncomingEmail(Base):
    __tablename__ = "incoming_emails"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    outlook_id = Column(String(500), unique=True, nullable=False)
    subject = Column(String(500), nullable=False)
    body = Column(Text, nullable=False)
    sender_email = Column(String(255), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        SQLEnum(EmailStatusEnum),
        default=EmailStatusEnum.PROCESSING,
        nullable=False
    )
    original_html_key = Column(String(500), nullable=True)
    processed_at = (Column(DateTime(timezone=True), nullable=True))
    created_at = (Column(DateTime(timezone=True), default=get_astana_time, nullable=True))

    def __repr__(self):
        return f"<IncomingEmail(id={self.id}, sender_email={self.sender_email}, status={self.status})>"
