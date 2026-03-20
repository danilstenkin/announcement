from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from enum import Enum
from uuid import UUID

if TYPE_CHECKING:
    from app.models import Announcement


class AnnouncementCategoryEnum(str, Enum):
    """Announcement categories."""

    TECH_QUESTION = "TECH_QUESTION"
    CHANGE = "CHANGE"
    NEW = "NEW"
    REVOKED = "REVOKED"


class AnnouncementBase(BaseModel):
    """Base announcement schema."""

    title: str = Field(..., min_length=1, max_length=255)
    category: AnnouncementCategoryEnum
    text: str = Field(..., min_length=1)
    script_kz: Optional[str] = Field(None)
    product: Optional[str] = Field(None, max_length=255)
    instruction: Optional[str] = None
    topic: Optional[str] = Field(None, max_length=255)
    resource_link: Optional[str] = Field(None, max_length=500)
    script_ru: Optional[str] = None
    attachment_path: Optional[str] = Field(None, max_length=500, description="MinIO object key (set automatically)")


class AnnouncementCreate(AnnouncementBase):
    """Schema for creating an announcement."""

    # is_hidden: bool = Field(
    #     default=True,
    #     description="If True, saves as draft (hidden). If False, publishes immediately.",
    # )


class AnnouncementUpdate(BaseModel):
    """Schema for updating an announcement."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    category: Optional[AnnouncementCategoryEnum] = None
    text: Optional[str] = Field(None, min_length=1)
    script_kz: Optional[str] = Field(None, min_length=1)
    product: Optional[str] = Field(None, max_length=255)
    instruction: Optional[str] = None
    topic: Optional[str] = Field(None, max_length=255)
    resource_link: Optional[str] = Field(None, max_length=500)
    script_ru: Optional[str] = None
    attachment_path: Optional[str] = Field(None, max_length=500)
    # is_hidden: Optional[bool] = Field(
    #     None,
    #     description="Change visibility: True = draft/hidden, False = publish/visible",
    # )


class AnnouncementResponse(AnnouncementBase):
    """Schema for announcement response."""

    id: UUID
    is_hidden: bool
    is_revoked: bool
    revoked_link: Optional[str]
    created_by: UUID
    username: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_read: Optional[bool] = None  # Only for employee responses

    model_config = ConfigDict(from_attributes=True)


class AnnouncementListResponse(AnnouncementBase):
    """Schema for announcements list with read status.

    Inherits all base fields from AnnouncementBase.
    text is overridden as Optional because it is hidden when the announcement is revoked.
    """

    # Override: text is hidden for revoked announcements
    text: Optional[str] = None

    id: UUID
    is_hidden: bool
    is_revoked: bool
    revoked_link: Optional[str]
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    is_read: Optional[bool] = None  # For employees
    username: Optional[str] = None
    email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_announcement(
        cls,
        announcement: "Announcement",
        is_read: Optional[bool],
    ) -> "AnnouncementListResponse":
        """Build a list-response from an ORM Announcement and a read-status flag.

        Centralises the mapping so routers do not duplicate field assignments.
        text is set to None when the announcement is revoked.
        """
        return cls(
            id=announcement.id,
            title=announcement.title,
            category=announcement.category,
            product=announcement.product,
            text=announcement.text if not announcement.is_revoked else None,
            instruction=announcement.instruction,
            topic=announcement.topic,
            resource_link=announcement.resource_link,
            script_kz=announcement.script_kz,
            script_ru=announcement.script_ru,
            attachment_path=announcement.attachment_path,
            is_hidden=announcement.is_hidden,
            is_revoked=announcement.is_revoked,
            revoked_link=announcement.revoked_link,
            created_by=announcement.created_by,
            username=announcement.name,
            email=announcement.email,
            created_at=announcement.created_at,
            updated_at=announcement.updated_at,
            is_read=is_read,
        )


class AnnouncementMonthResponse(BaseModel):
    key: str
    title: str
    count: int


class AnnouncementRevokeRequest(BaseModel):
    """Schema for revoking an announcement."""

    revoked_link: str = Field(
        ..., max_length=500, description="Link to the actual FAQ or resolution"
    )


class RevokeResponse(BaseModel):
    """Response after revoking an announcement."""

    id: UUID
    title: str
    is_revoked: bool
    revoked_link: str


class UpdateResponse(BaseModel):
    """Response after updating an announcement."""

    id: UUID
    title: str
    changes: dict = Field(description="Fields that were changed and their new values")


# ==================== Read Status Schemas ====================


class MarkAsReadRequest(BaseModel):
    """Schema for marking announcement as read."""

    is_read: bool


class ReadStatusResponse(BaseModel):
    """Schema for read status response."""

    user_id: UUID
    announcement_id: UUID
    is_read: bool


# ==================== Notification Schemas ====================


class NotificationEvent(BaseModel):
    """Schema for SSE notification event."""

    event_type: str = Field(
        ...,
        description="Event type: NEW_ANNOUNCEMENT, UPDATED_ANNOUNCEMENT, REVOKED_ANNOUNCEMENT",
    )
    title: str
    message: str
    category: AnnouncementCategoryEnum
    announcement_id: UUID
    timestamp: datetime


class UnreadCounterResponse(BaseModel):
    """Schema for unread counter response."""

    unread_count: int
    announcement: List[AnnouncementListResponse]


# ==================== Error Schemas ====================


class ErrorResponse(BaseModel):
    """Schema for error responses."""

    detail: str
    status_code: int