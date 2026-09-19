from __future__ import annotations

from typing import Any, Dict

from pymongo import MongoClient
from pymongo.database import Database

from app.config import settings


class MongoManager:
    def __init__(self, uri: str | None = None, database_name: str | None = None):
        self.uri = uri or settings.mongodb_uri
        self.database_name = database_name or settings.database_name
        self.client = MongoClient(self.uri, serverSelectionTimeoutMS=10000)
        self.db: Database = self.client[self.database_name]

    def ensure_indexes(self) -> None:
        self.db.guild_settings.create_index("guild_id", unique=True)
        self.db.mod_logs.create_index("guild_id")
        self.db.whitelist.create_index([("guild_id", 1), ("target_id", 1)], unique=True)
        self.db.profiles.create_index([("guild_id", 1), ("user_id", 1)], unique=True)
        self.db.ticket_data.create_index("guild_id")
        self.db.giveaways.create_index("guild_id")

    def ping(self) -> Dict[str, Any]:
        return {"ok": self.client.admin.command("ping")["ok"]}


mongo = MongoManager()


def get_db() -> Database:
    return mongo.db
