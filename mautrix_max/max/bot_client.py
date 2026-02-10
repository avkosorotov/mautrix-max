"""Max Bot API client (REST + long-polling)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import aiohttp

from .base_client import BaseMaxClient
from .errors import AuthError, MaxAPIError, NotFoundError, RateLimitError
from .types import (
    AttachmentType,
    ChatType,
    EventType,
    MaxAttachment,
    MaxChat,
    MaxEvent,
    MaxMessage,
    MaxPhoto,
    MaxUpdate,
    MaxUser,
)


class BotMaxClient(BaseMaxClient):
    """Max Bot API client using REST endpoints and long-polling for updates.

    Reference: https://platform-api.max.ru (Max Bot API)
    """

    def __init__(
        self,
        token: str,
        api_url: str = "https://platform-api.max.ru",
        polling_timeout: int = 90,
    ) -> None:
        super().__init__()
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.polling_timeout = polling_timeout
        self._session: Optional[aiohttp.ClientSession] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._marker: Optional[int] = None
        self._running = False
        self._me: Optional[MaxUser] = None

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token}

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=self.polling_timeout + 30),
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        data: Optional[aiohttp.FormData] = None,
    ) -> dict[str, Any]:
        """Make an API request and return the JSON response."""
        session = await self._ensure_session()
        url = f"{self.api_url}{path}"
        async with session.request(method, url, params=params, json=json, data=data) as resp:
            if resp.status == 401:
                raise AuthError("Invalid bot token")
            if resp.status == 404:
                raise NotFoundError(path)
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", "5"))
                raise RateLimitError(retry_after)
            body = await resp.json()
            if resp.status >= 400:
                raise MaxAPIError(
                    code=body.get("code", "unknown"),
                    message=body.get("message", str(body)),
                    status=resp.status,
                )
            return body

    # -- Connection ----------------------------------------------------------

    async def connect(self) -> None:
        """Verify the bot token and start long-polling."""
        resp = await self._request("GET", "/me")
        self._me = MaxUser(
            user_id=resp.get("user_id", 0),
            name=resp.get("name", ""),
            username=resp.get("username"),
            avatar_url=resp.get("avatar_url"),
            is_bot=True,
        )
        self.log.info("Authenticated as bot: %s (ID: %d)", self._me.name, self._me.user_id)
        self._running = True
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def disconnect(self) -> None:
        """Stop long-polling and close the session."""
        self._running = False
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    async def is_connected(self) -> bool:
        return self._running and self._polling_task is not None and not self._polling_task.done()

    # -- Long-polling --------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Continuously poll for updates."""
        self.log.debug("Starting long-polling loop (timeout=%ds)", self.polling_timeout)
        while self._running:
            try:
                params: dict[str, Any] = {"timeout": self.polling_timeout}
                if self._marker is not None:
                    params["marker"] = self._marker
                resp = await self._request("GET", "/updates", params=params)
                updates = resp.get("updates", [])
                if resp.get("marker"):
                    self._marker = resp["marker"]
                for raw_update in updates:
                    await self._handle_raw_update(raw_update)
            except asyncio.CancelledError:
                break
            except RateLimitError as e:
                self.log.warning("Rate limited, sleeping %ds", e.retry_after)
                await asyncio.sleep(e.retry_after)
            except Exception:
                self.log.exception("Error in polling loop, retrying in 5s")
                await asyncio.sleep(5)

    async def _handle_raw_update(self, raw: dict[str, Any]) -> None:
        """Parse a raw update and dispatch as MaxEvent."""
        self.log.debug("Raw update: %s", raw)
        update_type_str = raw.get("update_type", "")
        try:
            event_type = EventType(update_type_str)
        except ValueError:
            self.log.debug("Unknown update type: %s", update_type_str)
            return

        message = None
        raw_message = raw.get("message")
        if raw_message:
            sender = None
            raw_sender = raw_message.get("sender")
            if raw_sender:
                sender = MaxUser(
                    user_id=raw_sender.get("user_id", 0),
                    name=raw_sender.get("name", ""),
                    username=raw_sender.get("username"),
                    avatar_url=raw_sender.get("avatar_url"),
                    is_bot=raw_sender.get("is_bot", False),
                )
            link = None
            raw_link = raw_message.get("link")
            if raw_link:
                from .types import MaxLinkedMessage
                link = MaxLinkedMessage(
                    type=raw_link.get("type", "reply"),
                    mid=raw_link.get("mid", ""),
                )
            # mid can be at top level OR inside body (Max Bot API inconsistency)
            raw_body = raw_message.get("body") or {}
            mid = (
                raw_message.get("mid")
                or (raw_body.get("mid") if isinstance(raw_body, dict) else None)
                or str(raw_message.get("message_id", ""))
            )
            message = MaxMessage(
                mid=mid,
                timestamp=raw_message.get("timestamp", 0),
                sender=sender,
                recipient=raw_message.get("recipient"),
                body=raw_body if isinstance(raw_body, dict) else raw_message.get("body"),
                link=link,
                stat=raw_message.get("stat"),
            )

        chat_id = raw.get("chat_id", 0)
        if not chat_id and message and message.chat_id:
            chat_id = message.chat_id

        user = None
        raw_user = raw.get("user")
        if raw_user:
            user = MaxUser(
                user_id=raw_user.get("user_id", 0),
                name=raw_user.get("name", ""),
                username=raw_user.get("username"),
            )

        event = MaxEvent(
            type=event_type,
            chat_id=chat_id,
            message=message,
            user=user,
            message_id=raw.get("message_id"),
            timestamp=raw.get("timestamp", 0),
        )
        await self._dispatch_event(event)

    # -- Messaging -----------------------------------------------------------

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> MaxMessage:
        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if reply_to:
            body["link"] = {"type": "reply", "mid": reply_to}
        resp = await self._request(
            "POST", "/messages", params={"chat_id": chat_id}, json=body
        )
        raw_msg = resp.get("message", resp)
        return MaxMessage(
            mid=raw_msg.get("mid", ""),
            timestamp=raw_msg.get("timestamp", 0),
            body=raw_msg.get("body"),
            recipient=raw_msg.get("recipient"),
        )

    async def edit_message(self, message_id: str, text: str) -> None:
        await self._request(
            "PUT", "/messages", params={"message_id": message_id}, json={"text": text}
        )

    async def delete_message(self, message_id: str) -> None:
        await self._request(
            "DELETE", "/messages", params={"message_id": message_id}
        )

    # -- Chat info -----------------------------------------------------------

    async def get_chat(self, chat_id: int) -> MaxChat:
        resp = await self._request("GET", f"/chats/{chat_id}")
        return MaxChat(
            chat_id=resp.get("chat_id", chat_id),
            type=ChatType(resp.get("type", "dialog")),
            title=resp.get("title"),
            icon=resp.get("icon"),
            members_count=resp.get("members_count", 0),
            owner_id=resp.get("owner_id"),
            is_public=resp.get("is_public", False),
            description=resp.get("description"),
        )

    async def get_chat_members(self, chat_id: int) -> list[MaxUser]:
        resp = await self._request("GET", f"/chats/{chat_id}/members")
        members = []
        for m in resp.get("members", []):
            members.append(MaxUser(
                user_id=m.get("user_id", 0),
                name=m.get("name", ""),
                username=m.get("username"),
                avatar_url=m.get("avatar_url"),
                is_bot=m.get("is_bot", False),
            ))
        return members

    async def get_user_info(self, user_id: int) -> MaxUser:
        # Bot API doesn't have a direct /users/{id} endpoint.
        # We can try /chats/{id} for dialogs or store cached data.
        # For now, return minimal info.
        self.log.debug("get_user_info not fully supported in Bot API for user %d", user_id)
        return MaxUser(user_id=user_id, name=str(user_id))

    # -- Media ---------------------------------------------------------------

    async def download_media(self, url: str) -> bytes:
        session = await self._ensure_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                raise MaxAPIError("download_failed", f"HTTP {resp.status}", resp.status)
            return await resp.read()

    async def upload_media(
        self, data: bytes, filename: str, content_type: str
    ) -> str:
        """Upload media via /uploads and return the token for attaching."""
        # Step 1: get upload URL
        resp = await self._request(
            "POST", "/uploads", params={"type": self._guess_upload_type(content_type)}
        )
        upload_url = resp.get("url")
        if not upload_url:
            raise MaxAPIError("upload_failed", "No upload URL returned", 0)

        # Step 2: upload the file
        session = await self._ensure_session()
        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename, content_type=content_type)
        async with session.post(upload_url, data=form) as upload_resp:
            if upload_resp.status not in (200, 201):
                raise MaxAPIError("upload_failed", f"HTTP {upload_resp.status}", upload_resp.status)
            result = await upload_resp.json()

        # Return the token or photo info
        return result.get("token", result.get("url", ""))

    @staticmethod
    def _guess_upload_type(content_type: str) -> str:
        if content_type.startswith("image/"):
            return "photo"
        if content_type.startswith("video/"):
            return "video"
        if content_type.startswith("audio/"):
            return "audio"
        return "file"

    # -- Reactions / Read markers (Bot API has limited support) ---------------

    async def add_reaction(self, chat_id: int, message_id: str, emoji: str) -> None:
        # Bot API doesn't support reactions -- silent no-op
        self.log.debug("add_reaction not supported in Bot API")

    async def mark_as_read(self, chat_id: int, message_id: str) -> None:
        # Bot API doesn't support read markers -- silent no-op
        self.log.debug("mark_as_read not supported in Bot API")
