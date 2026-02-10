"""User — a Matrix user who has connected their Max account."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from mautrix.types import UserID

from .db.user import User as DBUser
from .max.base_client import BaseMaxClient
from .max.bot_client import BotMaxClient
from .max.types import EventType, MaxEvent
from .max.user_client import UserMaxClient

if TYPE_CHECKING:
    from .__main__ import MaxBridge

logger = logging.getLogger("mau.user")


class User:
    bridge: MaxBridge
    by_mxid: dict[str, User] = {}

    mxid: str
    max_user_id: Optional[int]
    max_token: Optional[str]
    connection_mode: Optional[str]  # "bot" or "user"
    bot_token: Optional[str]
    max_client: Optional[BaseMaxClient]
    _db: Optional[DBUser]

    def __init__(
        self,
        mxid: str,
        max_user_id: Optional[int] = None,
        max_token: Optional[str] = None,
        connection_mode: Optional[str] = None,
        bot_token: Optional[str] = None,
    ) -> None:
        self.mxid = mxid
        self.max_user_id = max_user_id
        self.max_token = max_token
        self.connection_mode = connection_mode
        self.bot_token = bot_token
        self.max_client = None
        self._db = None
        self.log = logger.getChild(mxid)

    @classmethod
    def init_cls(cls, bridge: MaxBridge) -> None:
        cls.bridge = bridge

    @classmethod
    async def get_by_mxid(cls, mxid: str, *, create: bool = True) -> Optional[User]:
        mxid = str(mxid)
        if mxid in cls.by_mxid:
            return cls.by_mxid[mxid]
        db_user = await DBUser.get_by_mxid(mxid)
        if db_user:
            user = cls(
                mxid=mxid,
                max_user_id=db_user.max_user_id,
                max_token=db_user.max_token,
                connection_mode=db_user.connection_mode,
                bot_token=db_user.bot_token,
            )
            user._db = db_user
            cls.by_mxid[mxid] = user
            return user
        if create:
            user = cls(mxid=mxid)
            cls.by_mxid[mxid] = user
            return user
        return None

    @classmethod
    async def all_logged_in(cls) -> list[User]:
        db_users = await DBUser.all_logged_in()
        users = []
        for db_user in db_users:
            user = await cls.get_by_mxid(db_user.mxid)
            if user:
                users.append(user)
        return users

    @property
    def is_logged_in(self) -> bool:
        return bool(self.bot_token or self.max_token)

    async def connect(self) -> None:
        """Connect to Max using the configured mode."""
        if self.max_client:
            await self.disconnect()

        if self.connection_mode == "bot" and self.bot_token:
            self.max_client = BotMaxClient(
                token=self.bot_token,
                api_url=self.bridge.config["max.api_url"],
                polling_timeout=self.bridge.config["max.polling_timeout"],
            )
        elif self.connection_mode == "user" and self.max_token:
            self.max_client = UserMaxClient(
                ws_url=self.bridge.config["max.ws_url"],
                auth_token=self.max_token,
            )
        else:
            self.log.warning("No valid credentials for connection mode %s", self.connection_mode)
            return

        self.max_client.on_event = self._on_max_event

        try:
            await self.max_client.connect()
            self.log.info("Connected to Max (mode=%s)", self.connection_mode)
            # Store bot's own user_id to filter echoed messages
            if hasattr(self.max_client, '_me') and self.max_client._me:
                if not self.max_user_id:
                    self.max_user_id = self.max_client._me.user_id
                    await self._save()
                    self.log.info("Stored bot user_id: %d", self.max_user_id)
        except Exception:
            self.log.exception("Failed to connect to Max")
            self.max_client = None

    async def disconnect(self) -> None:
        """Disconnect from Max."""
        if self.max_client:
            try:
                await self.max_client.disconnect()
            except Exception:
                self.log.exception("Error disconnecting from Max")
            self.max_client = None

    async def _on_max_event(self, event: MaxEvent) -> None:
        """Handle an event from Max and dispatch to the appropriate portal."""
        from .portal import Portal

        # BOT_STARTED: user started the bot — update puppet with avatar info
        if event.type == EventType.BOT_STARTED and event.user:
            from .puppet import Puppet
            puppet = await Puppet.get_by_max_user_id(event.user.user_id)
            if puppet:
                await puppet.update_info(event.user)
            # Also ensure portal exists for future messages
            portal = await Portal.get_by_max_chat_id(event.chat_id)
            return

        portal = await Portal.get_by_max_chat_id(event.chat_id)
        if not portal:
            return

        if event.type == EventType.MESSAGE_CREATED and event.message:
            # Dedup: skip messages already bridged from Matrix → Max
            if event.message.message_id:
                from .db.message import Message as DBMessage
                existing = await DBMessage.get_by_max_msg_id(
                    portal.max_chat_id, event.message.message_id
                )
                if existing:
                    return
            await portal.handle_max_message(self, event.message)
        elif event.type == EventType.MESSAGE_EDITED:
            # message_id can be at top level or inside message object
            msg_id = event.message_id or (event.message.message_id if event.message else None)
            new_text = (event.message.text if event.message else None) or ""
            if msg_id:
                await portal.handle_max_edit(msg_id, new_text)
        elif event.type == EventType.MESSAGE_REMOVED:
            msg_id = event.message_id or (event.message.message_id if event.message else None)
            if msg_id:
                await portal.handle_max_delete(msg_id)

    async def login_bot(self, token: str) -> None:
        """Set up bot mode with the given token."""
        self.bot_token = token
        self.connection_mode = "bot"
        await self._save()
        await self.connect()

    async def login_user(self, auth_token: str, user_id: int) -> None:
        """Set up user mode with the given auth token."""
        self.max_token = auth_token
        self.max_user_id = user_id
        self.connection_mode = "user"
        await self._save()
        await self.connect()

    async def logout(self) -> None:
        """Disconnect and clear credentials."""
        await self.disconnect()
        self.max_token = None
        self.bot_token = None
        self.max_user_id = None
        self.connection_mode = None
        await self._save()

    async def _save(self) -> None:
        if self._db:
            await self._db.update(
                max_user_id=self.max_user_id,
                max_token=self.max_token,
                connection_mode=self.connection_mode,
                bot_token=self.bot_token,
            )
        else:
            self._db = await DBUser.insert(
                mxid=self.mxid,
                max_user_id=self.max_user_id,
                max_token=self.max_token,
                connection_mode=self.connection_mode,
                bot_token=self.bot_token,
            )
