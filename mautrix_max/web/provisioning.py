"""Provisioning API for mautrix-max bridge (v3 REST + v1 WS compatibility)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Optional

from aiohttp import web
from mautrix.types import UserID

if TYPE_CHECKING:
    from ..__main__ import MaxBridge

logger = logging.getLogger("mau.prov")


class ProvisioningAPI:
    """HTTP API for provisioning Max bridge connections from the MergeChat dashboard.

    Supports two API versions:
    - v3: REST-based login flow (primary, used by MergeChat dashboard)
    - v1: WebSocket-based QR login (fallback, Telegram-style compatibility)
    """

    app: web.Application

    def __init__(self, shared_secret: str, bridge: MaxBridge) -> None:
        self.shared_secret = shared_secret
        self.bridge = bridge
        self._login_sessions: dict[str, dict[str, Any]] = {}
        self.app = web.Application()
        self._setup_routes()

    def _setup_routes(self) -> None:
        # v3 REST API routes
        self.app.router.add_get("/v3/login/flows", self.v3_get_login_flows)
        self.app.router.add_post("/v3/login/start/{flow_id}", self.v3_start_login)
        self.app.router.add_post("/v3/login/step/{login_id}", self.v3_login_step)

        # v1 WebSocket API routes (Telegram-style compatibility)
        self.app.router.add_get("/v1/user/{mxid}/login/qr", self.v1_qr_login)
        self.app.router.add_post("/v1/user/{mxid}/login/send_password", self.v1_send_password)
        self.app.router.add_post("/v1/user/{mxid}/logout", self.v1_logout)

        # Status endpoint
        self.app.router.add_get("/v1/user/{mxid}/status", self.v1_status)

    def _check_auth(self, request: web.Request) -> Optional[web.Response]:
        """Verify shared secret authorization."""
        auth = request.headers.get("Authorization", "")
        # Support both "Bearer <secret>" and raw "<secret>"
        token = auth.replace("Bearer ", "").strip()
        if token != self.shared_secret:
            return web.json_response(
                {"error": "Invalid authorization"},
                status=401,
            )
        return None

    def _get_user_id(self, request: web.Request) -> str:
        """Extract user_id from query params or path."""
        return request.query.get("user_id", request.match_info.get("mxid", ""))

    # ── v3 REST API ─────────────────────────────────────────────

    async def v3_get_login_flows(self, request: web.Request) -> web.Response:
        """GET /v3/login/flows — List available login methods."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        flows = [
            {
                "id": "bot_token",
                "name": "Bot Token",
                "description": "Connect using a Max Bot API token from @metabot",
            },
            {
                "id": "phone",
                "name": "Phone + SMS",
                "description": "Login with phone number and SMS verification code",
            },
            {
                "id": "qr",
                "name": "QR Code",
                "description": "Scan QR code with Max mobile app",
            },
        ]
        return web.json_response({"flows": flows})

    async def v3_start_login(self, request: web.Request) -> web.Response:
        """POST /v3/login/start/{flow_id} — Start a login flow."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        flow_id = request.match_info["flow_id"]
        user_id = self._get_user_id(request)
        login_id = str(uuid.uuid4())

        if flow_id == "bot_token":
            self._login_sessions[login_id] = {
                "flow": "bot_token",
                "user_id": user_id,
                "step": "token_input",
            }
            return web.json_response({
                "login_id": login_id,
                "type": "user_input",
                "user_input": {
                    "fields": [
                        {
                            "id": "token",
                            "type": "password",
                            "name": "Bot Token",
                            "description": "Get your bot token from @metabot in Max",
                        }
                    ]
                },
            })

        elif flow_id == "phone":
            self._login_sessions[login_id] = {
                "flow": "phone",
                "user_id": user_id,
                "step": "phone_input",
            }
            return web.json_response({
                "login_id": login_id,
                "type": "user_input",
                "user_input": {
                    "fields": [
                        {
                            "id": "phone",
                            "type": "phone",
                            "name": "Phone Number",
                            "description": "Enter your phone number with country code (e.g. +79001234567)",
                        }
                    ]
                },
            })

        elif flow_id == "qr":
            # Start QR auth flow via User API
            from ..max.user_client import UserMaxClient
            client = UserMaxClient(ws_url=self.bridge.config["max.ws_url"])

            try:
                qr_data = await client.start_qr_auth()
            except Exception as e:
                logger.exception("Failed to start QR auth")
                return web.json_response(
                    {"error": f"Failed to start QR auth: {e}"},
                    status=500,
                )

            self._login_sessions[login_id] = {
                "flow": "qr",
                "user_id": user_id,
                "step": "qr_scan",
                "client": client,
                "qr_data": qr_data,
            }
            return web.json_response({
                "login_id": login_id,
                "type": "display_and_wait",
                "display_and_wait": {
                    "type": "qr",
                    "data": qr_data.get("qr_url", qr_data.get("code", "")),
                    "timeout": 120,
                },
            })

        return web.json_response({"error": f"Unknown flow: {flow_id}"}, status=400)

    async def v3_login_step(self, request: web.Request) -> web.Response:
        """POST /v3/login/step/{login_id} — Submit data for a login step."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        login_id = request.match_info["login_id"]
        session = self._login_sessions.get(login_id)
        if not session:
            return web.json_response({"error": "Invalid login session"}, status=404)

        body = await request.json()
        user_id = session["user_id"]

        # ── Bot token flow ──
        if session["flow"] == "bot_token" and session["step"] == "token_input":
            token = body.get("token", "").strip()
            if not token:
                return web.json_response({"error": "Token is required"}, status=400)

            from ..user import User
            user = await User.get_by_mxid(user_id)
            if not user:
                return web.json_response({"error": "User not found"}, status=404)

            try:
                await user.login_bot(token)
            except Exception as e:
                return web.json_response(
                    {"error": f"Login failed: {e}"},
                    status=401,
                )

            del self._login_sessions[login_id]
            return web.json_response({"type": "complete", "success": True})

        # ── Phone flow: phone input ──
        if session["flow"] == "phone" and session["step"] == "phone_input":
            phone = body.get("phone", "").strip()
            if not phone:
                return web.json_response({"error": "Phone number is required"}, status=400)

            from ..max.user_client import UserMaxClient
            client = UserMaxClient(ws_url=self.bridge.config["max.ws_url"])

            try:
                resp = await client.start_phone_auth(phone)
            except Exception as e:
                return web.json_response(
                    {"error": f"Failed to start auth: {e}"},
                    status=500,
                )

            session["step"] = "code_input"
            session["client"] = client
            return web.json_response({
                "login_id": login_id,
                "type": "user_input",
                "user_input": {
                    "fields": [
                        {
                            "id": "code",
                            "type": "text",
                            "name": "SMS Code",
                            "description": "Enter the verification code sent to your phone",
                        }
                    ]
                },
            })

        # ── Phone flow: code input ──
        if session["flow"] == "phone" and session["step"] == "code_input":
            code = body.get("code", "").strip()
            if not code:
                return web.json_response({"error": "Code is required"}, status=400)

            client = session.get("client")
            if not client:
                return web.json_response({"error": "Session expired"}, status=410)

            try:
                resp = await client.check_auth_code(code)
            except Exception as e:
                return web.json_response(
                    {"error": f"Code verification failed: {e}"},
                    status=401,
                )

            if not client.auth_token:
                return web.json_response({"error": "Authentication failed"}, status=401)

            # Complete login
            from ..user import User
            user = await User.get_by_mxid(user_id)
            if not user:
                return web.json_response({"error": "User not found"}, status=404)

            user_data = resp.get("user", {})
            await user.login_user(
                auth_token=client.auth_token,
                user_id=user_data.get("user_id", 0),
            )

            del self._login_sessions[login_id]
            return web.json_response({"type": "complete", "success": True})

        # ── QR flow: poll for completion ──
        if session["flow"] == "qr" and session["step"] == "qr_scan":
            client = session.get("client")
            if not client:
                return web.json_response({"error": "Session expired"}, status=410)

            try:
                resp = await client.poll_qr_auth(timeout=5)
            except Exception:
                # Still waiting
                return web.json_response({
                    "login_id": login_id,
                    "type": "display_and_wait",
                    "status": "waiting",
                })

            if client.auth_token:
                from ..user import User
                user = await User.get_by_mxid(user_id)
                if user:
                    user_data = resp.get("user", {})
                    await user.login_user(
                        auth_token=client.auth_token,
                        user_id=user_data.get("user_id", 0),
                    )

                del self._login_sessions[login_id]
                return web.json_response({"type": "complete", "success": True})

            return web.json_response({
                "login_id": login_id,
                "type": "display_and_wait",
                "status": "waiting",
            })

        return web.json_response({"error": "Invalid session state"}, status=400)

    # ── v1 WebSocket API (Telegram-style compatibility) ─────────

    async def v1_qr_login(self, request: web.Request) -> web.WebSocketResponse:
        """WS /v1/user/{mxid}/login/qr — WebSocket QR login flow."""
        ws = web.WebSocketResponse(protocols=["net.maunium.max.auth"])
        await ws.prepare(request)

        mxid = request.match_info["mxid"]
        logger.info("v1 QR login started for %s", mxid)

        try:
            from ..max.user_client import UserMaxClient
            client = UserMaxClient(ws_url=self.bridge.config["max.ws_url"])

            qr_data = await client.start_qr_auth()
            qr_code = qr_data.get("qr_url", qr_data.get("code", ""))

            await ws.send_json({
                "code": qr_code,
                "timeout": 120,
            })

            # Poll for completion
            try:
                resp = await client.poll_qr_auth(timeout=120)
                if client.auth_token:
                    from ..user import User
                    user = await User.get_by_mxid(mxid)
                    if user:
                        user_data = resp.get("user", {})
                        await user.login_user(
                            auth_token=client.auth_token,
                            user_id=user_data.get("user_id", 0),
                        )
                    await ws.send_json({"success": True})
                else:
                    await ws.send_json({"success": False, "error": "auth_failed"})
            except Exception as e:
                await ws.send_json({"success": False, "error": str(e)})

        except Exception as e:
            logger.exception("Error in v1 QR login")
            await ws.send_json({"success": False, "error": str(e)})

        await ws.close()
        return ws

    async def v1_send_password(self, request: web.Request) -> web.Response:
        """POST /v1/user/{mxid}/login/send_password — Submit 2FA password."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # 2FA not yet implemented for Max
        return web.json_response(
            {"error": "2FA not supported for Max bridge"},
            status=501,
        )

    async def v1_logout(self, request: web.Request) -> web.Response:
        """POST /v1/user/{mxid}/logout — Disconnect the user."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        mxid = request.match_info["mxid"]

        from ..user import User
        user = await User.get_by_mxid(mxid)
        if user:
            await user.logout()

        return web.json_response({"success": True})

    async def v1_status(self, request: web.Request) -> web.Response:
        """GET /v1/user/{mxid}/status — Get bridge status for user."""
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        mxid = request.match_info["mxid"]

        from ..user import User
        user = await User.get_by_mxid(mxid, create=False)
        if not user:
            return web.json_response({"status": "not_logged_in"})

        connected = user.max_client and await user.max_client.is_connected() if user.max_client else False
        return web.json_response({
            "status": "connected" if connected else "disconnected",
            "mode": user.connection_mode,
            "max_user_id": user.max_user_id,
        })
