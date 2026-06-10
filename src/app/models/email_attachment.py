from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import expression
import uuid
from models.announcement import get_astana_time
from models.base import Base


class EmailAttachment(Base):
    __tablename__ = "email_attachments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    email_id = Column(
        UUID(as_uuid=True),
        ForeignKey("incoming_emails.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    object_key = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    content_type = Column(String(100), nullable=True)
    # Inline images embedded in the email body (e.g. screenshots). Kept for GPT
    # vision analysis but hidden from user-facing responses and not transferred
    # to the published announcement.
    is_inline = Column(
        Boolean, nullable=False, server_default=expression.false(), default=False
    )
    uploaded_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)

    email = relationship("IncomingEmail")

    def __repr__(self):
        return(
            f"EmailAttachment(id={self.id}, filename={self.filename})"
        )
