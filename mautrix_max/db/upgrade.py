"""Database schema migrations for mautrix-max."""

from __future__ import annotations

from mautrix.util.async_db import UpgradeTable

upgrade_table = UpgradeTable()


@upgrade_table.register(description="Initial schema")
async def upgrade_v1(conn, scheme) -> None:
    await conn.execute(
        """CREATE TABLE portal (
            max_chat_id BIGINT PRIMARY KEY,
            mxid        TEXT UNIQUE,
            name        TEXT,
            encrypted   BOOLEAN NOT NULL DEFAULT false,
            relay_user_id TEXT
        )"""
    )
    await conn.execute(
        """CREATE TABLE puppet (
            max_user_id BIGINT PRIMARY KEY,
            name        TEXT,
            username    TEXT,
            avatar_mxc  TEXT,
            name_set    BOOLEAN NOT NULL DEFAULT false,
            avatar_set  BOOLEAN NOT NULL DEFAULT false
        )"""
    )
    await conn.execute(
        """CREATE TABLE "user" (
            mxid            TEXT PRIMARY KEY,
            max_user_id     BIGINT,
            max_token       TEXT,
            connection_mode VARCHAR(10),
            bot_token       TEXT
        )"""
    )
    await conn.execute(
        """CREATE TABLE message (
            max_chat_id BIGINT NOT NULL,
            max_msg_id  TEXT NOT NULL,
            mxid        TEXT NOT NULL,
            mx_room     TEXT NOT NULL,
            timestamp   BIGINT,
            PRIMARY KEY (max_chat_id, max_msg_id)
        )"""
    )
    await conn.execute(
        "CREATE INDEX idx_message_mxid ON message (mxid)"
    )
    await conn.execute(
        """CREATE TABLE reaction (
            mxid           TEXT PRIMARY KEY,
            max_chat_id    BIGINT NOT NULL,
            max_msg_id     TEXT NOT NULL,
            max_sender_id  BIGINT NOT NULL,
            reaction       TEXT NOT NULL
        )"""
    )
