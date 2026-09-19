import asyncio
import os
import sys
import tempfile

import discord
from discord import app_commands
from discord.ext import commands
from gtts import gTTS
from pymongo import MongoClient


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_mongo_client():
    uri = os.getenv("DATABASE_URL") or os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("Missing DATABASE_URL/MONGODB_URI")
    return MongoClient(uri)


def get_db():
    mongo = get_mongo_client()
    db_name = os.getenv("DATABASE_NAME", "titanium_white")
    return mongo[db_name]


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

    async def setup_hook(self):
        try:
            synced = await self.tree.sync()
            print(f"[bot] Synced {len(synced)} application commands.")
        except Exception as exc:
            print(f"[bot] Slash command sync failed: {exc}")

    async def on_ready(self):
        print(f"[bot] Logged in as {self.user} ({self.user.id})")

    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        role = discord.utils.get(guild.roles, name="Not Welcomed")
        if role is None:
            try:
                role = await guild.create_role(name="Not Welcomed", reason="Onboarding greeting")
            except Exception as exc:
                print(f"[bot] Failed to create Not Welcomed role: {exc}")
                return

        try:
            await member.add_roles(role, reason="Onboarding greeting required")
        except Exception as exc:
            print(f"[bot] Failed to add Not Welcomed role: {exc}")
            return

        config = self.greetvoice_configs.find_one({"guild_id": guild.id})
        if not config:
            return

        channel = guild.get_channel(config.get("channel_id"))
        if not channel:
            return

        for target in guild.channels:
            if target == channel:
                continue
            try:
                await target.set_permissions(role, view_channel=False, connect=False, speak=False, send_messages=False)
            except Exception:
                pass

        try:
            await channel.set_permissions(role, view_channel=True, connect=True, speak=True, send_messages=True)
        except Exception as exc:
            print(f"[bot] Failed to configure channel permissions: {exc}")

    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        if before.channel == after.channel:
            return

        config = self.greetvoice_configs.find_one({"guild_id": member.guild.id})
        if not config:
            return

        if after.channel is None:
            return

        if after.channel.id != config.get("channel_id"):
            return

        role = discord.utils.get(member.guild.roles, name="Not Welcomed")
        if role is None or role not in member.roles:
            return

        prompt = config.get("prompt")
        if not prompt:
            return

        try:
            await self.play_greeting(after.channel, prompt)
            try:
                await member.move_to(None)
            except Exception:
                pass
            await member.remove_roles(role, reason="Welcome flow completed")
        except Exception as exc:
            print(f"[bot] Greet voice flow failed: {exc}")

    async def play_greeting(self, channel: discord.VoiceChannel, prompt: str):
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
            temp_path = temp.name

        try:
            tts = gTTS(text=prompt, lang="en")
            tts.save(temp_path)

            voice = await channel.connect()
            try:
                source = discord.FFmpegPCMAudio(temp_path)
                voice.play(source)
                while voice.is_playing():
                    await asyncio.sleep(0.5)
            finally:
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

    bot.greetvoice_configs.update_one(
        {"guild_id": guild.id},
        {"$set": {"guild_id": guild.id, "channel_id": channel.id, "prompt": prompt}},
        upsert=True,
    )

    role = discord.utils.get(guild.roles, name="Not Welcomed")
    if role is None:
        try:
            role = await guild.create_role(name="Not Welcomed", reason="Onboarding greeting")
        except Exception as exc:
            await interaction.response.send_message(f"Could not create `Not Welcomed` role: {exc}", ephemeral=True)
            return

    for target in guild.channels:
        if target == channel:
            continue
        try:
            await target.set_permissions(role, view_channel=False, connect=False, speak=False, send_messages=False)
        except Exception:
            pass

    try:
        await channel.set_permissions(role, view_channel=True, connect=True, speak=True, send_messages=True)
    except Exception as exc:
        print(f"[bot] Failed to set greet channel permissions: {exc}")

    embed = discord.Embed(title="Greet Voice Enabled", color=discord.Color.green())
    embed.add_field(name="Channel", value=channel.mention, inline=False)
    embed.add_field(name="Prompt", value=prompt, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=False)

    try:
        if not guild.voice_client:
            await channel.connect()
    except Exception as exc:
        print(f"[bot] Could not auto-join greet voice channel: {exc}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")
    else:
        print(f"[bot] Command error: {error}")


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
