"""Database model for User (Matrix user -> Max account)."""

from __future__ import annotations

from typing import ClassVar, Optional

from mautrix.util.async_db import Database


class User:
    db: ClassVar[Database]

    mxid: str
    max_user_id: Optional[int]
    max_token: Optional[str]
    connection_mode: Optional[str]
    bot_token: Optional[str]

    def __init__(
        self,
        mxid: str,
        max_user_id: Optional[int] = None,
        max_token: Optional[str] = None,
        connection_mode: Optional[str] = None,
        bot_token: Optional[str] = None,
    ) -> None:
        self.mxid = mxid
        self.max_user_id = max_user_id
        self.max_token = max_token
        self.connection_mode = connection_mode
        self.bot_token = bot_token

    @classmethod
    def _from_row(cls, row) -> User:
        return cls(
            mxid=row["mxid"],
            max_user_id=row["max_user_id"],
            max_token=row["max_token"],
            connection_mode=row["connection_mode"],
            bot_token=row["bot_token"],
        )

    @classmethod
    async def get_by_mxid(cls, mxid: str) -> Optional[User]:
        row = await cls.db.fetchrow(
            'SELECT * FROM "user" WHERE mxid=$1', mxid
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def all_logged_in(cls) -> list[User]:
        rows = await cls.db.fetch(
            'SELECT * FROM "user" WHERE bot_token IS NOT NULL OR max_token IS NOT NULL'
        )
        return [cls._from_row(row) for row in rows]

    @classmethod
    async def insert(
        cls,
        mxid: str,
        max_user_id: Optional[int] = None,
        max_token: Optional[str] = None,
        connection_mode: Optional[str] = None,
        bot_token: Optional[str] = None,
    ) -> User:
        await cls.db.execute(
            'INSERT INTO "user" (mxid, max_user_id, max_token, connection_mode, bot_token) '
            "VALUES ($1, $2, $3, $4, $5) "
            "ON CONFLICT (mxid) DO UPDATE SET "
            "max_user_id=EXCLUDED.max_user_id, max_token=EXCLUDED.max_token, "
            "connection_mode=EXCLUDED.connection_mode, bot_token=EXCLUDED.bot_token",
            mxid, max_user_id, max_token, connection_mode, bot_token,
        )
        return cls(mxid, max_user_id, max_token, connection_mode, bot_token)

    async def update(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        await self.db.execute(
            'UPDATE "user" SET max_user_id=$2, max_token=$3, connection_mode=$4, bot_token=$5 '
            "WHERE mxid=$1",
            self.mxid, self.max_user_id, self.max_token, self.connection_mode, self.bot_token,
        )
