from enum import Enum
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
import uuid

from models.announcement import get_astana_time
from models.base import Base


class TicketStatusEnum(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    REVISION = "REVISION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AGREED = "AGREED"
    PUBLISHED = "PUBLISHED"


class ReviewActionEnum(str, Enum):
    CREATED = "CREATED"
    ASSIGNED = "ASSIGNED"
    TAKEN = "TAKEN"
    SENT_TO_REVISION = "SENT_TO_REVISION"
    EDITED = "EDITED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SCHEDULED = "SCHEDULED"
    RESCHEDULED = "RESCHEDULED"
    PUBLICATION_CANCELED = "PUBLICATION_CANCELED"
    PUBLISHED = "PUBLISHED"
    TRANSFER_REQUESTED = "TRANSFER_REQUESTED"
    TRANSFER_APPROVED = "TRANSFER_APPROVED"
    TRANSFER_REJECTED = "TRANSFER_REJECTED"


class ReviewTicket(Base):
    __tablename__ = "review_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    email_id = Column(
        UUID(as_uuid=True),
        ForeignKey("incoming_emails.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(
        SQLEnum(TicketStatusEnum, schema="cchub_announcements"),
        default=TicketStatusEnum.PENDING_REVIEW,
        nullable=False,
    )
    assignee_id = Column(UUID(as_uuid=True), nullable=True)
    assignee_name = Column(String(255), nullable=True)
    title = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    script_ru = Column(Text, nullable=True)
    script_kz = Column(Text, nullable=True)
    ai_summary = Column(Text, nullable=True)
    # Тематическая категория от ИИ (Изменения/Инциденты/Качество работы).
    ai_category = Column(String(50), nullable=True)
    source = Column(String(50), nullable=True)
    recommended_publish_at = Column(DateTime(timezone=True), nullable=True)
    publish_at = Column(DateTime(timezone=True), nullable=True)
    publish_confirmed = Column(Boolean, default=False, nullable=False)
    announcement_id = Column(UUID(as_uuid=True), nullable=True)
    # Передача анонса другому тренеру с подтверждением руководителя УЦ.
    # Заполнены = есть запрос на передачу, ожидающий подтверждения.
    pending_assignee_id = Column(UUID(as_uuid=True), nullable=True)
    pending_assignee_name = Column(String(255), nullable=True)
    transfer_requested_by_id = Column(UUID(as_uuid=True), nullable=True)
    transfer_requested_by_name = Column(String(255), nullable=True)
    transfer_requested_at = Column(DateTime(timezone=True), nullable=True)
    transfer_reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=get_astana_time, onupdate=get_astana_time, nullable=False)

    history = relationship(
        "ReviewHistory",
        back_populates="ticket",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ReviewHistory.created_at",
    )


class ReviewHistory(Base):
    __tablename__ = "review_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(
        UUID(as_uuid=True),
        ForeignKey("review_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(SQLEnum(ReviewActionEnum, schema="cchub_announcements"), nullable=False)
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    actor_name = Column(String(255), nullable=True)
    comment = Column(Text, nullable=True)
    changes = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=get_astana_time, nullable=False)

    ticket = relationship("ReviewTicket", back_populates="history")
