import asyncio
import os
import sys
import tempfile

import discord
from discord import app_commands
from discord.ext import commands
from gtts import gTTS
from pymongo import MongoClient

ROLE_NAME = "Not Welcomed"


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_db():
    uri = os.getenv("DATABASE_URL") or os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("Missing DATABASE_URL/MONGODB_URI")
    mongo = MongoClient(uri, serverSelectionTimeoutMS=10000)
    return mongo[os.getenv("DATABASE_NAME", "titanium_white")]


class TitaniumWhiteBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)
        self.db = get_db()
        self.greetvoice_configs = self.db.greetvoice_configs
        self._guild_locks: dict[int, asyncio.Lock] = {}

    # ---------- helpers ----------

    def guild_lock(self, guild_id: int) -> asyncio.Lock:
        return self._guild_locks.setdefault(guild_id, asyncio.Lock())

    async def get_config(self, guild_id: int):
        # pymongo is blocking -> run in a thread so the event loop isn't stalled
        return await asyncio.to_thread(self.greetvoice_configs.find_one, {"guild_id": guild_id})

    async def save_config(self, guild_id: int, channel_id: int, prompt: str):
        await asyncio.to_thread(
            self.greetvoice_configs.update_one,
            {"guild_id": guild_id},
            {"$set": {"guild_id": guild_id, "channel_id": channel_id, "prompt": prompt}},
            True,  # upsert
        )

    async def get_or_create_role(self, guild: discord.Guild) -> discord.Role:
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if role is None:
            role = await guild.create_role(name=ROLE_NAME, reason="Onboarding greeting")
        return role

    async def apply_role_permissions(self, guild: discord.Guild, role: discord.Role, greet_channel):
        """Deny the role everywhere except the greet voice channel."""
        for target in guild.channels:
            if target.id == greet_channel.id:
                continue
            try:
                await target.set_permissions(
                    role,
                    view_channel=False,
                    connect=False,
                    speak=False,
                    send_messages=False,
                )
            except Exception:
                pass

        try:
            await greet_channel.set_permissions(
                role,
                view_channel=True,
                connect=True,
                speak=True,
            )
        except Exception as exc:
            print(f"[bot] Failed to set greet channel permissions: {exc}")

    # ---------- events ----------

    async def setup_hook(self):
        self.tree.on_error = self.on_app_command_error
        try:
            synced = await self.tree.sync()
            print(f"[bot] Synced {len(synced)} application commands.")
        except Exception as exc:
            print(f"[bot] Slash command sync failed: {exc}")

    async def on_ready(self):
        print(f"[bot] Logged in as {self.user} ({self.user.id})")

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You need Administrator permission to use this command."
        else:
            print(f"[bot] App command error: {error}")
            msg = "Something went wrong while running this command."

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You do not have permission to use this command.")
        elif isinstance(error, commands.CommandNotFound):
            return
        else:
            print(f"[bot] Command error: {error}")

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        try:
            role = await self.get_or_create_role(guild)
        except Exception as exc:
            print(f"[bot] Failed to create {ROLE_NAME} role: {exc}")
            return

        try:
            await member.add_roles(role, reason="Onboarding greeting required")
        except Exception as exc:
            print(f"[bot] Failed to add {ROLE_NAME} role: {exc}")
            return

        config = await self.get_config(guild.id)
        if not config:
            return

        channel = guild.get_channel(config.get("channel_id"))
        if not channel:
            return

        await self.apply_role_permissions(guild, role, channel)

    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        if before.channel == after.channel or after.channel is None:
            return

        config = await self.get_config(member.guild.id)
        if not config:
            return

        if after.channel.id != config.get("channel_id"):
            return

        role = discord.utils.get(member.guild.roles, name=ROLE_NAME)
        if role is None or role not in member.roles:
            return

        prompt = config.get("prompt")
        if not prompt:
            return

        # One greeting at a time per guild (avoids "already playing" errors)
        async with self.guild_lock(member.guild.id):
            # re-check: member may have left or been welcomed while waiting
            if member.voice is None or member.voice.channel != after.channel:
                return
            if role not in member.roles:
                return

            try:
                await self.play_greeting(after.channel, prompt)
            except Exception as exc:
                print(f"[bot] Greet voice flow failed: {exc}")
                return

            try:
                await member.move_to(None)
            except Exception:
                pass

            try:
                await member.remove_roles(role, reason="Welcome flow completed")
            except Exception as exc:
                print(f"[bot] Failed to remove {ROLE_NAME} role: {exc}")

    async def play_greeting(self, channel: discord.VoiceChannel, prompt: str):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
            temp_path = temp.name

        try:
            # gTTS is blocking (network) -> thread
            def make_tts():
                gTTS(text=prompt, lang="en").save(temp_path)

            await asyncio.to_thread(make_tts)

            # Reuse existing connection (e.g. from /greetvoice auto-join)
            voice = channel.guild.voice_client
            connected_here = False
            if voice is None:
                voice = await channel.connect()
                connected_here = True
            elif voice.channel != channel:
                await voice.move_to(channel)

            try:
                if voice.is_playing():
                    voice.stop()
                voice.play(discord.FFmpegPCMAudio(temp_path))
                while voice.is_playing():
                    await asyncio.sleep(0.5)
            finally:
                # Only leave if we joined just for this greeting
                if connected_here:
                    await voice.disconnect(force=True)
        finally:
            try:
                os.remove(temp_path)
            except FileNotFoundError:
                pass


bot = TitaniumWhiteBot()


@bot.tree.command(name="greetvoice", description="Configure onboarding greet voice")
@app_commands.describe(channel="Voice channel to greet in", prompt="Text to convert to speech")
@app_commands.checks.has_permissions(administrator=True)
async def greetvoice(interaction: discord.Interaction, channel: discord.VoiceChannel, prompt: str):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    # Permission loops can take a while -> defer to avoid the 3s interaction timeout
    await interaction.response.defer(ephemeral=False)

    await bot.save_config(guild.id, channel.id, prompt)

    try:
        role = await bot.get_or_create_role(guild)
    except Exception as exc:
        await interaction.followup.send(f"Could not create `{ROLE_NAME}` role: {exc}", ephemeral=True)
        return

    await bot.apply_role_permissions(guild, role, channel)

    embed = discord.Embed(title="Greet Voice Enabled", color=discord.Color.green())
    embed.add_field(name="Channel", value=channel.mention, inline=False)
    embed.add_field(name="Prompt", value=prompt[:1024], inline=False)
    await interaction.followup.send(embed=embed)

    try:
        if guild.voice_client is None:
            await channel.connect()
        elif guild.voice_client.channel != channel:
            await guild.voice_client.move_to(channel)
    except Exception as exc:
        print(f"[bot] Could not auto-join greet voice channel: {exc}")


def main():
    try:
        token = require_env("DISCORD_TOKEN")
        require_env("DATABASE_URL")
    except Exception as exc:
        print(f"[bot] Startup failed: {exc}")
        sys.exit(1)

    try:
        bot.run(token)
    except Exception as exc:
        print(f"[bot] Discord client failed to start: {exc}")
        raise


if __name__ == "__main__":
    main()
