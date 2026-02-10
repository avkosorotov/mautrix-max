"""Puppet — a ghost Matrix user representing a Max user."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import aiohttp
from mautrix.appservice import IntentAPI
from mautrix.types import UserID

from .db.puppet import Puppet as DBPuppet

if TYPE_CHECKING:
    from .__main__ import MaxBridge
    from .max.types import MaxUser

logger = logging.getLogger("mau.puppet")


class Puppet:
    bridge: MaxBridge
    by_max_user_id: dict[int, Puppet] = {}

    max_user_id: int
    name: Optional[str]
    username: Optional[str]
    avatar_mxc: Optional[str]
    name_set: bool
    avatar_set: bool
    is_registered: bool

    intent: IntentAPI

    def __init__(
        self,
        max_user_id: int,
        name: Optional[str] = None,
        username: Optional[str] = None,
        avatar_mxc: Optional[str] = None,
        name_set: bool = False,
        avatar_set: bool = False,
        is_registered: bool = False,
    ) -> None:
        self.max_user_id = max_user_id
        self.name = name
        self.username = username
        self.avatar_mxc = avatar_mxc
        self.name_set = name_set
        self.avatar_set = avatar_set
        self.is_registered = is_registered
        self.log = logger.getChild(str(max_user_id))

    @classmethod
    def init_cls(cls, bridge: MaxBridge) -> None:
        cls.bridge = bridge

    @property
    def mxid(self) -> UserID:
        template = self.bridge.config.username_template
        localpart = template.format(userid=self.max_user_id)
        return UserID(f"@{localpart}:{self.bridge.config['homeserver.domain']}")

    @property
    def displayname(self) -> str:
        template = self.bridge.config.displayname_template
        return template.format(
            displayname=self.name or str(self.max_user_id),
            username=self.username or "",
            id=self.max_user_id,
        )

    def _get_intent(self) -> IntentAPI:
        return self.bridge.az.intent.user(self.mxid)

    @classmethod
    async def get_by_max_user_id(cls, user_id: int, *, create: bool = True) -> Optional[Puppet]:
        if user_id in cls.by_max_user_id:
            return cls.by_max_user_id[user_id]
        db_puppet = await DBPuppet.get_by_max_user_id(user_id)
        if db_puppet:
            puppet = cls(
                max_user_id=db_puppet.max_user_id,
                name=db_puppet.name,
                avatar_mxc=db_puppet.avatar_mxc,
                name_set=db_puppet.name_set,
                avatar_set=db_puppet.avatar_set,
                is_registered=db_puppet.is_registered,
            )
            puppet.intent = puppet._get_intent()
            cls.by_max_user_id[user_id] = puppet
            return puppet
        if create:
            puppet = cls(max_user_id=user_id)
            puppet.intent = puppet._get_intent()
            cls.by_max_user_id[user_id] = puppet
            return puppet
        return None

    @classmethod
    async def get_by_mxid(cls, mxid: UserID) -> Optional[Puppet]:
        # Parse mxid to extract max_user_id
        template = cls.bridge.config.username_template
        prefix = template.split("{userid}")[0]
        suffix = template.split("{userid}")[1] if "{userid}" in template else ""
        localpart = mxid.split(":")[0][1:]  # Remove @ and domain
        if not localpart.startswith(prefix):
            return None
        user_id_str = localpart[len(prefix):]
        if suffix and user_id_str.endswith(suffix):
            user_id_str = user_id_str[:-len(suffix)]
        try:
            user_id = int(user_id_str)
        except ValueError:
            return None
        return await cls.get_by_max_user_id(user_id)

    async def update_info(self, info: MaxUser) -> None:
        """Update puppet display name and avatar from Max user info."""
        changed = False
        if info.name and info.name != self.name:
            self.name = info.name
            changed = True
        if info.username and info.username != self.username:
            self.username = info.username
            changed = True

        if changed or not self.name_set:
            try:
                await self.intent.set_displayname(self.displayname)
                if not self.name_set:
                    self.name_set = True
                    changed = True
            except Exception:
                self.log.exception("Failed to set displayname")

        if info.avatar_url and not self.avatar_set:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(info.avatar_url) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            mime = resp.headers.get("Content-Type", "image/png")
                            mxc = await self.intent.upload_media(data, mime_type=mime)
                            await self.intent.set_avatar_url(mxc)
                            self.avatar_mxc = str(mxc)
                            self.avatar_set = True
                            changed = True
                            self.log.info("Set avatar from %s -> %s", info.avatar_url, mxc)
            except Exception:
                self.log.exception("Failed to set avatar")

        if changed:
            await self.save()

    async def save(self) -> None:
        await DBPuppet.insert_or_update(
            max_user_id=self.max_user_id,
            name=self.name,
            username=self.username,
            avatar_mxc=self.avatar_mxc,
            name_set=self.name_set,
            avatar_set=self.avatar_set,
            is_registered=self.is_registered,
        )
