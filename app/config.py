import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def get_env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    value = os.getenv(name, default)
    if required and (value is None or value == ""):
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass
class Settings:
    discord_token: str = field(default_factory=lambda: get_env("DISCORD_TOKEN", required=True))
    bot_prefix: str = field(default_factory=lambda: get_env("BOT_PREFIX", "!"))
    guild_id: int | None = field(default_factory=lambda: int(get_env("GUILD_ID", "0") or 0))
    database_url: str = field(default_factory=lambda: get_env("DATABASE_URL", "mongodb://localhost:27017"))
    mongodb_uri: str = field(default_factory=lambda: get_env("MONGODB_URI", get_env("DATABASE_URL", "mongodb://localhost:27017")))
    database_name: str = field(default_factory=lambda: get_env("DATABASE_NAME", "titanium_white"))
    redis_url: str = field(default_factory=lambda: get_env("REDIS_URL", "redis://localhost:6379/0"))
    lavalink_host: str = field(default_factory=lambda: get_env("LAVALINK_HOST", "localhost"))
    lavalink_port: int = field(default_factory=lambda: int(get_env("LAVALINK_PORT", "2333") or 2333))
    lavalink_password: str = field(default_factory=lambda: get_env("LAVALINK_PASSWORD", "youshallnotpass"))
    lavalink_secure: bool = field(default_factory=lambda: (get_env("LAVALINK_SECURE", "false") or "false").lower() == "true")
    dashboard_url: str = field(default_factory=lambda: get_env("DASHBOARD_URL", "http://localhost:5000"))
    dashboard_secret: str = field(default_factory=lambda: get_env("DASHBOARD_SECRET", "change-me"))
    session_secret: str = field(default_factory=lambda: get_env("SESSION_SECRET", "change-me"))
    client_id: str = field(default_factory=lambda: get_env("CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: get_env("CLIENT_SECRET", ""))
    log_level: str = field(default_factory=lambda: get_env("LOG_LEVEL", "INFO"))
    owner_ids: List[int] = field(default_factory=lambda: [int(v.strip()) for v in (get_env("OWNER_IDS", "") or "").split(",") if v.strip()])


settings = Settings()
