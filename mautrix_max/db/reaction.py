"""Database model for Reaction (Max reaction <-> Matrix reaction mapping)."""

from __future__ import annotations

from typing import ClassVar, Optional

from mautrix.util.async_db import Database


class Reaction:
    db: ClassVar[Database]

    mxid: str  # Matrix event ID of the reaction
    max_chat_id: int
    max_msg_id: str  # Max message being reacted to
    max_sender_id: int  # Max user who sent the reaction
    reaction: str  # Emoji

    def __init__(
        self,
        mxid: str,
        max_chat_id: int,
        max_msg_id: str,
        max_sender_id: int,
        reaction: str,
    ) -> None:
        self.mxid = mxid
        self.max_chat_id = max_chat_id
        self.max_msg_id = max_msg_id
        self.max_sender_id = max_sender_id
        self.reaction = reaction

    @classmethod
    def _from_row(cls, row) -> Reaction:
        return cls(
            mxid=row["mxid"],
            max_chat_id=row["max_chat_id"],
            max_msg_id=row["max_msg_id"],
            max_sender_id=row["max_sender_id"],
            reaction=row["reaction"],
        )

    @classmethod
    async def get_by_mxid(cls, mxid: str) -> Optional[Reaction]:
        row = await cls.db.fetchrow(
            "SELECT * FROM reaction WHERE mxid=$1", mxid
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def get_by_max_ids(
        cls, chat_id: int, msg_id: str, sender_id: int
    ) -> Optional[Reaction]:
        row = await cls.db.fetchrow(
            "SELECT * FROM reaction "
            "WHERE max_chat_id=$1 AND max_msg_id=$2 AND max_sender_id=$3",
            chat_id, msg_id, sender_id,
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def insert(
        cls,
        mxid: str,
        max_chat_id: int,
        max_msg_id: str,
        max_sender_id: int,
        reaction: str,
    ) -> Reaction:
        await cls.db.execute(
            "INSERT INTO reaction (mxid, max_chat_id, max_msg_id, max_sender_id, reaction) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (mxid) DO UPDATE SET reaction=$5",
            mxid, max_chat_id, max_msg_id, max_sender_id, reaction,
        )
        return cls(mxid, max_chat_id, max_msg_id, max_sender_id, reaction)

    @classmethod
    async def delete_by_mxid(cls, mxid: str) -> None:
        await cls.db.execute("DELETE FROM reaction WHERE mxid=$1", mxid)

    @classmethod
    async def delete_by_max_ids(
        cls, chat_id: int, msg_id: str, sender_id: int
    ) -> None:
        await cls.db.execute(
            "DELETE FROM reaction "
            "WHERE max_chat_id=$1 AND max_msg_id=$2 AND max_sender_id=$3",
            chat_id, msg_id, sender_id,
        )
