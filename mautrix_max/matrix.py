"""Matrix event handler for mautrix-max bridge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mautrix.bridge import BaseMatrixHandler
from mautrix.types import (
    Event,
    EventID,
    EventType,
    MessageEvent,
    MessageType,
    RedactionEvent,
    RoomID,
    StateEvent,
    TextMessageEventContent,
)

if TYPE_CHECKING:
    from .__main__ import MaxBridge

logger = logging.getLogger("mau.matrix")


class MatrixHandler(BaseMatrixHandler):
    def __init__(self, bridge: MaxBridge) -> None:
        super().__init__(bridge=bridge)
        self.bridge = bridge

    async def handle_message(self, room_id: RoomID, user_id: str, message: MessageEvent) -> None:
        """Handle an incoming Matrix message."""
        from .portal import Portal
        from .user import User

        portal = await Portal.get_by_mxid(room_id)
        if not portal:
            return

        sender = await User.get_by_mxid(user_id)
        if not sender or not sender.is_logged_in:
            return

        content = message.content
        if not isinstance(content, TextMessageEventContent):
            # For now, only handle text messages
            # TODO: handle media messages
            return

        await portal.handle_matrix_message(sender, message.event_id, content)

    async def handle_redaction(self, room_id: RoomID, user_id: str, event_id: EventID, redaction: RedactionEvent) -> None:
        """Handle a Matrix message redaction (deletion)."""
        from .portal import Portal
        from .user import User

        portal = await Portal.get_by_mxid(room_id)
        if not portal:
            return

        sender = await User.get_by_mxid(user_id)
        if not sender or not sender.is_logged_in:
            return

        await portal.handle_matrix_redaction(sender, event_id)
