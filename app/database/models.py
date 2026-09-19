from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class GuildSettings:
    guild_id: int
    prefix: str = "!"
    language: str = "en"
    mod_log_channel: Optional[int] = None
    welcome_channel: Optional[int] = None
    goodbye_channel: Optional[int] = None
    auto_roles: List[int] = field(default_factory=list)
    leveling_enabled: bool = True
    automod_enabled: bool = True
    tickets_enabled: bool = True
    music_enabled: bool = True
    tts_enabled: bool = True
    logging_enabled: bool = True
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_document(self) -> Dict[str, Any]:
        return {
            "guild_id": self.guild_id,
            "prefix": self.prefix,
            "language": self.language,
            "mod_log_channel": self.mod_log_channel,
            "welcome_channel": self.welcome_channel,
            "goodbye_channel": self.goodbye_channel,
            "auto_roles": self.auto_roles,
            "leveling_enabled": self.leveling_enabled,
            "automod_enabled": self.automod_enabled,
            "tickets_enabled": self.tickets_enabled,
            "music_enabled": self.music_enabled,
            "tts_enabled": self.tts_enabled,
            "logging_enabled": self.logging_enabled,
            "updated_at": self.updated_at,
        }


@dataclass
class UserProfile:
    guild_id: int
    user_id: int
    xp: int = 0
    level: int = 0
    warns: int = 0
    strikes: int = 0
    afk: bool = False
    last_xp_at: Optional[datetime] = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class WhitelistEntry:
    guild_id: int
    target_id: int
    target_type: str
    reason: str = ""
    created_by: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ModLogEvent:
    guild_id: int
    action: str
    moderator_id: int
    target_id: int
    reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
