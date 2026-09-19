from __future__ import annotations

from typing import Iterable

from app.database.mongo import get_db


class WhitelistService:
    def __init__(self):
        self.collection = get_db().whitelist

    def is_whitelisted(self, guild_id: int, target_id: int, target_type: str = "user") -> bool:
        return self.collection.find_one({"guild_id": guild_id, "target_id": target_id, "target_type": target_type}) is not None

    def add(self, guild_id: int, target_id: int, target_type: str, reason: str, created_by: int | None = None) -> None:
        self.collection.update_one(
            {"guild_id": guild_id, "target_id": target_id, "target_type": target_type},
            {"$set": {"guild_id": guild_id, "target_id": target_id, "target_type": target_type, "reason": reason, "created_by": created_by}},
            upsert=True,
        )

    def remove(self, guild_id: int, target_id: int, target_type: str = "user") -> None:
        self.collection.delete_one({"guild_id": guild_id, "target_id": target_id, "target_type": target_type})

    def list(self, guild_id: int) -> Iterable[dict]:
        return list(self.collection.find({"guild_id": guild_id}))


whitelist_service = WhitelistService()
