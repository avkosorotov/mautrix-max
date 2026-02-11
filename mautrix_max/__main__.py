"""mautrix-max — Matrix-Max Messenger puppeting bridge."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import aiohttp
from mautrix.bridge import Bridge
from mautrix.types import RoomID, UserID

from . import __version__
from .config import Config
from .db import DBMessage, DBPortal, DBPuppet, DBReaction, DBUser, upgrade_table
from .matrix import MatrixHandler
from .portal import Portal
from .puppet import Puppet
from .user import User
from .web.provisioning import ProvisioningAPI


class MaxBridge(Bridge):
    name = "mautrix-max"
    module = "mautrix_max"
    command = "python -m mautrix_max"
    description = "A Matrix-Max Messenger puppeting bridge."
    repo_url = "https://github.com/avkosorotov/mautrix-max"
    version = __version__
    command_prefix = "!max"
    config_class = Config
    matrix_class = MatrixHandler
    upgrade_table = upgrade_table

    config: Config
    provisioning_api: ProvisioningAPI

    def prepare_db(self) -> None:
        super().prepare_db()
        # Assign database instance to all DB model classes
        DBPortal.db = self.db
        DBPuppet.db = self.db
        DBUser.db = self.db
        DBMessage.db = self.db
        DBReaction.db = self.db

    async def _check_license(self) -> tuple[bool, str]:
        """Check MergeChat license. Returns (valid, error_message)."""
        license_key = self.config["mergechat.license_key"]
        server_id = self.config["mergechat.server_id"]
        api_url = self.config["mergechat.api_url"]

        if not license_key or not server_id:
            return False, "license_key and server_id are required"

        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"{api_url}/license/verify",
                    json={
                        "license_key": license_key,
                        "server_id": server_id,
                        "module": "max",
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                )
                if resp.status != 200:
                    body = await resp.text()
                    return False, f"HTTP {resp.status}: {body}"
                data = await resp.json()
                if not data.get("valid"):
                    return False, data.get("error", "unknown")
                allowed = data.get("allowed_bridges", [])
                # Empty list = all bridges allowed
                if allowed and "max" not in allowed:
                    return False, f"Module 'max' not in allowed bridges: {', '.join(allowed)}"
        except aiohttp.ClientError as e:
            return False, f"Connection error: {e}"

        return True, ""

    async def _verify_license(self) -> None:
        """Verify MergeChat license on startup. Exits on failure."""
        valid, error = await self._check_license()
        if not valid:
            self.log.fatal("MergeChat license verification failed: %s", error)
            sys.exit(1)
        self.log.info("MergeChat license verified successfully")

    def prepare_bridge(self) -> None:
        super().prepare_bridge()
        cfg = self.config["bridge.provisioning"]
        if cfg.get("enabled", True):
            self.provisioning_api = ProvisioningAPI(
                shared_secret=cfg["shared_secret"],
                bridge=self,
            )
            self.az.app.add_subapp(
                cfg.get("prefix", "/_matrix/provision"),
                self.provisioning_api.app,
            )
        # Schedule periodic license check (every 24 hours)
        self.loop.create_task(self._periodic_license_check())

    async def _periodic_license_check(self) -> None:
        """Re-verify license every 24 hours with 72-hour grace period."""
        while True:
            await asyncio.sleep(24 * 3600)
            valid, error = await self._check_license()
            if valid:
                self.log.debug("Periodic license check passed")
                continue
            self.log.error(
                "License verification failed: %s. "
                "Bridge will shut down in 72 hours if not resolved.",
                error,
            )
            # Grace period: re-check every 12 hours for 72 hours
            grace_end = asyncio.get_event_loop().time() + 72 * 3600
            resolved = False
            while asyncio.get_event_loop().time() < grace_end:
                await asyncio.sleep(12 * 3600)
                valid, _ = await self._check_license()
                if valid:
                    self.log.info("License re-verified successfully during grace period")
                    resolved = True
                    break
            if not resolved:
                self.log.fatal("License still invalid after 72-hour grace period. Shutting down.")
                sys.exit(1)

    async def start(self) -> None:
        await self._verify_license()
        User.init_cls(self)
        Puppet.init_cls(self)
        Portal.init_cls(self)
        await super().start()
        # Start any logged-in users
        users = await User.all_logged_in()
        for user in users:
            asyncio.create_task(user.connect())

    async def stop(self) -> None:
        for user in User.by_mxid.values():
            await user.disconnect()
        await super().stop()

    async def get_user(self, user_id: UserID, create: bool = True) -> User:
        return await User.get_by_mxid(user_id, create=create)

    async def get_portal(self, room_id: RoomID) -> Portal | None:
        return await Portal.get_by_mxid(room_id)

    async def get_puppet(self, user_id: UserID, create: bool = False) -> Puppet | None:
        return await Puppet.get_by_mxid(user_id)

    async def get_double_puppet(self, user_id: UserID) -> Puppet | None:
        return None  # Double puppeting not yet implemented

    async def count_logged_in_users(self) -> int:
        users = await User.all_logged_in()
        return len(users)

    def is_bridge_ghost(self, user_id: UserID) -> bool:
        prefix = self.config.username_template.split('{userid}')[0]
        localpart = str(user_id).split(':')[0][1:]  # Remove @ and domain
        return localpart.startswith(prefix)


def main() -> None:
    MaxBridge().run()


if __name__ == "__main__":
    main()
