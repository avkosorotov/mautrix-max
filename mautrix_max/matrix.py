"""Matrix event handler for mautrix-max bridge."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mautrix.bridge import BaseMatrixHandler
from mautrix.types import (
    Event,
    EventID,
    EventType,
    MediaMessageEventContent,
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

    async def handle_event(self, evt: Event) -> None:
        """Override to intercept m.reaction events before default dispatch."""
        if evt.type == EventType.REACTION:
            await self._handle_reaction(evt)
            return
        await super().handle_event(evt)

    async def _handle_reaction(self, evt: Event) -> None:
        """Handle an incoming Matrix reaction (m.reaction)."""
        from .portal import Portal
        from .user import User

        if self.bridge.is_bridge_ghost(evt.sender):
            return

        portal = await Portal.get_by_mxid(evt.room_id)
        if not portal:
            return

        sender = await User.get_by_mxid(evt.sender)
        if not sender or not sender.is_logged_in:
            return

        # Extract reaction data from m.relates_to
        content = evt.content
        relates_to = None
        if hasattr(content, "relates_to"):
            relates_to = content.relates_to
        elif isinstance(content, dict):
            relates_to = content.get("m.relates_to")

        if not relates_to:
            return

        # Get the target event ID and emoji key
        if isinstance(relates_to, dict):
            target_event_id = relates_to.get("event_id")
            emoji = relates_to.get("key", "")
        else:
            target_event_id = getattr(relates_to, "event_id", None)
            emoji = getattr(relates_to, "key", "")

        if not target_event_id or not emoji:
            return

        await portal.handle_matrix_reaction(
            sender, evt.event_id, emoji, EventID(target_event_id)
        )

    async def handle_receipt(self, evt: Event) -> None:
        """Handle Matrix read receipts — forward to Max."""
        from .portal import Portal
        from .user import User

        portal = await Portal.get_by_mxid(evt.room_id)
        if not portal:
            return

        # Receipt content: {event_id: {"m.read": {user_id: {ts: N}}}}
        content = evt.content
        if isinstance(content, dict):
            receipt_data = content
        elif hasattr(content, "serialize"):
            receipt_data = content.serialize()
        else:
            receipt_data = {}

        for event_id_str, receipt_types in receipt_data.items():
            if not isinstance(receipt_types, dict):
                continue
            read_users = receipt_types.get("m.read", {})
            if not isinstance(read_users, dict):
                continue
            for user_id in read_users:
                if self.bridge.is_bridge_ghost(user_id):
                    continue
                sender = await User.get_by_mxid(user_id)
                if sender and sender.is_logged_in and sender.max_client:
                    await portal.handle_matrix_read_receipt(
                        sender, EventID(event_id_str)
                    )

    async def handle_typing(self, room_id: RoomID, typing: list[str]) -> None:
        """Handle Matrix typing events — forward to Max."""
        from .portal import Portal
        from .user import User

        portal = await Portal.get_by_mxid(room_id)
        if not portal:
            return

        for user_id in typing:
            if self.bridge.is_bridge_ghost(user_id):
                continue
            sender = await User.get_by_mxid(user_id)
            if sender and sender.is_logged_in and sender.max_client:
                try:
                    await sender.max_client.send_typing(portal.max_chat_id)
                except Exception:
                    logger.debug("Failed to send typing to Max for %s", portal.max_chat_id)

    async def handle_message(self, evt: MessageEvent, was_encrypted: bool = False) -> None:
        """Handle an incoming Matrix message."""
        from .portal import Portal
        from .user import User

        # Skip events from bridge ghosts/puppets (prevent echo loop)
        if self.bridge.is_bridge_ghost(evt.sender):
            return

        portal = await Portal.get_by_mxid(evt.room_id)
        if not portal:
            return

        sender = await User.get_by_mxid(evt.sender)
        if not sender or not sender.is_logged_in:
            return

        content = evt.content
        if isinstance(content, TextMessageEventContent):
            await portal.handle_matrix_message(sender, evt.event_id, content)
        elif isinstance(content, MediaMessageEventContent):
            await portal.handle_matrix_media(sender, evt.event_id, content)
        elif hasattr(content, 'msgtype') and content.msgtype in (
            MessageType.IMAGE, MessageType.FILE, MessageType.VIDEO, MessageType.AUDIO,
        ):
            # Fallback: treat as media even if not properly typed
            await portal.handle_matrix_media(sender, evt.event_id, content)

    async def handle_redaction(self, room_id: RoomID, user_id: str, event_id: EventID, redaction: RedactionEvent) -> None:
        """Handle a Matrix message redaction (deletion)."""
        from .portal import Portal
        from .user import User

        # Skip events from bridge ghosts/puppets (prevent echo loop)
        if self.bridge.is_bridge_ghost(user_id):
            return

        portal = await Portal.get_by_mxid(room_id)
        if not portal:
            return

        sender = await User.get_by_mxid(user_id)
        if not sender or not sender.is_logged_in:
            return

        await portal.handle_matrix_redaction(sender, event_id)
