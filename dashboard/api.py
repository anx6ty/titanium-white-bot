from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.database.mongo import mongo

app = FastAPI(title="Titanium White Dashboard")


@app.get("/health")
async def health():
    try:
        mongo.ping()
        return {"ok": True}
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)


@app.get("/")
async def index():
    return {"name": "Titanium White Dashboard", "oauth_url": f"https://discord.com/oauth2/authorize?client_id={settings.client_id}&scope=identify%20guilds&response_type=code"}


@app.get("/api/guilds/{guild_id}/settings")
async def guild_settings(guild_id: int):
    value = mongo.db.guild_settings.find_one({"guild_id": guild_id}, {"_id": 0})
    return value or {"guild_id": guild_id}
