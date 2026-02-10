"""Max Messenger data types."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChatType(str, enum.Enum):
    DIALOG = "dialog"
    GROUP = "group"
    CHANNEL = "channel"


class AttachmentType(str, enum.Enum):
    PHOTO = "photo"
    IMAGE = "image"  # Bot API uses "image" instead of "photo"
    FILE = "file"
    STICKER = "sticker"
    VIDEO = "video"
    VOICE = "voice"
    AUDIO = "audio"
    CONTACT = "contact"
    LOCATION = "location"

    @property
    def is_photo(self) -> bool:
        return self in (AttachmentType.PHOTO, AttachmentType.IMAGE)


class EventType(str, enum.Enum):
    MESSAGE_CREATED = "message_created"
    MESSAGE_EDITED = "message_edited"
    MESSAGE_REMOVED = "message_removed"
    MESSAGE_CALLBACK = "message_callback"  # button callback
    BOT_STARTED = "bot_started"
    BOT_ADDED = "bot_added"
    BOT_REMOVED = "bot_removed"
    USER_ADDED = "user_added"
    USER_REMOVED = "user_removed"
    CHAT_TITLE_CHANGED = "chat_title_changed"


class MaxUser(BaseModel):
    """A Max Messenger user."""

    user_id: int
    name: str
    username: Optional[str] = None
    avatar_url: Optional[str] = None
    is_bot: bool = False
    last_activity_time: Optional[int] = None

    @property
    def display_name(self) -> str:
        return self.name or self.username or str(self.user_id)


class MaxPhoto(BaseModel):
    """Photo size info returned by Max API."""

    url: str
    token: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


class MaxAttachment(BaseModel):
    """An attachment on a Max message."""

    type: AttachmentType
    # Photo fields
    photos: Optional[dict[str, MaxPhoto]] = None
    # File/video/audio fields
    url: Optional[str] = None
    file_id: Optional[int] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None
    # Sticker fields
    sticker_id: Optional[str] = None
    # Location fields
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @property
    def best_photo_url(self) -> Optional[str]:
        """Get the highest-resolution photo URL."""
        if not self.photos:
            # Fallback: check url field directly (some API formats)
            return self.url
        # Prefer original > large > medium > small
        for key in ("original", "large", "medium", "small"):
            if key in self.photos:
                return self.photos[key].url
        # Fallback to first available
        return next(iter(self.photos.values())).url if self.photos else self.url


class MaxLinkedMessage(BaseModel):
    """A linked (replied-to) message reference."""

    type: str = "reply"  # "reply" | "forward"
    mid: str  # message ID
    sender: Optional[MaxUser] = None
    text: Optional[str] = None


class MaxMessage(BaseModel):
    """A Max Messenger message."""

    message_id: str = Field(alias="mid", default="")
    timestamp: int = 0
    sender: Optional[MaxUser] = None
    recipient: Optional[dict[str, Any]] = None
    body: Optional[dict[str, Any]] = None  # Raw body with text + attachments
    link: Optional[MaxLinkedMessage] = None
    stat: Optional[dict[str, Any]] = None  # views count etc.

    class Config:
        populate_by_name = True

    @property
    def text(self) -> Optional[str]:
        if self.body:
            return self.body.get("text")
        return None

    @property
    def attachments(self) -> list[MaxAttachment]:
        if not self.body or "attachments" not in self.body:
            return []
        result = []
        for att in self.body["attachments"]:
            att_type = att.get("type", "")
            try:
                # Bot API wraps attachment data in "payload", unwrap it
                payload = att.get("payload", {})
                fields = {k: v for k, v in att.items() if k not in ("type", "payload")}
                if isinstance(payload, dict):
                    fields.update(payload)
                result.append(MaxAttachment(type=att_type, **fields))
            except Exception:
                continue
        return result

    @property
    def chat_id(self) -> Optional[int]:
        if self.recipient:
            return self.recipient.get("chat_id")
        return None

    @property
    def reply_to(self) -> Optional[str]:
        if self.link and self.link.type == "reply":
            return self.link.mid
        return None


class MaxChat(BaseModel):
    """A Max Messenger chat."""

    chat_id: int
    type: ChatType = ChatType.DIALOG
    title: Optional[str] = None
    icon: Optional[dict[str, Any]] = None
    members_count: int = 0
    owner_id: Optional[int] = None
    participants: Optional[list[MaxUser]] = None
    is_public: bool = False
    last_event_time: Optional[int] = None
    description: Optional[str] = None
    dialog_with_user: Optional[MaxUser] = None

    @property
    def display_title(self) -> str:
        if self.dialog_with_user:
            return self.dialog_with_user.display_name
        return self.title or f"Chat {self.chat_id}"


class MaxUpdate(BaseModel):
    """A single update from the Max Bot API long-polling or webhook."""

    update_type: EventType
    timestamp: int
    message: Optional[MaxMessage] = None
    chat_id: Optional[int] = None
    user: Optional[MaxUser] = None
    # For message_removed
    message_id: Optional[str] = None
    # For callbacks
    callback: Optional[dict[str, Any]] = None

    class Config:
        populate_by_name = True


class MaxEvent(BaseModel):
    """Internal event wrapper used by both Bot and User clients."""

    type: EventType
    chat_id: int
    message: Optional[MaxMessage] = None
    user: Optional[MaxUser] = None
    # Edit/delete specific
    message_id: Optional[str] = None
    # New text for edits
    new_text: Optional[str] = None
    # Reaction
    reaction: Optional[str] = None
    sender_id: Optional[int] = None
    timestamp: int = 0
