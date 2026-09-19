from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LavalinkNode:
    host: str
    port: int
    password: str
    secure: bool = False


class LavalinkManager:
    def __init__(self, host: str, port: int, password: str, secure: bool = False):
        self.node = LavalinkNode(host, port, password, secure)

    async def connect(self):
        return True

    async def disconnect(self):
        return True

    async def play_tts(self, guild_id: int, text: str):
        return {"guild_id": guild_id, "text": text, "status": "queued"}


