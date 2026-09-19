import os
import sys


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    print("[bot] Starting Titanium White bot...")

    try:
        require_env("DISCORD_TOKEN")
        require_env("DATABASE_URL")
    except RuntimeError as exc:
        print(f"[bot] Startup failed: {exc}")
        sys.exit(1)

    # Optional Lavalink config (some bots use it)
    lavalink_host = os.getenv("LAVALINK_HOST")
    lavalink_port = os.getenv("LAVALINK_PORT")
    lavalink_password = os.getenv("LAVALINK_PASSWORD")

    if lavalink_host:
        print(f"[bot] Lavalink host configured: {lavalink_host}:{lavalink_port or 'default'}")
    else:
        print("[bot] Lavalink not configured; continuing without it.")

    try:
        import discord
    except ModuleNotFoundError:
        print("[bot] ERROR: discord.py is not installed. Add it to the environment before running this bot.")
        sys.exit(1)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        print(f"[bot] Logged in as {client.user} ({client.user.id})")

    try:
        client.run(require_env("DISCORD_TOKEN"))
    except Exception as exc:
        print(f"[bot] Discord client failed to start: {exc}")
        raise


if __name__ == "__main__":
    main()
