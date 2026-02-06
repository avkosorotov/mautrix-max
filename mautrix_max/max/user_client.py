"""Max User API client (WebSocket, reverse-engineered protocol)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

import aiohttp

from .base_client import BaseMaxClient
from .errors import AuthError, MaxAPIError
from .types import (
    ChatType,
    EventType,
    MaxChat,
    MaxEvent,
    MaxMessage,
    MaxUser,
)


# Known WebSocket opcodes (from PyMax / vkmax research)
class Opcode:
    HELLO = 6
    START_AUTH = 17
    LOGIN_BY_TOKEN = 19
    GET_CHATS = 47
    GET_CHAT = 49
    MARK_READ = 50
    SEND_MESSAGE = 64
    DELETE_MESSAGE = 66
    EDIT_MESSAGE = 67
    INCOMING_EVENT = 128
    REACT = 178


class UserMaxClient(BaseMaxClient):
    """Max User API client using WebSocket protocol.

    Connects to wss://ws-api.oneme.ru/websocket and uses a JSON-based
    protocol with opcodes for different operations.

    Auth flows:
    - Phone + SMS code
    - QR code (device_type=WEB)
    - Token re-login (after initial auth)
    """

    def __init__(
        self,
        ws_url: str = "wss://ws-api.oneme.ru/websocket",
        auth_token: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.ws_url = ws_url
        self.auth_token = auth_token
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._listen_task: Optional[asyncio.Task] = None
        self._running = False
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._device_id = str(uuid.uuid4())
        self._me: Optional[MaxUser] = None
        self._keepalive_task: Optional[asyncio.Task] = None

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _send(self, opcode: int, data: dict[str, Any] | None = None, *, seq: int | None = None) -> int:
        """Send a JSON message over WebSocket. Returns the sequence number."""
        if self._ws is None or self._ws.closed:
            raise MaxAPIError("not_connected", "WebSocket not connected")
        if seq is None:
            seq = self._next_seq()
        msg: dict[str, Any] = {"op": opcode, "seq": seq}
        if data:
            msg["d"] = data
        await self._ws.send_json(msg)
        return seq

    async def _send_and_wait(self, opcode: int, data: dict[str, Any] | None = None, timeout: float = 30) -> Any:
        """Send a message and wait for the response with matching sequence."""
        seq = self._next_seq()
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[seq] = future
        try:
            await self._send(opcode, data, seq=seq)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise MaxAPIError("timeout", f"Timeout waiting for response to opcode {opcode}")
        finally:
            self._pending.pop(seq, None)

    # -- Connection ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect to WebSocket and authenticate."""
        session = await self._ensure_session()
        self._ws = await session.ws_connect(self.ws_url)
        self._running = True

        # Wait for Hello
        hello_msg = await self._ws.receive_json()
        if hello_msg.get("op") != Opcode.HELLO:
            raise MaxAPIError("protocol_error", f"Expected Hello (op=6), got {hello_msg}")
        self.log.debug("Received Hello: %s", hello_msg)

        # Authenticate
        if self.auth_token:
            await self._login_by_token()
        else:
            raise AuthError("No auth_token provided. Use start_phone_auth() or start_qr_auth() first.")

        # Start listener + keepalive
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _login_by_token(self) -> None:
        """Authenticate using a saved auth token."""
        resp = await self._send_and_wait(Opcode.LOGIN_BY_TOKEN, {
            "token": self.auth_token,
            "device_id": self._device_id,
            "device_type": "WEB",
            "user_agent": "mautrix-max/0.1.0",
        })
        if not resp or resp.get("error"):
            raise AuthError(f"Token login failed: {resp}")
        user_data = resp.get("user", {})
        self._me = MaxUser(
            user_id=user_data.get("user_id", 0),
            name=user_data.get("name", ""),
            username=user_data.get("username"),
        )
        self.log.info("Authenticated as user: %s (ID: %d)", self._me.name, self._me.user_id)

    async def disconnect(self) -> None:
        """Disconnect WebSocket and clean up."""
        self._running = False
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    async def is_connected(self) -> bool:
        return self._running and self._ws is not None and not self._ws.closed

    # -- Auth flows ----------------------------------------------------------

    async def start_phone_auth(self, phone: str) -> dict[str, Any]:
        """Start phone + SMS authentication flow.

        Returns the server response. Caller should then call check_auth_code().
        """
        session = await self._ensure_session()
        self._ws = await session.ws_connect(self.ws_url)

        # Wait for Hello
        hello_msg = await self._ws.receive_json()
        self.log.debug("Hello: %s", hello_msg)

        resp = await self._send_and_wait(Opcode.START_AUTH, {
            "phone": phone,
            "type": "START_AUTH",
            "device_type": "WEB",
            "device_id": self._device_id,
            "user_agent": "mautrix-max/0.1.0",
        })
        return resp or {}

    async def check_auth_code(self, code: str) -> dict[str, Any]:
        """Submit SMS verification code. Returns auth tokens on success."""
        resp = await self._send_and_wait(Opcode.START_AUTH, {
            "type": "CHECK_CODE",
            "verify_code": code,
            "auth_token_type": "CHECK_CODE",
        })
        if resp and resp.get("token"):
            self.auth_token = resp["token"]
        return resp or {}

    async def start_qr_auth(self) -> dict[str, Any]:
        """Start QR code authentication flow.

        Returns a dict with QR data. The user scans the QR in the Max mobile app.
        Caller should poll for completion with poll_qr_auth().
        """
        session = await self._ensure_session()
        self._ws = await session.ws_connect(self.ws_url)

        hello_msg = await self._ws.receive_json()
        self.log.debug("Hello: %s", hello_msg)

        resp = await self._send_and_wait(Opcode.START_AUTH, {
            "type": "START_AUTH",
            "device_type": "WEB",
            "device_id": self._device_id,
            "user_agent": "mautrix-max/0.1.0",
        })
        return resp or {}

    async def poll_qr_auth(self, timeout: float = 120) -> dict[str, Any]:
        """Poll for QR auth completion. Returns auth tokens when user scans QR."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ws is None or self._ws.closed:
                raise MaxAPIError("not_connected", "WebSocket closed during QR auth")
            try:
                msg = await asyncio.wait_for(self._ws.receive_json(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if msg.get("d", {}).get("token"):
                self.auth_token = msg["d"]["token"]
                return msg.get("d", {})
            if msg.get("d", {}).get("error"):
                raise AuthError(f"QR auth failed: {msg['d']}")
        raise MaxAPIError("timeout", "QR auth timed out")

    # -- Listener ------------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Listen for incoming WebSocket messages."""
        while self._running and self._ws and not self._ws.closed:
            try:
                msg = await self._ws.receive()
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_ws_message(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    self.log.warning("WebSocket closed: %s", msg.type)
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                self.log.exception("Error in WebSocket listener")
                await asyncio.sleep(1)

    async def _handle_ws_message(self, data: dict[str, Any]) -> None:
        """Handle an incoming WebSocket message."""
        seq = data.get("seq")
        op = data.get("op")
        payload = data.get("d", {})

        # Check if this is a response to a pending request
        if seq and seq in self._pending:
            future = self._pending.pop(seq)
            if not future.done():
                future.set_result(payload)
            return

        # Handle incoming events
        if op == Opcode.INCOMING_EVENT:
            await self._handle_incoming_event(payload)

    async def _handle_incoming_event(self, payload: dict[str, Any]) -> None:
        """Parse and dispatch an incoming event from the WS stream."""
        event_type_str = payload.get("type", "")
        type_map = {
            "new_message": EventType.MESSAGE_CREATED,
            "edit_message": EventType.MESSAGE_EDITED,
            "delete_message": EventType.MESSAGE_REMOVED,
        }
        event_type = type_map.get(event_type_str)
        if not event_type:
            self.log.debug("Unknown WS event type: %s", event_type_str)
            return

        message = None
        raw_msg = payload.get("message", {})
        if raw_msg:
            sender = None
            raw_sender = raw_msg.get("sender", {})
            if raw_sender:
                sender = MaxUser(
                    user_id=raw_sender.get("user_id", 0),
                    name=raw_sender.get("name", ""),
                    username=raw_sender.get("username"),
                )
            message = MaxMessage(
                mid=str(raw_msg.get("mid", raw_msg.get("message_id", ""))),
                timestamp=raw_msg.get("timestamp", 0),
                sender=sender,
                body=raw_msg.get("body"),
                recipient=raw_msg.get("recipient"),
            )

        event = MaxEvent(
            type=event_type,
            chat_id=payload.get("chat_id", 0),
            message=message,
            message_id=payload.get("message_id"),
            timestamp=payload.get("timestamp", int(time.time())),
        )
        await self._dispatch_event(event)

    async def _keepalive_loop(self) -> None:
        """Send periodic pings to keep the connection alive."""
        while self._running:
            try:
                await asyncio.sleep(30)
                if self._ws and not self._ws.closed:
                    await self._ws.ping()
            except asyncio.CancelledError:
                break
            except Exception:
                self.log.debug("Keepalive ping failed")

    # -- Messaging -----------------------------------------------------------

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> MaxMessage:
        data: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to:
            data["reply_to"] = reply_to
        if attachments:
            data["attachments"] = attachments
        resp = await self._send_and_wait(Opcode.SEND_MESSAGE, data)
        return MaxMessage(
            mid=str(resp.get("mid", "")),
            timestamp=resp.get("timestamp", 0),
            body={"text": text},
        )

    async def edit_message(self, message_id: str, text: str) -> None:
        await self._send_and_wait(Opcode.EDIT_MESSAGE, {
            "message_id": message_id,
            "text": text,
        })

    async def delete_message(self, message_id: str) -> None:
        await self._send_and_wait(Opcode.DELETE_MESSAGE, {
            "message_id": message_id,
        })

    # -- Chat info -----------------------------------------------------------

    async def get_chat(self, chat_id: int) -> MaxChat:
        resp = await self._send_and_wait(Opcode.GET_CHAT, {"chat_id": chat_id})
        return MaxChat(
            chat_id=resp.get("chat_id", chat_id),
            type=ChatType(resp.get("type", "dialog")),
            title=resp.get("title"),
            members_count=resp.get("members_count", 0),
        )

    async def get_chat_members(self, chat_id: int) -> list[MaxUser]:
        resp = await self._send_and_wait(Opcode.GET_CHAT, {
            "chat_id": chat_id,
            "include_members": True,
        })
        members = []
        for m in resp.get("members", []):
            members.append(MaxUser(
                user_id=m.get("user_id", 0),
                name=m.get("name", ""),
                username=m.get("username"),
            ))
        return members

    async def get_user_info(self, user_id: int) -> MaxUser:
        # User API: we can get user info from chat or cached data
        resp = await self._send_and_wait(Opcode.GET_CHAT, {"user_id": user_id})
        return MaxUser(
            user_id=resp.get("user_id", user_id),
            name=resp.get("name", str(user_id)),
            username=resp.get("username"),
            avatar_url=resp.get("avatar_url"),
        )

    # -- Media ---------------------------------------------------------------

    async def download_media(self, url: str) -> bytes:
        session = await self._ensure_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                raise MaxAPIError("download_failed", f"HTTP {resp.status}", resp.status)
            return await resp.read()

    async def upload_media(self, data: bytes, filename: str, content_type: str) -> str:
        # User API media upload -- typically via separate HTTP endpoint
        session = await self._ensure_session()
        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename, content_type=content_type)
        async with session.post(
            "https://platform-api.max.ru/uploads",
            data=form,
        ) as resp:
            if resp.status not in (200, 201):
                raise MaxAPIError("upload_failed", f"HTTP {resp.status}", resp.status)
            result = await resp.json()
            return result.get("token", result.get("url", ""))

    # -- Reactions / Read markers --------------------------------------------

    async def add_reaction(self, chat_id: int, message_id: str, emoji: str) -> None:
        await self._send(Opcode.REACT, {
            "chat_id": chat_id,
            "message_id": message_id,
            "reaction": emoji,
        })

    async def mark_as_read(self, chat_id: int, message_id: str) -> None:
        await self._send(Opcode.MARK_READ, {
            "chat_id": chat_id,
            "message_id": message_id,
        })
