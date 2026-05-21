from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, List, TYPE_CHECKING
from datetime import datetime
from uuid import UUID

from models.announcement import AnnouncementCategoryEnum

if TYPE_CHECKING:
    from models.announcement import Announcement


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
    attachment_path: Optional[str] = Field(
        None, max_length=500, description="MinIO object key (set automatically)"
    )


class AnnouncementCreate(AnnouncementBase):
    """Schema for creating an announcement."""


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


class AttachmentResponse(BaseModel):
    """Schema for attachment response."""
    id: UUID
    filename: str
    object_key: str
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    uploaded_at: datetime
    is_ai: bool = False
    source: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AnnouncementResponse(AnnouncementBase):
    """Schema for announcement response."""
    id: UUID
    is_hidden: bool
    is_revoked: bool
    revoked_link: Optional[str]
    created_by: UUID
    username: Optional[str] = Field(None, validation_alias="name")
    email: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_read: Optional[bool] = None
    is_ai: bool = False
    source: Optional[str] = None
    attachments: List[AttachmentResponse] = []

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AnnouncementListResponse(AnnouncementBase):
    """Schema for announcements list with read status."""

    text: Optional[str] = None

    id: UUID
    is_hidden: bool
    is_revoked: bool
    revoked_link: Optional[str]
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    is_read: Optional[bool] = None
    username: Optional[str] = None
    email: Optional[str] = None
    is_ai: bool = False
    source: Optional[str] = None
    attachments: List[AttachmentResponse] = []

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_announcement(
        cls,
        announcement: "Announcement",
        is_read: Optional[bool],
    ) -> "AnnouncementListResponse":
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
            is_ai=announcement.is_ai or False,
            source=announcement.source,
            attachments=[
                AttachmentResponse.model_validate(att)
                for att in (announcement.attachments or [])
            ],
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
    is_read: bool


class ReadStatusResponse(BaseModel):
    user_id: UUID
    announcement_id: UUID
    is_read: bool


# ==================== Notification Schemas ====================


class NotificationEvent(BaseModel):
    event_type: str = Field(
        ...,
        description="Event type: NEW_ANNOUNCEMENT, UPDATED_ANNOUNCEMENT, REVOKED_ANNOUNCEMENT",
    )
    title: str
    message: str
    category: AnnouncementCategoryEnum
    announcement_id: UUID
    topic: Optional[str] = None
    product: Optional[str] = None
    text: Optional[str] = None
    timestamp: datetime
    is_ai: bool = False
    source: Optional[str] = None


class UnreadCounterResponse(BaseModel):
    unread_count: int
    announcement: List[AnnouncementListResponse]


class ErrorResponse(BaseModel):
    detail: str
    status_code: int
