"""Database model for Portal (Max chat <-> Matrix room mapping)."""

from __future__ import annotations

from typing import ClassVar, Optional

from mautrix.types import RoomID
from mautrix.util.async_db import Database


class Portal:
    db: ClassVar[Database]

    max_chat_id: int
    mxid: Optional[str]
    name: Optional[str]
    encrypted: bool
    relay_user_id: Optional[str]

    def __init__(
        self,
        max_chat_id: int,
        mxid: Optional[str] = None,
        name: Optional[str] = None,
        encrypted: bool = False,
        relay_user_id: Optional[str] = None,
    ) -> None:
        self.max_chat_id = max_chat_id
        self.mxid = mxid
        self.name = name
        self.encrypted = encrypted
        self.relay_user_id = relay_user_id

    @classmethod
    def _from_row(cls, row) -> Portal:
        return cls(
            max_chat_id=row["max_chat_id"],
            mxid=row["mxid"],
            name=row["name"],
            encrypted=row["encrypted"],
            relay_user_id=row["relay_user_id"],
        )

    @classmethod
    async def get_by_max_chat_id(cls, max_chat_id: int) -> Optional[Portal]:
        row = await cls.db.fetchrow(
            "SELECT * FROM portal WHERE max_chat_id=$1", max_chat_id
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def get_by_mxid(cls, mxid: str) -> Optional[Portal]:
        row = await cls.db.fetchrow(
            "SELECT * FROM portal WHERE mxid=$1", mxid
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def insert(
        cls,
        max_chat_id: int,
        mxid: Optional[str] = None,
        name: Optional[str] = None,
        encrypted: bool = False,
        relay_user_id: Optional[str] = None,
    ) -> Portal:
        await cls.db.execute(
            "INSERT INTO portal (max_chat_id, mxid, name, encrypted, relay_user_id) "
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (max_chat_id) DO UPDATE SET "
            "mxid=EXCLUDED.mxid, name=EXCLUDED.name, encrypted=EXCLUDED.encrypted, "
            "relay_user_id=EXCLUDED.relay_user_id",
            max_chat_id, mxid, name, encrypted, relay_user_id,
        )
        return cls(max_chat_id, mxid, name, encrypted, relay_user_id)

    async def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        await self.db.execute(
            "UPDATE portal SET mxid=$2, name=$3, encrypted=$4, relay_user_id=$5 "
            "WHERE max_chat_id=$1",
            self.max_chat_id, self.mxid, self.name, self.encrypted, self.relay_user_id,
        )

    @classmethod
    async def get_all_with_mxid(cls) -> list[Portal]:
        rows = await cls.db.fetch(
            "SELECT * FROM portal WHERE mxid IS NOT NULL"
        )
        return [cls._from_row(row) for row in rows]

    async def delete(self) -> None:
        await self.db.execute(
            "DELETE FROM portal WHERE max_chat_id=$1", self.max_chat_id
        )
