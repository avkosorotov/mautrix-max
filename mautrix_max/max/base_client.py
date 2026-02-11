"""Abstract base client for Max Messenger API."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from .types import MaxChat, MaxEvent, MaxMessage, MaxUser

if TYPE_CHECKING:
    pass

EventHandler = Callable[[MaxEvent], Awaitable[None]]


class BaseMaxClient(ABC):
    """Abstract interface for Max Messenger clients.

    Both BotMaxClient (REST + long-polling) and UserMaxClient (WebSocket)
    implement this interface, allowing the bridge to work with either mode.
    """

    log: logging.Logger
    on_event: Optional[EventHandler] = None

    def __init__(self) -> None:
        self.log = logging.getLogger(self.__class__.__qualname__)
        self.on_event = None

    @abstractmethod
    async def connect(self) -> None:
        """Connect to Max and start receiving events."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from Max and stop receiving events."""

    @abstractmethod
    async def is_connected(self) -> bool:
        """Check if the client is currently connected."""

    @abstractmethod
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> MaxMessage:
        """Send a text message to a Max chat."""

    @abstractmethod
    async def edit_message(self, message_id: str, text: str) -> None:
        """Edit a previously sent message."""

    @abstractmethod
    async def delete_message(self, message_id: str) -> None:
        """Delete a message."""

    @abstractmethod
    async def get_chat(self, chat_id: int) -> MaxChat:
        """Get chat information."""

    @abstractmethod
    async def get_chat_members(self, chat_id: int) -> list[MaxUser]:
        """Get list of chat members."""

    @abstractmethod
    async def get_user_info(self, user_id: int) -> MaxUser:
        """Get user information."""

    @abstractmethod
    async def download_media(self, url: str) -> bytes:
        """Download media from a Max URL."""

    @abstractmethod
    async def upload_media(
        self, data: bytes, filename: str, content_type: str
    ) -> str:
        """Upload media and return the attachment token/URL."""

    async def get_chat_history(self, chat_id: int, count: int = 10) -> list[dict]:
        """Get recent messages from a chat. Returns raw message dicts."""
        return []

    @abstractmethod
    async def add_reaction(self, chat_id: int, message_id: str, emoji: str) -> None:
        """Add a reaction to a message (User API only, no-op for Bot API)."""

    @abstractmethod
    async def mark_as_read(self, chat_id: int, message_id: str) -> None:
        """Mark a message as read (User API only, no-op for Bot API)."""

    async def _dispatch_event(self, event: MaxEvent) -> None:
        """Dispatch an event to the registered handler."""
        if self.on_event:
            try:
                await self.on_event(event)
            except Exception:
                self.log.exception("Error handling Max event %s", event.type)
