"""Portal — represents a Max chat bridged to a Matrix room."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

from mautrix.appservice import IntentAPI
from mautrix.types import (
    EventID,
    EventType,
    MessageType,
    RoomID,
    TextMessageEventContent,
)

from .db.portal import Portal as DBPortal

if TYPE_CHECKING:
    from .__main__ import MaxBridge
    from .max.types import MaxChat, MaxEvent, MaxMessage, MaxUser
    from .puppet import Puppet
    from .user import User

logger = logging.getLogger("mau.portal")


class Portal:
    bridge: MaxBridge
    by_max_chat_id: dict[int, Portal] = {}
    by_mxid: dict[RoomID, Portal] = {}

    max_chat_id: int
    mxid: Optional[RoomID]
    name: Optional[str]
    encrypted: bool
    relay_user_id: Optional[str]

    _db: Optional[DBPortal]
    _main_intent: Optional[IntentAPI]

    def __init__(
        self,
        max_chat_id: int,
        mxid: Optional[RoomID] = None,
        name: Optional[str] = None,
        encrypted: bool = False,
        relay_user_id: Optional[str] = None,
    ) -> None:
        self.max_chat_id = max_chat_id
        self.mxid = mxid
        self.name = name
        self.encrypted = encrypted
        self.relay_user_id = relay_user_id
        self._db = None
        self._main_intent = None
        self._create_room_lock = asyncio.Lock()
        self.log = logger.getChild(str(max_chat_id))

    @classmethod
    def init_cls(cls, bridge: MaxBridge) -> None:
        cls.bridge = bridge

    # ── Lookup ──────────────────────────────────────────────────

    @classmethod
    async def get_by_max_chat_id(cls, chat_id: int, *, create: bool = True) -> Optional[Portal]:
        if chat_id in cls.by_max_chat_id:
            return cls.by_max_chat_id[chat_id]
        db_portal = await DBPortal.get_by_max_chat_id(chat_id)
        if db_portal:
            portal = cls(
                max_chat_id=db_portal.max_chat_id,
                mxid=db_portal.mxid,
                name=db_portal.name,
                encrypted=db_portal.encrypted,
            )
            portal._db = db_portal
            cls.by_max_chat_id[chat_id] = portal
            if portal.mxid:
                cls.by_mxid[portal.mxid] = portal
            return portal
        if create:
            portal = cls(max_chat_id=chat_id)
            cls.by_max_chat_id[chat_id] = portal
            return portal
        return None

    @classmethod
    async def get_by_mxid(cls, mxid: RoomID) -> Optional[Portal]:
        if mxid in cls.by_mxid:
            return cls.by_mxid[mxid]
        db_portal = await DBPortal.get_by_mxid(mxid)
        if db_portal:
            portal = cls(
                max_chat_id=db_portal.max_chat_id,
                mxid=db_portal.mxid,
                name=db_portal.name,
            )
            portal._db = db_portal
            cls.by_max_chat_id[db_portal.max_chat_id] = portal
            cls.by_mxid[mxid] = portal
            return portal
        return None

    # ── Matrix room creation ────────────────────────────────────

    async def create_matrix_room(self, source: User, info: MaxChat | None = None) -> RoomID:
        """Create a Matrix room for this portal if it doesn't exist."""
        if self.mxid:
            return self.mxid

        async with self._create_room_lock:
            # Double-check after acquiring lock
            if self.mxid:
                return self.mxid

            if info:
                self.name = info.display_title

            main_intent = self._get_main_intent()
            room_id = await main_intent.create_room(
                name=self.name,
                is_direct=(info.type.value == "dialog" if info else False),
                invitees=[source.mxid],
            )
            self.mxid = room_id
            self.by_mxid[room_id] = self
            await self._save()
            self.log.info("Created Matrix room %s for Max chat %d", room_id, self.max_chat_id)
            return room_id

    # ── Handle Max → Matrix ─────────────────────────────────────

    async def handle_max_message(self, source: User, message: MaxMessage) -> None:
        """Handle an incoming Max message and relay to Matrix."""
        if not self.mxid:
            # Create room on first message
            chat_info = None
            if source.max_client:
                try:
                    chat_info = await source.max_client.get_chat(self.max_chat_id)
                except Exception:
                    pass
            await self.create_matrix_room(source, chat_info)

        # Get puppet for the sender
        puppet = None
        if message.sender:
            from .puppet import Puppet
            puppet = await Puppet.get_by_max_user_id(message.sender.user_id)
            if puppet:
                await puppet.update_info(message.sender)

        intent = puppet.intent if puppet else self._get_main_intent()

        # Send message to Matrix
        text = message.text or ""
        content = TextMessageEventContent(
            msgtype=MessageType.TEXT,
            body=text,
        )

        # Handle reply
        if message.reply_to:
            from .db.message import Message as DBMessage
            db_msg = await DBMessage.get_by_max_msg_id(self.max_chat_id, message.reply_to)
            if db_msg and db_msg.mxid:
                content.set_reply(EventID(db_msg.mxid))

        event_id = await intent.send_message(self.mxid, content)

        # Save message mapping (skip if message_id is empty)
        if message.message_id:
            from .db.message import Message as DBMessage
            await DBMessage.insert(
                max_chat_id=self.max_chat_id,
                max_msg_id=message.message_id,
                mxid=str(event_id),
                mx_room=str(self.mxid),
            )

    async def handle_max_edit(self, message_id: str, new_text: str) -> None:
        """Handle a Max message edit."""
        from .db.message import Message as DBMessage
        db_msg = await DBMessage.get_by_max_msg_id(self.max_chat_id, message_id)
        if not db_msg or not self.mxid:
            return
        # Send edit event to Matrix
        intent = self._get_main_intent()
        content = TextMessageEventContent(
            msgtype=MessageType.TEXT,
            body=f"* {new_text}",
        )
        content.set_edit(EventID(db_msg.mxid))
        await intent.send_message(self.mxid, content)

    async def handle_max_delete(self, message_id: str) -> None:
        """Handle a Max message deletion."""
        from .db.message import Message as DBMessage
        db_msg = await DBMessage.get_by_max_msg_id(self.max_chat_id, message_id)
        if not db_msg or not self.mxid:
            return
        intent = self._get_main_intent()
        await intent.redact(RoomID(self.mxid), EventID(db_msg.mxid))

    # ── Handle Matrix → Max ─────────────────────────────────────

    async def handle_matrix_message(self, sender: User, event_id: EventID, content: TextMessageEventContent) -> None:
        """Handle a Matrix message and relay to Max."""
        if not sender.max_client:
            self.log.warning("User %s has no Max client", sender.mxid)
            return

        text = content.body or ""

        # Check for reply
        reply_to = None
        if content.relates_to and content.relates_to.in_reply_to:
            from .db.message import Message as DBMessage
            replied_evt = content.relates_to.in_reply_to.event_id
            db_msg = await DBMessage.get_by_mxid(str(replied_evt))
            if db_msg:
                reply_to = db_msg.max_msg_id

        # Check for edit
        if content.relates_to and getattr(content.relates_to, "rel_type", None) and content.relates_to.rel_type.value == "m.replace":
            from .db.message import Message as DBMessage
            edited_evt = content.relates_to.event_id
            db_msg = await DBMessage.get_by_mxid(str(edited_evt))
            if db_msg:
                new_text = content.body or text
                if hasattr(content, "new_content") and content.new_content:
                    new_text = content.new_content.body or new_text
                await sender.max_client.edit_message(db_msg.max_msg_id, new_text)
                return

        max_msg = await sender.max_client.send_message(
            self.max_chat_id, text, reply_to=reply_to
        )

        # Save message mapping (skip if message_id is empty)
        if max_msg.message_id:
            from .db.message import Message as DBMessage
            await DBMessage.insert(
                max_chat_id=self.max_chat_id,
                max_msg_id=max_msg.message_id,
                mxid=str(event_id),
                mx_room=str(self.mxid),
            )

    async def handle_matrix_redaction(self, sender: User, event_id: EventID) -> None:
        """Handle a Matrix message redaction (deletion)."""
        if not sender.max_client:
            return
        from .db.message import Message as DBMessage
        db_msg = await DBMessage.get_by_mxid(str(event_id))
        if db_msg:
            await sender.max_client.delete_message(db_msg.max_msg_id)

    # ── Helpers ─────────────────────────────────────────────────

    def _get_main_intent(self) -> IntentAPI:
        if self._main_intent is None:
            self._main_intent = self.bridge.az.intent
        return self._main_intent

    async def _save(self) -> None:
        if self._db:
            await self._db.update(
                mxid=self.mxid,
                name=self.name,
                encrypted=self.encrypted,
            )
        else:
            self._db = await DBPortal.insert(
                max_chat_id=self.max_chat_id,
                mxid=self.mxid,
                name=self.name,
                encrypted=self.encrypted,
            )
