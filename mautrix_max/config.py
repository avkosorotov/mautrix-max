"""Bridge configuration for mautrix-max."""

from __future__ import annotations

from typing import Any

from mautrix.bridge.config import BaseBridgeConfig
from mautrix.client import Client
from mautrix.types import UserID


class Config(BaseBridgeConfig):
    def do_update(self, helper: Any) -> None:
        super().do_update(helper)

        # Max connection settings
        helper.copy("max.connection_mode")
        helper.copy("max.bot_token")
        helper.copy("max.api_url")
        helper.copy("max.ws_url")
        helper.copy("max.polling_timeout")

        # Bridge settings
        helper.copy("bridge.username_template")
        helper.copy("bridge.displayname_template")
        helper.copy("bridge.command_prefix")
        helper.copy("bridge.permissions")
        helper.copy("bridge.relay.enabled")
        helper.copy("bridge.provisioning.enabled")
        helper.copy("bridge.provisioning.prefix")
        helper.copy("bridge.provisioning.shared_secret")

        # MergeChat license
        helper.copy("mergechat.license_key")
        helper.copy("mergechat.server_id")
        helper.copy("mergechat.api_url")

    def _get_permissions(self, key: str) -> str:
        permissions: dict[str, str] = self["bridge.permissions"]
        if key in permissions:
            return permissions[key]
        # Check for domain wildcards
        if ":" in key:
            domain = key.split(":", 1)[1]
            if domain in permissions:
                return permissions[domain]
        if "*" in permissions:
            return permissions["*"]
        return ""

    def get_permissions(self, mxid: UserID) -> str:
        return self._get_permissions(mxid)

    @property
    def username_template(self) -> str:
        return self["bridge.username_template"]

    @property
    def displayname_template(self) -> str:
        return self["bridge.displayname_template"]

    @property
    def nameid_template(self) -> str:
        return self.username_template
