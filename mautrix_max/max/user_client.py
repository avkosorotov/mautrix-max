"""Max User API client (WebSocket, reverse-engineered protocol).

Protocol format (ver 11):
    Request:  {ver: 11, cmd: 0, seq: N, opcode: N, payload: {...}}
    Response: {ver: 11, cmd: 1, seq: N, opcode: N, payload: {...}}
    Ack:      {ver: 11, cmd: 2, seq: N, opcode: N}
    Error:    {ver: 11, cmd: 3, seq: N, opcode: N, payload: {code: N, message: "..."}}

cmd values: 0=request, 1=response, 2=ack, 3=error
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
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

PROTOCOL_VERSION = 11
APP_VERSION = "26.2.2"

# Headers required by ws-api.oneme.ru to accept WebSocket upgrade (403 without them)
WS_HEADERS = {
    "Origin": "https://web.max.ru",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


class Opcode:
    """WebSocket opcodes reverse-engineered from web.max.ru client."""

    # Connection
    PING = 0
    HEARTBEAT = 1
    INIT_CONNECTION = 5
    INIT_SESSION = 6

    # Auth — phone + SMS
    START_PHONE_AUTH = 17  # {phone, type: "START_AUTH"/"RESEND", language}
    CHECK_CODE = 18        # {token, verifyCode, authTokenType: "CHECK_CODE"}

    # Auth — token re-login
    LOGIN_BY_TOKEN = 19    # {token, chatsCount, lastLogin, ...sync params}

    # Auth — results
    LOGOUT_RESULT = 20
    AUTH_MIGRATE = 23
    AUTH_REFRESH = 25
    AUTH_RESPONSE = 26

    # Contacts
    GET_CONTACTS = 32

    # Chats
    GET_CHATS = 48
    GET_CHAT = 49
    MARK_READ = 50
    GET_CHAT_HISTORY = 53

    # Messages
    SEND_MESSAGE = 64
    DELETE_MESSAGE = 66
    EDIT_MESSAGE = 67

    # Media / files
    GET_UPLOAD_URL = 86
    GET_FILE = 88

    # Presence
    GET_PRESENCE = 35

    # Stickers
    SEND_STICKER = 56

    # Reactions
    REACT = 178

    # Incoming events from server
    INCOMING_MESSAGE = 128
    INCOMING_EDIT = 129
    INCOMING_DELETE = 130
    INCOMING_READ = 131
    INCOMING_TYPING = 132

    # 2FA
    TWO_FA_PASSWORD = 115  # {trackId, password}

    # QR auth
    QR_GENERATE = 288      # no payload → {trackId, qrLink, expiresAt, pollingInterval}
    QR_POLL = 289          # {trackId} → {status: {loginAvailable, expiresAt}}
    QR_CONFIRM = 291       # {trackId} → {tokenAttrs, profile} or {passwordChallenge}

    # Folders
    GET_FOLDER = 273
    SYNC_FOLDERS = 272

    # Calls
    CALL_RELATED = 257

    # Service
    LOGOUT = 101           # opcode sent by client to log out


class Cmd:
    """Message cmd field values."""
    REQUEST = 0
    RESPONSE = 1
    ACK = 2
    ERROR = 3


# Opcodes that must be sent immediately even before login completes
IMMEDIATE_OPCODES = frozenset([
    Opcode.INIT_CONNECTION, Opcode.INIT_SESSION,
    Opcode.START_PHONE_AUTH, Opcode.CHECK_CODE, Opcode.LOGIN_BY_TOKEN,
    Opcode.QR_GENERATE, Opcode.QR_POLL, Opcode.QR_CONFIRM,
    Opcode.AUTH_MIGRATE, Opcode.LOGOUT,
    Opcode.TWO_FA_PASSWORD,
])


class UserMaxClient(BaseMaxClient):
    """Max User API client using WebSocket protocol.

    Connects to wss://ws-api.oneme.ru/websocket and uses a JSON-based
    protocol (ver 11) with opcodes for different operations.

    Auth flows:
    - Phone + SMS code (opcodes 17 → 18)
    - QR code (opcodes 288 → 289 → 291)
    - Token re-login (opcode 19)
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
        self._viewer_id: Optional[int] = None
        # For phone auth flow — token returned by START_PHONE_AUTH
        self._auth_flow_token: Optional[str] = None

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    # -- Wire protocol -------------------------------------------------------

    async def _send_raw(
        self,
        cmd: int,
        opcode: int,
        payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> int:
        """Send a JSON message in the Max wire format. Returns seq number."""
        if self._ws is None or self._ws.closed:
            raise MaxAPIError("not_connected", "WebSocket not connected")
        if seq is None:
            seq = self._next_seq()
        msg: dict[str, Any] = {
            "ver": PROTOCOL_VERSION,
            "cmd": cmd,
            "seq": seq,
            "opcode": opcode,
        }
        if payload is not None:
            msg["payload"] = payload
        await self._ws.send_json(msg)
        return seq

    async def _send(
        self,
        opcode: int,
        payload: dict[str, Any] | None = None,
        *,
        seq: int | None = None,
    ) -> int:
        """Send a request (cmd=0). Returns seq number."""
        return await self._send_raw(Cmd.REQUEST, opcode, payload, seq=seq)

    async def _send_and_wait(
        self,
        opcode: int,
        payload: dict[str, Any] | None = None,
        timeout: float = 30,
    ) -> Any:
        """Send a request and wait for the matching response."""
        seq = self._next_seq()
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[seq] = future
        try:
            await self._send(opcode, payload, seq=seq)
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise MaxAPIError(
                "timeout",
                f"Timeout waiting for response to opcode {opcode}",
            )
        finally:
            self._pending.pop(seq, None)

    def _build_user_agent(self) -> dict[str, Any]:
        """Build the userAgent payload for INIT_SESSION."""
        return {
            "deviceType": "WEB",
            "locale": "ru",
            "deviceLocale": "ru",
            "osVersion": platform.platform(),
            "deviceName": "mautrix-max",
            "headerUserAgent": "mautrix-max/0.1.0",
            "appVersion": APP_VERSION,
            "screen": "1920x1080 1.0x",
            "timezone": "Europe/Moscow",
        }

    # -- Connection ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect to WebSocket and authenticate with saved token."""
        session = await self._ensure_session()
        self._ws = await session.ws_connect(self.ws_url, headers=WS_HEADERS)
        self._running = True

        # Step 1: Start listener FIRST — _send_and_wait depends on it to receive responses
        self._listen_task = asyncio.create_task(self._listen_loop())

        # Step 2: Send INIT_SESSION (client sends first, no waiting for HELLO)
        init_resp = await self._send_and_wait(Opcode.INIT_SESSION, {
            "userAgent": self._build_user_agent(),
            "deviceId": self._device_id,
        })
        self.log.debug("Session initialized: %s", init_resp)

        # Step 3: Authenticate with saved token
        if self.auth_token:
            await self._login_by_token()
        else:
            raise AuthError(
                "No auth_token provided. "
                "Use start_phone_auth() or start_qr_auth() first."
            )

        # Step 4: Start keepalive (listener already running)
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _login_by_token(self) -> None:
        """Authenticate using a saved auth token (opcode 19)."""
        resp = await self._send_and_wait(Opcode.LOGIN_BY_TOKEN, {
            "token": self.auth_token,
            "chatsCount": 40,
            "lastLogin": 0,
        })
        if not resp:
            raise AuthError("Token login failed: empty response")
        # Response may contain token + profile
        token = resp.get("token")
        profile = resp.get("profile", {})
        contact = profile.get("contact", {})
        if token:
            self.auth_token = token
        if contact.get("id"):
            self._viewer_id = contact["id"]
            self._me = MaxUser(
                user_id=contact["id"],
                name=contact.get("name", ""),
                username=contact.get("username"),
            )
            self.log.info(
                "Authenticated as user: %s (ID: %d)",
                self._me.name,
                self._me.user_id,
            )
        else:
            self.log.info("Token login succeeded (no profile in response)")

    async def _close_ws(self) -> None:
        """Close WebSocket and cancel listener (used on auth failure cleanup)."""
        self._running = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            self._listen_task = None
        if self._ws and not self._ws.closed:
            await self._ws.close()

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

    # -- Auth: Phone + SMS ---------------------------------------------------

    async def _init_auth_session(self) -> None:
        """Open WS, start listener, and send INIT_SESSION. Used by auth flows."""
        session = await self._ensure_session()
        self._ws = await session.ws_connect(self.ws_url, headers=WS_HEADERS)
        self._running = True
        # Start listener so server heartbeats are handled during auth
        self._listen_task = asyncio.create_task(self._listen_loop())

        await self._send_and_wait(Opcode.INIT_SESSION, {
            "userAgent": self._build_user_agent(),
            "deviceId": self._device_id,
        })

    async def start_phone_auth(self, phone: str) -> dict[str, Any]:
        """Start phone + SMS authentication flow (opcode 17).

        Returns server response with {token, codeLength, requestCountLeft, altActionDuration}.
        Caller should then call check_auth_code() with the SMS code.
        """
        try:
            await self._init_auth_session()
        except Exception:
            await self._close_ws()
            raise

        try:
            resp = await self._send_and_wait(Opcode.START_PHONE_AUTH, {
                "phone": phone,
                "type": "START_AUTH",
                "language": "ru",
            })
        except Exception:
            await self._close_ws()
            raise
        if resp and resp.get("token"):
            self._auth_flow_token = resp["token"]
        return resp or {}

    async def check_auth_code(self, code: str) -> dict[str, Any]:
        """Submit SMS verification code (opcode 18).

        Returns auth result with tokenAttrs.LOGIN.token and profile.
        """
        resp = await self._send_and_wait(Opcode.CHECK_CODE, {
            "token": self._auth_flow_token,
            "verifyCode": code,
            "authTokenType": "CHECK_CODE",
        })
        if resp:
            token_attrs = resp.get("tokenAttrs", {})
            login_token = token_attrs.get("LOGIN", {}).get("token")
            if login_token:
                self.auth_token = login_token
            profile = resp.get("profile", {})
            contact = profile.get("contact", {})
            if contact.get("id"):
                self._viewer_id = contact["id"]
                self._me = MaxUser(
                    user_id=contact["id"],
                    name=contact.get("name", ""),
                    username=contact.get("username"),
                )
        return resp or {}

    # -- Auth: QR code -------------------------------------------------------

    async def start_qr_auth(self) -> dict[str, Any]:
        """Start QR code authentication flow (opcode 288).

        Returns {trackId, qrLink, expiresAt, pollingInterval}.
        Caller should display qrLink as QR and poll with poll_qr_auth().
        """
        try:
            await self._init_auth_session()
        except Exception:
            await self._close_ws()
            raise

        try:
            resp = await self._send_and_wait(Opcode.QR_GENERATE)
        except Exception:
            await self._close_ws()
            raise
        if resp and resp.get("trackId"):
            self._auth_flow_token = resp["trackId"]
        return resp or {}

    async def poll_qr_auth(self, timeout: float = 120) -> dict[str, Any]:
        """Poll for QR auth completion (opcodes 289 + 291).

        Returns auth result when user scans QR on their phone.
        """
        if not self._auth_flow_token:
            raise MaxAPIError("no_track_id", "No trackId from QR generation")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ws is None or self._ws.closed:
                raise MaxAPIError("not_connected", "WebSocket closed during QR auth")

            try:
                # Check QR status (opcode 289)
                status_resp = await self._send_and_wait(
                    Opcode.QR_POLL,
                    {"trackId": self._auth_flow_token},
                    timeout=10,
                )
            except MaxAPIError:
                await asyncio.sleep(2)
                continue

            status = status_resp.get("status", {})
            if status.get("loginAvailable"):
                # QR was scanned — confirm login (opcode 291)
                confirm_resp = await self._send_and_wait(
                    Opcode.QR_CONFIRM,
                    {"trackId": self._auth_flow_token},
                )
                # Extract auth token
                token_attrs = confirm_resp.get("tokenAttrs", {})
                login_token = token_attrs.get("LOGIN", {}).get("token")
                if login_token:
                    self.auth_token = login_token
                profile = confirm_resp.get("profile", {})
                contact = profile.get("contact", {})
                if contact.get("id"):
                    self._viewer_id = contact["id"]
                    self._me = MaxUser(
                        user_id=contact["id"],
                        name=contact.get("name", ""),
                        username=contact.get("username"),
                    )
                return confirm_resp

            # Update expiresAt if provided
            if status.get("expiresAt"):
                expires_at = status["expiresAt"]
                now_ms = int(time.time() * 1000)
                if expires_at < now_ms:
                    raise MaxAPIError("qr_expired", "QR code expired")

            await asyncio.sleep(3)

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
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    self.log.warning("WebSocket closed: %s", msg.type)
                    break
            except asyncio.CancelledError:
                break
            except Exception:
                self.log.exception("Error in WebSocket listener")
                await asyncio.sleep(1)

    async def _handle_ws_message(self, data: dict[str, Any]) -> None:
        """Handle an incoming WebSocket message (ver 11 protocol)."""
        cmd = data.get("cmd")
        seq = data.get("seq")
        opcode = data.get("opcode")
        payload = data.get("payload", {})

        if cmd == Cmd.REQUEST:
            # Incoming request from server (events, heartbeat)
            if opcode == Opcode.HEARTBEAT:
                # Respond to server heartbeat
                await self._send_raw(
                    Cmd.RESPONSE, Opcode.HEARTBEAT, seq=seq,
                )
                return
            if opcode == Opcode.INCOMING_MESSAGE:
                # ACK the incoming message
                raw_msg = payload.get("message", payload)
                await self._send_raw(
                    Cmd.RESPONSE,
                    Opcode.INCOMING_MESSAGE,
                    {
                        "chatId": payload.get("chatId"),
                        "messageId": raw_msg.get("id", raw_msg.get("messageId")) if isinstance(raw_msg, dict) else None,
                    },
                    seq=seq,
                )
                try:
                    await self._handle_incoming_event(opcode, payload)
                except Exception:
                    self.log.exception("Error handling incoming message")
                return
            # Other incoming events (edit, delete, typing, etc.)
            if opcode in (
                Opcode.INCOMING_EDIT,
                Opcode.INCOMING_DELETE,
                Opcode.INCOMING_READ,
                Opcode.INCOMING_TYPING,
            ):
                try:
                    await self._handle_incoming_event(opcode, payload)
                except Exception:
                    self.log.exception("Error handling incoming event opcode=%s", opcode)
                return

        elif cmd == Cmd.RESPONSE:
            # Response to our request
            if seq is not None and seq in self._pending:
                future = self._pending.pop(seq)
                if not future.done():
                    future.set_result(payload)
                return

        elif cmd == Cmd.ERROR:
            # Error response
            if seq is not None and seq in self._pending:
                future = self._pending.pop(seq)
                if not future.done():
                    error_code = payload.get("code", "unknown")
                    error_msg = payload.get("message", "Unknown error")
                    future.set_exception(
                        MaxAPIError(str(error_code), error_msg)
                    )
                return
            self.log.warning("WS error (no pending): opcode=%s payload=%s", opcode, payload)

        elif cmd == Cmd.ACK:
            # Ack — just ignore
            return

    async def _handle_incoming_event(
        self, opcode: int, payload: dict[str, Any]
    ) -> None:
        """Parse and dispatch an incoming event from the WS stream."""
        opcode_to_event = {
            Opcode.INCOMING_MESSAGE: EventType.MESSAGE_CREATED,
            Opcode.INCOMING_EDIT: EventType.MESSAGE_EDITED,
            Opcode.INCOMING_DELETE: EventType.MESSAGE_REMOVED,
        }
        event_type = opcode_to_event.get(opcode)
        if not event_type:
            self.log.debug("Unhandled incoming opcode: %s", opcode)
            return

        self.log.debug("Incoming event opcode=%s payload=%s", opcode, payload)

        message = None
        raw_msg = payload.get("message", payload)
        if raw_msg and isinstance(raw_msg, dict):
            sender = None
            raw_sender = raw_msg.get("sender", raw_msg.get("from"))
            if raw_sender is not None:
                if isinstance(raw_sender, int):
                    # User API sends sender as plain int (user_id)
                    sender = MaxUser(user_id=raw_sender, name=str(raw_sender))
                elif isinstance(raw_sender, dict):
                    sender = MaxUser(
                        user_id=raw_sender.get("userId", raw_sender.get("user_id", 0)),
                        name=raw_sender.get("name", raw_sender.get("firstName", "")),
                        username=raw_sender.get("username"),
                    )
            message = MaxMessage(
                mid=str(raw_msg.get("mid", raw_msg.get("id", raw_msg.get("messageId", "")))),
                timestamp=raw_msg.get("timestamp", 0),
                sender=sender,
                body=raw_msg.get("body", raw_msg.get("text")),
                recipient=raw_msg.get("recipient"),
            )

        chat_id = payload.get("chatId", payload.get("chat_id",
                  raw_msg.get("chatId", 0) if isinstance(raw_msg, dict) else 0))
        event = MaxEvent(
            type=event_type,
            chat_id=chat_id,
            message=message,
            message_id=payload.get("messageId", payload.get("message_id")),
            timestamp=payload.get("timestamp", int(time.time())),
        )
        await self._dispatch_event(event)

    async def _keepalive_loop(self) -> None:
        """Send periodic heartbeats (opcode 1) every 30s."""
        while self._running:
            try:
                await asyncio.sleep(30)
                if self._ws and not self._ws.closed:
                    await self._send(
                        Opcode.HEARTBEAT,
                        {"interactive": True},
                    )
            except asyncio.CancelledError:
                break
            except Exception:
                self.log.debug("Keepalive heartbeat failed")

    # -- Messaging -----------------------------------------------------------

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_to: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
    ) -> MaxMessage:
        data: dict[str, Any] = {"chatId": chat_id, "text": text}
        if reply_to:
            data["replyTo"] = reply_to
        if attachments:
            data["attachments"] = attachments
        resp = await self._send_and_wait(Opcode.SEND_MESSAGE, data)
        return MaxMessage(
            mid=str(resp.get("id", resp.get("mid", ""))),
            timestamp=resp.get("timestamp", 0),
            body={"text": text},
        )

    async def edit_message(self, message_id: str, text: str) -> None:
        await self._send_and_wait(Opcode.EDIT_MESSAGE, {
            "messageId": message_id,
            "text": text,
        })

    async def delete_message(self, message_id: str) -> None:
        await self._send_and_wait(Opcode.DELETE_MESSAGE, {
            "messageId": message_id,
        })

    # -- Chat info -----------------------------------------------------------

    async def get_chat(self, chat_id: int) -> MaxChat:
        resp = await self._send_and_wait(Opcode.GET_CHAT, {"chatIds": [chat_id]})
        chats = resp.get("chats", [])
        if chats:
            c = chats[0]
            return MaxChat(
                chat_id=c.get("chatId", chat_id),
                type=ChatType(c.get("type", "dialog")),
                title=c.get("title"),
                members_count=c.get("membersCount", 0),
            )
        return MaxChat(chat_id=chat_id, type=ChatType.DIALOG, title=None, members_count=0)

    async def get_chat_members(self, chat_id: int) -> list[MaxUser]:
        resp = await self._send_and_wait(Opcode.GET_CHAT, {
            "chatIds": [chat_id],
        })
        members = []
        chats = resp.get("chats", [])
        if chats:
            for m in chats[0].get("members", []):
                members.append(MaxUser(
                    user_id=m.get("userId", m.get("user_id", 0)),
                    name=m.get("name", ""),
                    username=m.get("username"),
                ))
        return members

    async def get_user_info(self, user_id: int) -> MaxUser:
        resp = await self._send_and_wait(Opcode.GET_CONTACTS, {
            "contactIds": [user_id],
        })
        contacts = resp.get("contacts", [])
        if contacts:
            c = contacts[0]
            return MaxUser(
                user_id=c.get("id", user_id),
                name=c.get("name", str(user_id)),
                username=c.get("username"),
                avatar_url=c.get("avatarUrl"),
            )
        return MaxUser(user_id=user_id, name=str(user_id))

    # -- Media ---------------------------------------------------------------

    async def download_media(self, url: str) -> bytes:
        session = await self._ensure_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                raise MaxAPIError("download_failed", f"HTTP {resp.status}", resp.status)
            return await resp.read()

    async def upload_media(self, data: bytes, filename: str, content_type: str) -> str:
        session = await self._ensure_session()
        form = aiohttp.FormData()
        form.add_field("data", data, filename=filename, content_type=content_type)
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
            "chatId": chat_id,
            "messageId": message_id,
            "reaction": emoji,
        })

    async def mark_as_read(self, chat_id: int, message_id: str) -> None:
        await self._send(Opcode.MARK_READ, {
            "chatId": chat_id,
            "messageId": message_id,
        })
