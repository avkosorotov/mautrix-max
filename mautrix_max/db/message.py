"""Database model for Message (Max message <-> Matrix event mapping)."""

from __future__ import annotations

from typing import ClassVar, Optional

from mautrix.util.async_db import Database


class Message:
    db: ClassVar[Database]

    max_chat_id: int
    max_msg_id: str
    mxid: str
    mx_room: str
    timestamp: Optional[int]

    def __init__(
        self,
        max_chat_id: int,
        max_msg_id: str,
        mxid: str,
        mx_room: str,
        timestamp: Optional[int] = None,
    ) -> None:
        self.max_chat_id = max_chat_id
        self.max_msg_id = max_msg_id
        self.mxid = mxid
        self.mx_room = mx_room
        self.timestamp = timestamp

    @classmethod
    def _from_row(cls, row) -> Message:
        return cls(
            max_chat_id=row["max_chat_id"],
            max_msg_id=row["max_msg_id"],
            mxid=row["mxid"],
            mx_room=row["mx_room"],
            timestamp=row["timestamp"],
        )

    @classmethod
    async def get_by_max_msg_id(cls, chat_id: int, msg_id: str) -> Optional[Message]:
        row = await cls.db.fetchrow(
            "SELECT * FROM message WHERE max_chat_id=$1 AND max_msg_id=$2",
            chat_id, msg_id,
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def get_by_mxid(cls, mxid: str) -> Optional[Message]:
        row = await cls.db.fetchrow(
            "SELECT * FROM message WHERE mxid=$1", mxid
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def insert(
        cls,
        max_chat_id: int,
        max_msg_id: str,
        mxid: str,
        mx_room: str,
        timestamp: Optional[int] = None,
    ) -> Message:
        await cls.db.execute(
            "INSERT INTO message (max_chat_id, max_msg_id, mxid, mx_room, timestamp) "
            "VALUES ($1, $2, $3, $4, $5)",
            max_chat_id, max_msg_id, mxid, mx_room, timestamp,
        )
        return cls(max_chat_id, max_msg_id, mxid, mx_room, timestamp)

    @classmethod
    async def count_by_chat(cls, chat_id: int) -> int:
        row = await cls.db.fetchrow(
            "SELECT COUNT(*) AS cnt FROM message WHERE max_chat_id=$1", chat_id
        )
        return row["cnt"] if row else 0

    @classmethod
    async def delete_by_max_msg_id(cls, chat_id: int, msg_id: str) -> None:
        await cls.db.execute(
            "DELETE FROM message WHERE max_chat_id=$1 AND max_msg_id=$2",
            chat_id, msg_id,
        )
