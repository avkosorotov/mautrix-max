"""Database model for Puppet (Max user -> Matrix ghost)."""

from __future__ import annotations

from typing import ClassVar, Optional

from mautrix.util.async_db import Database


class Puppet:
    db: ClassVar[Database]

    max_user_id: int
    name: Optional[str]
    username: Optional[str]
    avatar_mxc: Optional[str]
    name_set: bool
    avatar_set: bool
    is_registered: bool

    def __init__(
        self,
        max_user_id: int,
        name: Optional[str] = None,
        username: Optional[str] = None,
        avatar_mxc: Optional[str] = None,
        name_set: bool = False,
        avatar_set: bool = False,
        is_registered: bool = False,
    ) -> None:
        self.max_user_id = max_user_id
        self.name = name
        self.username = username
        self.avatar_mxc = avatar_mxc
        self.name_set = name_set
        self.avatar_set = avatar_set
        self.is_registered = is_registered

    @classmethod
    def _from_row(cls, row) -> Puppet:
        return cls(
            max_user_id=row["max_user_id"],
            name=row["name"],
            username=row["username"],
            avatar_mxc=row["avatar_mxc"],
            name_set=row["name_set"],
            avatar_set=row["avatar_set"],
            is_registered=row["is_registered"],
        )

    @classmethod
    async def get_by_max_user_id(cls, max_user_id: int) -> Optional[Puppet]:
        row = await cls.db.fetchrow(
            "SELECT * FROM puppet WHERE max_user_id=$1", max_user_id
        )
        return cls._from_row(row) if row else None

    @classmethod
    async def insert_or_update(
        cls,
        max_user_id: int,
        name: Optional[str] = None,
        username: Optional[str] = None,
        avatar_mxc: Optional[str] = None,
        name_set: bool = False,
        avatar_set: bool = False,
        is_registered: bool = False,
    ) -> Puppet:
        await cls.db.execute(
            "INSERT INTO puppet (max_user_id, name, username, avatar_mxc, name_set, avatar_set, is_registered) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) "
            "ON CONFLICT (max_user_id) DO UPDATE SET "
            "name=EXCLUDED.name, username=EXCLUDED.username, avatar_mxc=EXCLUDED.avatar_mxc, "
            "name_set=EXCLUDED.name_set, avatar_set=EXCLUDED.avatar_set, is_registered=EXCLUDED.is_registered",
            max_user_id, name, username, avatar_mxc, name_set, avatar_set, is_registered,
        )
        return cls(max_user_id, name, username, avatar_mxc, name_set, avatar_set, is_registered)
