from __future__ import annotations

import asyncio
import os
import tempfile
import time
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from app.command_suite import install as install_command_suite
from app.config import settings
from app.database.mongo import get_db, mongo
from app.log import logger
from app.services.permissions import (
    require_admin,
    require_moderator,
    safe_send,
)
from app.services.whitelist import whitelist_service


# ============================================================
# CONSTANTS
# ============================================================

COLOR = discord.Color.from_str("#2B2D31")
WELCOME_ROLE_NAME = "Not Welcomed"

DEFAULT_EMOJIS = {
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "owner": "👑",
    "welcome": "👋",
    "voice": "🔊",
    "music": "🎵",
    "lock": "🔒",
    "unlock": "🔓",
    "bot": "🤖",
    "settings": "⚙️",
    "online": "🟢",
    "offline": "🔴",
    "check": "☑️",
    "cross": "✖️",
    "star": "⭐",
    "shield": "🛡️",
    "server": "🏠",
    "user": "👤",
    "volume": "🔉",
}

MUSIC_COMMAND_NAMES = {
    "play",
    "p",
    "pause",
    "resume",
    "skip",
    "stop",
    "queue",
    "q",
    "nowplaying",
    "np",
    "volume",
    "shuffle",
    "loop",
    "disconnect",
    "leave",
    "join",
    "music",
}


# ============================================================
# HELPERS
# ============================================================

def embed(
    title: str,
    description: str = "",
    *,
    colour: discord.Color = COLOR,
) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=description,
        colour=colour,
    )


def is_owner(user_id: int) -> bool:
    return user_id in settings.owner_ids


def mention_prefixes(bot: commands.Bot):
    """
    Allows:
        !command
        @Bot command

    The normal discord.py command-prefix handling already supports
    mentions when using a regular string prefix.

    This helper exists for clarity and future customization.
    """
    return commands.when_mentioned_or(settings.bot_prefix)(bot)


# ============================================================
# TITANIUM BOT
# ============================================================

class TitaniumBot(commands.Bot):

    def __init__(self):
        intents = discord.Intents.default()

        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        intents.message_content = True

        super().__init__(
            command_prefix=mention_prefixes,
            intents=intents,
            help_command=None,
        )

        self.db = get_db()

        self.guild_locks: dict[int, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

        self.spam: dict[
            tuple[int, int],
            deque[float],
        ] = defaultdict(deque)

        self.started_at = time.monotonic()

        # Prevent multiple welcome speeches from playing
        # simultaneously in the same guild.
        self.greet_locks: dict[int, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

        # Members currently going through GreetVoice.
        self.greet_members: dict[int, set[int]] = defaultdict(set)

        # Temporary TTS files.
        self.tts_files: set[str] = set()

        # Guild -> task keeping the bot inside the Welcome VC.
        self.voice_keeper_tasks: dict[
            int,
            asyncio.Task,
        ] = {}

        # Cached custom emojis.
        self.emoji_cache: dict[str, str] = {}

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    async def guild_config(
        self,
        guild_id: int,
    ) -> dict:
        value = await asyncio.to_thread(
            self.db.guild_settings.find_one,
            {"guild_id": guild_id},
        )

        if value:
            return value

        defaults = {
            "guild_id": guild_id,
            "automod_enabled": True,
            "leveling_enabled": True,
            "music_enabled": True,
            "tickets_enabled": True,
            "greetvoice_enabled": False,
        }

        await asyncio.to_thread(
            self.db.guild_settings.update_one,
            {"guild_id": guild_id},
            {
                "$setOnInsert": defaults,
            },
            upsert=True,
        )

        return defaults

    async def set_config(
        self,
        guild_id: int,
        **values,
    ):
        await asyncio.to_thread(
            self.db.guild_settings.update_one,
            {"guild_id": guild_id},
            {
                "$set": values,
            },
            upsert=True,
        )

    # --------------------------------------------------------
    # EMOJI SYSTEM
    # --------------------------------------------------------

    async def get_emojis(self) -> dict[str, str]:
        if self.emoji_cache:
            return dict(self.emoji_cache)

        document = await asyncio.to_thread(
            self.db.bot_settings.find_one,
            {"key": "emojis"},
        )

        emojis = dict(DEFAULT_EMOJIS)

        if document and isinstance(
            document.get("values"),
            dict,
        ):
            emojis.update(
                {
                    str(k): str(v)
                    for k, v in document["values"].items()
                }
            )

        self.emoji_cache = emojis

        return dict(emojis)

    async def get_emoji(
        self,
        name: str,
    ) -> str:
        emojis = await self.get_emojis()

        return emojis.get(
            name,
            DEFAULT_EMOJIS.get(name, "•"),
        )

    async def set_emoji(
        self,
        name: str,
        value: str,
    ):
        emojis = await self.get_emojis()

        emojis[name] = value
        self.emoji_cache = dict(emojis)

        await asyncio.to_thread(
            self.db.bot_settings.update_one,
            {"key": "emojis"},
            {
                "$set": {
                    "values": emojis,
                }
            },
            upsert=True,
        )

    async def reset_emoji(
        self,
        name: str,
    ):
        emojis = await self.get_emojis()

        if name in DEFAULT_EMOJIS:
            emojis[name] = DEFAULT_EMOJIS[name]
        else:
            emojis.pop(name, None)

        self.emoji_cache = dict(emojis)

        await asyncio.to_thread(
            self.db.bot_settings.update_one,
            {"key": "emojis"},
            {
                "$set": {
                    "values": emojis,
                }
            },
            upsert=True,
        )

    # --------------------------------------------------------
    # SETUP
    # --------------------------------------------------------

    async def setup_hook(self):
        mongo.ensure_indexes()

        self.add_view(LeaveView(self))

        install_command_suite(self)

        await self.tree.sync()

        logger.info(
            "Application commands synchronised"
        )

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    async def on_ready(self):
        logger.info(
            "Logged in as %s (%s)",
            self.user,
            self.user.id if self.user else "unknown",
        )

        # Re-enter configured Welcome VCs after a restart/reconnect.
        for guild in self.guilds:
            asyncio.create_task(
                self.ensure_welcome_voice(guild)
            )

    # --------------------------------------------------------
    # WELCOME VOICE
    # --------------------------------------------------------

    async def ensure_welcome_voice(
        self,
        guild: discord.Guild,
    ):
        """
        Keep the bot permanently connected to the configured
        Welcome VC.

        The bot is never intentionally disconnected by this system
        when the GreetVoice channel is configured.
        """
        try:
            config = await self.guild_config(guild.id)

            if not config.get(
                "greetvoice_enabled",
                False,
            ):
                return

            channel_id = config.get("greetvoice_channel")

            if not channel_id:
                return

            channel = guild.get_channel(channel_id)

            if not isinstance(
                channel,
                discord.VoiceChannel,
            ):
                return

            voice_client = guild.voice_client

            if voice_client is None:
                try:
                    await channel.connect(reconnect=True)

                    logger.info(
                        "Joined GreetVoice channel %s in %s",
                        channel.id,
                        guild.id,
                    )

                except (
                    discord.ClientException,
                    discord.Forbidden,
                    discord.HTTPException,
                    asyncio.TimeoutError,
                ) as exc:
                    logger.warning(
                        "Could not join GreetVoice VC in %s: %s",
                        guild.id,
                        exc,
                    )

            elif voice_client.channel.id != channel.id:
                try:
                    await voice_client.move_to(channel)

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ) as exc:
                    logger.warning(
                        "Could not move bot to GreetVoice VC in %s: %s",
                        guild.id,
                        exc,
                    )

        except Exception:
            logger.exception(
                "Unexpected GreetVoice connection error"
            )

    async def create_tts_file(
        self,
        text: str,
    ) -> Optional[str]:
        """
        Generate a temporary MP3 using gTTS.

        gTTS must be installed in requirements.txt.
        """
        try:
            from gtts import gTTS

        except ImportError:
            logger.error(
                "gTTS is not installed."
            )
            return None

        filename = tempfile.mktemp(
            prefix="titanium_greet_",
            suffix=".mp3",
        )

        try:
            tts = gTTS(
                text=text,
                lang="en",
                slow=False,
            )

            await asyncio.to_thread(
                tts.save,
                filename,
            )

            self.tts_files.add(filename)

            return filename

        except Exception:
            logger.exception(
                "TTS generation failed"
            )

            try:
                if os.path.exists(filename):
                    os.remove(filename)

            except OSError:
                pass

            return None

    async def play_greeting(
        self,
        member: discord.Member,
    ):
        guild = member.guild

        config = await self.guild_config(
            guild.id
        )

        channel_id = config.get(
            "greetvoice_channel"
        )

        greeting = config.get(
            "greetvoice_text"
        )

        if not channel_id or not greeting:
            return

        channel = guild.get_channel(
            channel_id
        )

        if not isinstance(
            channel,
            discord.VoiceChannel,
        ):
            return

        voice_client = guild.voice_client

        if voice_client is None:
            await self.ensure_welcome_voice(guild)
            voice_client = guild.voice_client

        if voice_client is None:
            return

        # Only the member being welcomed should be in the
        # onboarding channel besides the bot.
        if member.voice is None:
            return

        if member.voice.channel.id != channel.id:
            return

        # Only one onboarding speech at a time.
        lock = self.greet_locks[guild.id]

        if lock.locked():
            try:
                await member.move_to(
                    None,
                    reason="GreetVoice queue is busy",
                )

            except discord.HTTPException:
                pass

            return

        async with lock:
            self.greet_members[guild.id].add(
                member.id
            )

            try:
                # Make sure the bot remains in the configured VC.
                if (
                    guild.voice_client is None
                    or guild.voice_client.channel.id != channel.id
                ):
                    await self.ensure_welcome_voice(guild)

                voice_client = guild.voice_client

                if voice_client is None:
                    return

                # Generate speech.
                filename = await self.create_tts_file(
                    greeting
                )

                if not filename:
                    return

                finished = asyncio.Event()

                def after_playing(
                    error: Optional[Exception],
                ):
                    if error:
                        logger.error(
                            "GreetVoice playback error: %s",
                            error,
                        )

                    bot.loop.call_soon_threadsafe(
                        finished.set
                    )

                try:
                    audio = discord.FFmpegPCMAudio(
                        filename
                    )

                    voice_client.play(
                        audio,
                        after=after_playing,
                    )

                    await finished.wait()

                except (
                    discord.ClientException,
                    discord.HTTPException,
                ):
                    logger.exception(
                        "Unable to play GreetVoice audio"
                    )

                finally:
                    try:
                        if voice_client.is_playing():
                            voice_client.stop()

                    except Exception:
                        pass

                    try:
                        if os.path.exists(filename):
                            os.remove(filename)

                        self.tts_files.discard(filename)

                    except OSError:
                        pass

                # Fetch the latest member object.
                member = guild.get_member(
                    member.id
                )

                if member is None:
                    return

                # Disconnect the member from Welcome VC.
                if member.voice is not None:
                    try:
                        if (
                            member.voice.channel
                            and member.voice.channel.id == channel.id
                        ):
                            await member.move_to(
                                None,
                                reason=(
                                    "GreetVoice welcome "
                                    "speech completed"
                                ),
                            )

                    except (
                        discord.Forbidden,
                        discord.HTTPException,
                    ) as exc:
                        logger.warning(
                            "Could not disconnect welcomed member %s: %s",
                            member.id,
                            exc,
                        )

                # Remove the gate role.
                role_id = config.get(
                    "greetvoice_role"
                )

                role = (
                    guild.get_role(role_id)
                    if role_id
                    else discord.utils.get(
                        guild.roles,
                        name=WELCOME_ROLE_NAME,
                    )
                )

                if role and role in member.roles:
                    try:
                        await member.remove_roles(
                            role,
                            reason=(
                                "GreetVoice welcome "
                                "completed"
                            ),
                        )

                    except (
                        discord.Forbidden,
                        discord.HTTPException,
                    ) as exc:
                        logger.warning(
                            "Could not remove GreetVoice role from %s: %s",
                            member.id,
                            exc,
                        )

            finally:
                self.greet_members[
                    guild.id
                ].discard(member.id)

                # Immediately make sure the bot remains in VC.
                asyncio.create_task(
                    self.ensure_welcome_voice(guild)
                )

    async def configure_welcome_permissions(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        role: discord.Role,
    ):
        """
        Restrict the Not Welcomed role.

        Important: This uses Discord permission overwrites on the
        @everyone role and Not Welcomed role.

        The role is denied viewing normal channels, while the
        configured Welcome VC is explicitly allowed.
        """

        # Deny the role from seeing normal channels.
        for target in guild.channels:
            try:
                if target.id == channel.id:
                    await target.set_permissions(
                        role,
                        view_channel=True,
                        connect=True,
                        speak=True,
                        stream=True,
                        use_voice_activation=True,
                        reason=(
                            "GreetVoice access configuration"
                        ),
                    )

                    continue

                # Skip categories containing the Welcome VC;
                # otherwise denying ViewChannel at category level
                # could interfere with the explicit VC override.
                if isinstance(
                    target,
                    discord.CategoryChannel,
                ):
                    continue

                await target.set_permissions(
                    role,
                    view_channel=False,
                    send_messages=False,
                    connect=False,
                    reason=(
                        "GreetVoice access restriction"
                    ),
                )

            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                logger.warning(
                    "Could not configure permission for channel %s",
                    target.id,
                )

    async def setup_greetvoice(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel,
        speech: str,
        role: Optional[discord.Role] = None,
    ):
        if role is None:
            role = discord.utils.get(
                guild.roles,
                name=WELCOME_ROLE_NAME,
            )

        if role is None:
            try:
                role = await guild.create_role(
                    name=WELCOME_ROLE_NAME,
                    colour=discord.Colour.dark_grey(),
                    reason="GreetVoice onboarding",
                )

            except discord.Forbidden:
                return (
                    None,
                    (
                        "I couldn't create the "
                        "`Not Welcomed` role. "
                        "Please give me **Manage Roles** permission."
                    ),
                )

        await self.set_config(
            guild.id,
            greetvoice_enabled=True,
            greetvoice_channel=channel.id,
            greetvoice_role=role.id,
            greetvoice_text=speech[:1000],
        )

        await self.configure_welcome_permissions(
            guild,
            channel,
            role,
        )

        await self.ensure_welcome_voice(guild)

        return role, None

    # --------------------------------------------------------
    # MEMBER JOIN
    # --------------------------------------------------------

    async def on_member_join(
        self,
        member: discord.Member,
    ):
        config = await self.guild_config(
            member.guild.id
        )

        if config.get(
            "greetvoice_enabled",
            False,
        ):
            role_id = config.get(
                "greetvoice_role"
            )

            role = (
                member.guild.get_role(role_id)
                if role_id
                else discord.utils.get(
                    member.guild.roles,
                    name=WELCOME_ROLE_NAME,
                )
            )

            if role:
                try:
                    await member.add_roles(
                        role,
                        reason=(
                            "GreetVoice onboarding "
                            "gate"
                        ),
                    )

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    logger.warning(
                        "Could not add GreetVoice role to %s",
                        member.id,
                    )

            asyncio.create_task(
                self.ensure_welcome_voice(
                    member.guild
                )
            )

        # Existing configured auto roles.
        for role_id in config.get(
            "auto_roles",
            [],
        ):
            role = member.guild.get_role(
                role_id
            )

            if role:
                try:
                    await member.add_roles(
                        role,
                        reason="Configured auto-role",
                    )

                except discord.HTTPException:
                    logger.warning(
                        "Unable to apply auto-role in %s",
                        member.guild.id,
                    )

        # Existing welcome channel.
        channel = (
            member.guild.get_channel(
                config.get("welcome_channel")
            )
            if config.get("welcome_channel")
            else None
        )

        # Don't expose normal welcome messaging to the gated
        # member unless an admin has configured it separately.
        if (
            isinstance(
                channel,
                discord.TextChannel,
            )
            and not config.get(
                "greetvoice_enabled",
                False,
            )
        ):
            await channel.send(
                embed=embed(
                    "Welcome",
                    (
                        f"Welcome {member.mention} "
                        f"to **{member.guild.name}**."
                    ),
                )
            )

    # --------------------------------------------------------
    # VOICE STATE
    # --------------------------------------------------------

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        if member.bot:
            return

        guild = member.guild

        config = await self.guild_config(
            guild.id
        )

        if not config.get(
            "greetvoice_enabled",
            False,
        ):
            return

        channel_id = config.get(
            "greetvoice_channel"
        )

        if not channel_id:
            return

        # Member just entered the configured Welcome VC.
        if (
            after.channel is not None
            and after.channel.id == channel_id
            and (
                before.channel is None
                or before.channel.id != channel_id
            )
        ):
            role_id = config.get(
                "greetvoice_role"
            )

            role = (
                guild.get_role(role_id)
                if role_id
                else discord.utils.get(
                    guild.roles,
                    name=WELCOME_ROLE_NAME,
                )
            )

            # Only run onboarding for members who still
            # have the gate role.
            if role and role in member.roles:
                asyncio.create_task(
                    self.play_greeting(member)
                )

        # If the bot itself was moved/disconnected, restore it.
        if (
            member.id == self.user.id
            if self.user
            else False
        ):
            asyncio.create_task(
                self.ensure_welcome_voice(guild)
            )

    # --------------------------------------------------------
    # MESSAGE / AUTOMOD
    # --------------------------------------------------------

    async def on_message(
        self,
        message: discord.Message,
    ):
        if (
            message.author.bot
            or not message.guild
        ):
            return

        config = await self.guild_config(
            message.guild.id
        )

        if (
            config.get(
                "automod_enabled",
                True,
            )
            and not whitelist_service.is_whitelisted(
                message.guild.id,
                message.author.id,
            )
        ):
            key = (
                message.guild.id,
                message.author.id,
            )

            now = time.monotonic()
            bucket = self.spam[key]

            bucket.append(now)

            while (
                bucket
                and now - bucket[0] > 8
            ):
                bucket.popleft()

            blocked = (
                len(bucket) >= 7
                or (
                    (
                        "http://" in message.content.lower()
                        or "https://" in message.content.lower()
                    )
                    and config.get(
                        "anti_links",
                        False,
                    )
                )
            )

            if blocked:
                try:
                    await message.delete()

                    await message.author.timeout(
                        timedelta(minutes=1),
                        reason="Automod violation",
                    )

                except (
                    discord.Forbidden,
                    discord.HTTPException,
                ):
                    logger.warning(
                        "Automod could not act in %s",
                        message.guild.id,
                    )

                return

        await self.process_commands(message)

    # --------------------------------------------------------
    # COMMAND ERROR
    # --------------------------------------------------------

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        logger.error(
            "Application command failed: %s",
            error,
        )

        await safe_send(
            interaction,
            (
                "Something went wrong while running "
                "that command. Please check my permissions "
                "and try again."
            ),
        )

    # --------------------------------------------------------
    # MUSIC PROTECTION
    # --------------------------------------------------------

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        """
        Prevent music commands from trying to take the bot away
        from the permanent Welcome VC.
        """
        try:
            command_name = (
                interaction.data.get("name")
                if interaction.data
                else None
            )

            if command_name in MUSIC_COMMAND_NAMES:
                config = await self.guild_config(
                    interaction.guild_id
                )

                if config.get(
                    "greetvoice_enabled",
                    False,
                ):
                    welcome_channel_id = config.get(
                        "greetvoice_channel"
                    )

                    if (
                        welcome_channel_id
                        and interaction.guild
                        and interaction.guild.voice_client
                    ):
                        vc = interaction.guild.voice_client

                        if (
                            vc.channel
                            and vc.channel.id == welcome_channel_id
                        ):
                            e = await self.get_emojis()

                            await interaction.response.send_message(
                                (
                                    f"{e['voice']} **Welcome Voice is active!**\n\n"
                                    "I'm currently stationed in the "
                                    "**Welcome VC** for new members, so "
                                    "I can't leave this channel for music "
                                    "right now.\n\n"
                                    f"{e['info']} Please ask an **admin** "
                                    "to remove/disable the Welcome VC "
                                    "setup first, then I'll be able to "
                                    "handle music normally. 🎵"
                                ),
                                ephemeral=True,
                            )

                            return False

        except Exception:
            logger.exception(
                "Music protection check failed"
            )

        return True


bot = TitaniumBot()


# ============================================================
# APPLICATION COMMAND GROUPS
# ============================================================

admin_group = app_commands.Group(
    name="config",
    description="Manage server configuration",
)

mod_group = app_commands.Group(
    name="mod",
    description="Moderation tools",
)

level_group = app_commands.Group(
    name="level",
    description="Leveling tools",
)

greetvoice_group = app_commands.Group(
    name="greetvoice",
    description="Configure the Welcome Voice system",
)

bot.tree.add_command(admin_group)
bot.tree.add_command(mod_group)
bot.tree.add_command(level_group)
bot.tree.add_command(greetvoice_group)


# ============================================================
# HELP
# ============================================================

def build_help_embed() -> discord.Embed:
    return embed(
        "Titanium White — Help",
        (
            "👤 **Member Commands**\n"
            "`/help` • `!help`\n"
            "`/ping` • `!ping`\n"
            "`/uptime` • `!uptime`\n"
            "`/level rank`\n\n"
            "🛡️ **Moderator Commands**\n"
            "`/mod warn`\n"
            "`/mod timeout`\n"
            "`/mod kick`\n"
            "`/mod ban`\n\n"
            "⚙️ **Admin Commands**\n"
            "`/config panel`\n"
            "`/config toggle`\n"
            "`/config leavelogging`\n"
            "`/config setup_leave`\n"
            "`/config whitelist`\n"
            "`/greetvoice setup`\n"
            "`/greetvoice speech`\n"
            "`/greetvoice status`\n"
            "`/greetvoice disable`\n"
            "`/setgreetvoice`\n\n"
            "👑 **Bot Owner**\n"
            "Owner commands are prefix/mention only.\n"
            "`!owneronly`\n"
            "`@Bot owneronly`"
        ),
    )


@bot.tree.command(
    name="help",
    description="Show member, moderator and admin commands",
)
async def help_command(
    interaction: discord.Interaction,
):
    await interaction.response.send_message(
        embed=build_help_embed(),
        ephemeral=True,
    )


@bot.command(
    name="help",
    aliases=["commands"],
)
async def prefix_help(
    ctx: commands.Context,
):
    await ctx.send(
        embed=build_help_embed()
    )


# ============================================================
# BASIC
# ============================================================

@bot.tree.command(
    name="ping",
    description="Show bot latency",
)
async def ping(
    interaction: discord.Interaction,
):
    e = await bot.get_emojis()

    await interaction.response.send_message(
        embed=embed(
            f"{e['bot']} Pong!",
            (
                f"{e['online']} WebSocket: "
                f"`{round(bot.latency * 1000)}ms`"
            ),
        ),
        ephemeral=True,
    )


@bot.command(name="ping")
async def prefix_ping(
    ctx: commands.Context,
):
    e = await bot.get_emojis()

    await ctx.send(
        embed=embed(
            f"{e['bot']} Pong!",
            (
                f"{e['online']} WebSocket: "
                f"`{round(bot.latency * 1000)}ms`"
            ),
        )
    )


@bot.tree.command(
    name="uptime",
    description="Show bot uptime",
)
async def uptime(
    interaction: discord.Interaction,
):
    e = await bot.get_emojis()

    seconds = int(
        time.monotonic() - bot.started_at
    )

    await interaction.response.send_message(
        embed=embed(
            f"{e['online']} Uptime",
            f"`{seconds} seconds`",
        ),
        ephemeral=True,
    )


@bot.command(name="uptime")
async def prefix_uptime(
    ctx: commands.Context,
):
    seconds = int(
        time.monotonic() - bot.started_at
    )

    await ctx.send(
        embed=embed(
            "🟢 Uptime",
            f"`{seconds} seconds`",
        )
    )


# ============================================================
# GREETVOICE ADMIN COMMANDS
# ============================================================

@greetvoice_group.command(
    name="setup",
    description="Configure the permanent Welcome VC and onboarding speech",
)
@app_commands.describe(
    channel="The voice channel where the bot will stay",
    speech="The speech the bot should play for new members",
    role="Optional Not Welcomed role",
)
async def greetvoice_setup(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    speech: str,
    role: Optional[discord.Role] = None,
):
    if not await require_admin(interaction):
        return

    if len(speech) > 1000:
        await interaction.response.send_message(
            "The welcome speech must be 1000 characters or fewer.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(
        ephemeral=True
    )

    created_role, error = await bot.setup_greetvoice(
        interaction.guild,
        channel,
        speech,
        role,
    )

    if error:
        await interaction.followup.send(
            f"❌ {error}",
            ephemeral=True,
        )
        return

    e = await bot.get_emojis()

    await interaction.followup.send(
        embed=embed(
            f"{e['success']} GreetVoice Enabled",
            (
                f"{e['voice']} **Welcome VC:** {channel.mention}\n"
                f"{e['lock']} **Gate Role:** {created_role.mention}\n\n"
                f"{e['welcome']} **Speech:**\n"
                f"> {speech}\n\n"
                f"{e['bot']} I will remain in this VC permanently "
                "while GreetVoice is enabled."
            ),
        ),
        ephemeral=True,
    )


@greetvoice_group.command(
    name="speech",
    description="Change the speech played for new members",
)
@app_commands.describe(
    speech="New welcome speech",
)
async def greetvoice_speech(
    interaction: discord.Interaction,
    speech: str,
):
    if not await require_admin(interaction):
        return

    if len(speech) > 1000:
        await interaction.response.send_message(
            "The speech must be 1000 characters or fewer.",
            ephemeral=True,
        )
        return

    await bot.set_config(
        interaction.guild_id,
        greetvoice_text=speech,
    )

    await interaction.response.send_message(
        embed=embed(
            "🔊 Welcome Speech Updated",
            f"👋 New speech:\n> {speech}",
        ),
        ephemeral=True,
    )


@greetvoice_group.command(
    name="status",
    description="Show the current GreetVoice configuration",
)
async def greetvoice_status(
    interaction: discord.Interaction,
):
    if not await require_admin(interaction):
        return

    config = await bot.guild_config(
        interaction.guild_id
    )

    channel = (
        interaction.guild.get_channel(
            config.get("greetvoice_channel")
        )
        if config.get("greetvoice_channel")
        else None
    )

    role = (
        interaction.guild.get_role(
            config.get("greetvoice_role")
        )
        if config.get("greetvoice_role")
        else None
    )

    enabled = config.get(
        "greetvoice_enabled",
        False,
    )

    await interaction.response.send_message(
        embed=embed(
            "🔊 GreetVoice Status",
            (
                f"Status: **{'Enabled 🟢' if enabled else 'Disabled 🔴'}**\n"
                f"Welcome VC: "
                f"{channel.mention if channel else 'Not configured'}\n"
                f"Gate Role: "
                f"{role.mention if role else 'Not configured'}\n"
                "Speech:\n"
                f"> {config.get('greetvoice_text', 'Not configured')}"
            ),
        ),
        ephemeral=True,
    )


@greetvoice_group.command(
    name="disable",
    description="Disable GreetVoice",
)
async def greetvoice_disable(
    interaction: discord.Interaction,
):
    if not await require_admin(interaction):
        return

    await bot.set_config(
        interaction.guild_id,
        greetvoice_enabled=False,
    )

    # NOTE:
    # We intentionally do NOT disconnect the bot here.
    # The user requested that the bot should never leave
    # the Welcome VC automatically.

    await interaction.response.send_message(
        embed=embed(
            "🔴 GreetVoice Disabled",
            (
                "New members will no longer be placed into "
                "the GreetVoice onboarding flow.\n\n"
                "The bot will remain in the Welcome VC until "
                "an owner manually moves/disconnects it."
            ),
        ),
        ephemeral=True,
    )


# Backwards-compatible command from the original bot.
@bot.tree.command(
    name="setgreetvoice",
    description="Configure the Welcome Voice onboarding system",
)
@app_commands.describe(
    channel="Welcome voice channel",
    tts_text="Speech played to new members",
)
async def setgreetvoice(
    interaction: discord.Interaction,
    channel: discord.VoiceChannel,
    tts_text: str,
):
    if not await require_admin(interaction):
        return

    if len(tts_text) > 1000:
        await interaction.response.send_message(
            "The speech must be 1000 characters or fewer.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(
        ephemeral=True
    )

    role, error = await bot.setup_greetvoice(
        interaction.guild,
        channel,
        tts_text,
    )

    if error:
        await interaction.followup.send(
            f"❌ {error}",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=embed(
            "🔊 GreetVoice Configured",
            (
                f"Welcome VC: {channel.mention}\n"
                f"Gate role: {role.mention}\n\n"
                f"👋 Speech:\n> {tts_text}"
            ),
        ),
        ephemeral=True,
    )


# ============================================================
# ADMIN CONFIG
# ============================================================

class ConfigView(discord.ui.View):

    def __init__(
        self,
        bot_instance: TitaniumBot,
    ):
        super().__init__(
            timeout=180
        )

        self.bot_instance = bot_instance

        self.add_item(
            ConfigSelect(bot_instance)
        )


class ConfigSelect(discord.ui.Select):

    def __init__(
        self,
        bot_instance: TitaniumBot,
    ):
        self.bot_instance = bot_instance

        options = [
            discord.SelectOption(
                label=x,
                value=x.lower(),
            )
            for x in (
                "Security",
                "Leveling",
                "Audio",
                "Tickets",
                "Welcome",
                "Logging",
            )
        ]

        super().__init__(
            placeholder="Choose a configuration category",
            options=options,
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):
        if not await require_admin(
            interaction
        ):
            return

        data = await self.bot_instance.guild_config(
            interaction.guild_id
        )

        key = self.values[0]

        value = data.get(
            f"{key}_enabled",
            data.get(
                "automod_enabled",
                True,
            ),
        )

        await interaction.response.send_message(
            embed=embed(
                f"{key.title()} configuration",
                (
                    f"Enabled: **{value}**\n"
                    "Use `/config toggle` to change feature flags."
                ),
            ),
            ephemeral=True,
        )


@admin_group.command(
    name="panel",
    description="Open the interactive configuration panel",
)
async def config_panel(
    interaction: discord.Interaction,
):
    if not await require_admin(
        interaction
    ):
        return

    await interaction.response.send_message(
        embed=embed(
            "⚙️ Titanium Configuration",
            "Choose a category below.",
        ),
        view=ConfigView(bot),
        ephemeral=True,
    )


@admin_group.command(
    name="toggle",
    description="Enable or disable a feature",
)
@app_commands.describe(
    feature="Feature name",
    enabled="Whether the feature is enabled",
)
async def config_toggle(
    interaction: discord.Interaction,
    feature: str,
    enabled: bool,
):
    if not await require_admin(
        interaction
    ):
        return

    allowed = {
        "automod",
        "leveling",
        "music",
        "tickets",
        "anti_links",
    }

    if feature not in allowed:
        await interaction.response.send_message(
            (
                "Feature must be one of: "
                + ", ".join(
                    sorted(allowed)
                )
            ),
            ephemeral=True,
        )
        return

    key = (
        f"{feature}_enabled"
        if feature != "anti_links"
        else feature
    )

    await bot.set_config(
        interaction.guild_id,
        **{key: enabled},
    )

    await interaction.response.send_message(
        (
            f"`{feature}` is now "
            f"**{'enabled 🟢' if enabled else 'disabled 🔴'}**."
        ),
        ephemeral=True,
    )


# ============================================================
# LEAVE SYSTEM
# ============================================================

class LeaveView(discord.ui.View):

    def __init__(
        self,
        bot_instance: TitaniumBot,
    ):
        super().__init__(
            timeout=None
        )

        self.bot_instance = bot_instance

    @discord.ui.button(
        label="Create Leave",
        style=discord.ButtonStyle.primary,
        custom_id="leave:create",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ):
        await interaction.response.send_modal(
            LeaveModal(
                self.bot_instance
            )
        )


class LeaveModal(
    discord.ui.Modal,
    title="Staff Leave Request",
):

    duration = discord.ui.TextInput(
        label="Leave duration",
        placeholder="e.g. 3 days",
        max_length=100,
    )

    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    start_date = discord.ui.TextInput(
        label="Start date",
        placeholder="YYYY-MM-DD",
        max_length=20,
    )

    def __init__(
        self,
        bot_instance: TitaniumBot,
    ):
        super().__init__()

        self.bot_instance = bot_instance

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        config = await self.bot_instance.guild_config(
            interaction.guild_id
        )

        channel = (
            interaction.guild.get_channel(
                config.get("leave_log_channel")
            )
            if config.get("leave_log_channel")
            else None
        )

        if not isinstance(
            channel,
            discord.TextChannel,
        ):
            await interaction.response.send_message(
                "Leave logging is not configured.",
                ephemeral=True,
            )
            return

        card = embed(
            "📋 Staff Leave Request",
            f"Requested by {interaction.user.mention}",
        )

        card.add_field(
            name="Duration",
            value=self.duration.value,
            inline=True,
        )

        card.add_field(
            name="Start date",
            value=self.start_date.value,
            inline=True,
        )

        card.add_field(
            name="Reason",
            value=self.reason.value,
            inline=False,
        )

        await channel.send(
            embed=card
        )

        await interaction.response.send_message(
            "✅ Your leave request was submitted.",
            ephemeral=True,
        )


@admin_group.command(
    name="leavelogging",
    description="Set the staff leave log channel",
)
async def leavelogging(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    if not await require_admin(
        interaction
    ):
        return

    await bot.set_config(
        interaction.guild_id,
        leave_log_channel=channel.id,
    )

    await interaction.response.send_message(
        (
            "📋 Leave requests will be sent to "
            f"{channel.mention}."
        ),
        ephemeral=True,
    )


@admin_group.command(
    name="setup_leave",
    description="Post the staff leave application panel",
)
async def setup_leave(
    interaction: discord.Interaction,
):
    if not await require_admin(
        interaction
    ):
        return

    if isinstance(
        interaction.channel,
        discord.TextChannel,
    ):
        await interaction.channel.send(
            embed=embed(
                "📋 Staff Leave",
                (
                    "Submit a leave request "
                    "using the button below."
                ),
            ),
            view=LeaveView(bot),
        )

    await interaction.response.send_message(
        "✅ Leave panel posted.",
        ephemeral=True,
    )


@admin_group.command(
    name="whitelist",
    description="Add or remove an automod whitelist entry",
)
@app_commands.describe(
    action="add or remove",
    user="User to whitelist",
)
@app_commands.choices(
    action=[
        app_commands.Choice(
            name="add",
            value="add",
        ),
        app_commands.Choice(
            name="remove",
            value="remove",
        ),
    ]
)
async def whitelist(
    interaction: discord.Interaction,
    action: app_commands.Choice[str],
    user: discord.Member,
):
    if not await require_admin(
        interaction
    ):
        return

    if action.value == "add":
        await asyncio.to_thread(
            whitelist_service.add,
            interaction.guild_id,
            user.id,
            "user",
            "Configured by administrator",
            interaction.user.id,
        )

    else:
        await asyncio.to_thread(
            whitelist_service.remove,
            interaction.guild_id,
            user.id,
        )

    await interaction.response.send_message(
        (
            f"✅ Whitelist entry **{action.value}** "
            f"completed for {user.mention}."
        ),
        ephemeral=True,
    )


# ============================================================
# MODERATION
# ============================================================

async def moderate(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str,
    action: str,
):
    if not await require_moderator(
        interaction
    ):
        return

    if (
        member == interaction.user
        or member.top_role >= interaction.user.top_role
    ):
        await interaction.response.send_message(
            (
                "⚠️ You cannot moderate that member "
                "because of the Discord role hierarchy."
            ),
            ephemeral=True,
        )
        return

    try:
        if action == "warn":
            await asyncio.to_thread(
                bot.db.mod_logs.insert_one,
                {
                    "guild_id": interaction.guild_id,
                    "action": action,
                    "target_id": member.id,
                    "moderator_id": interaction.user.id,
                    "reason": reason,
                },
            )

        elif action == "timeout":
            await member.timeout(
                timedelta(minutes=10),
                reason=reason,
            )

        elif action == "kick":
            await member.kick(
                reason=reason
            )

        else:
            await member.ban(
                reason=reason
            )

        await interaction.response.send_message(
            embed=embed(
                f"🛡️ {action.title()} Applied",
                (
                    f"Target: {member.mention}\n"
                    f"Reason: {reason}"
                ),
            ),
            ephemeral=True,
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            (
                "❌ I don't have the required permission "
                "or role position to do that."
            ),
            ephemeral=True,
        )


def make_moderation_handler(
    action: str,
):
    async def handler(
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason provided",
    ):
        await moderate(
            interaction,
            member,
            reason,
            action,
        )

    return handler


for command_name, description, action in (
    (
        "warn",
        "Warn a member",
        "warn",
    ),
    (
        "timeout",
        "Timeout a member",
        "timeout",
    ),
    (
        "kick",
        "Kick a member",
        "kick",
    ),
    (
        "ban",
        "Ban a member",
        "ban",
    ),
):
    handler = make_moderation_handler(
        action
    )

    handler.__name__ = command_name

    mod_group.add_command(
        app_commands.Command(
            name=command_name,
            description=description,
            callback=handler,
        )
    )


# ============================================================
# LEVELING
# ============================================================

@level_group.command(
    name="rank",
    description="Show your level and XP",
)
async def rank(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    member = member or interaction.user

    profile = await asyncio.to_thread(
        bot.db.profiles.find_one,
        {
            "guild_id": interaction.guild_id,
            "user_id": member.id,
        },
    ) or {
        "xp": 0,
        "level": 0,
    }

    await interaction.response.send_message(
        embed=embed(
            f"⭐ {member.display_name}'s Rank",
            (
                f"Level: **{profile.get('level', 0)}**\n"
                f"XP: **{profile.get('xp', 0)}**"
            ),
        ),
        ephemeral=True,
    )


# ============================================================
# OWNER-ONLY SYSTEM
#
# IMPORTANT:
# Owner commands intentionally DO NOT use slash commands.
#
# They are:
#
# !owneronly
# @Bot owneronly
#
# and the same format applies to every owner command.
# ============================================================

def owner_denied_message() -> str:
    return (
        "🔒 **Access denied.**\n"
        "This command is reserved for the bot owner."
    )


def owner_help_text() -> str:
    return (
        "👑 **Titanium White — Owner Commands**\n\n"
        "🔹 `!owneronly`\n"
        "Show all owner-only commands.\n\n"
        "🔹 `!emoji list`\n"
        "Show every emoji currently used by the bot.\n\n"
        "🔹 `!emoji set <name> <emoji>`\n"
        "Change a bot emoji.\n\n"
        "🔹 `!emoji reset <name>`\n"
        "Reset an emoji to its default.\n\n"
        "🔹 `!emoji resetall`\n"
        "Reset every bot emoji.\n\n"
        "🔹 `!ownersync`\n"
        "Synchronise application commands.\n\n"
        "🔹 `!ownerguilds`\n"
        "Show all servers the bot is currently in.\n\n"
        "🔹 `!ownerleave <guild_id>`\n"
        "Make the bot leave a server.\n\n"
        "🔹 `!ownersay <channel> <message>`\n"
        "Send a message through the bot.\n\n"
        "🔹 `!shutdown`\n"
        "Shut down the bot.\n\n"
        "💡 You can replace `!` with a bot mention:\n"
        "`@Bot owneronly`\n"
        "`@Bot emoji list`"
    )


@bot.command(
    name="owneronly",
)
async def owneronly(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    await ctx.send(
        embed=embed(
            "👑 Owner Only",
            owner_help_text(),
        )
    )


# ============================================================
# OWNER EMOJI COMMAND
# ============================================================

@bot.group(
    name="emoji",
    invoke_without_command=True,
)
async def emoji_group(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    await ctx.send(
        (
            "👑 Emoji manager:\n"
            f"`{settings.bot_prefix}emoji list`\n"
            f"`{settings.bot_prefix}emoji set <name> <emoji>`\n"
            f"`{settings.bot_prefix}emoji reset <name>`\n"
            f"`{settings.bot_prefix}emoji resetall`"
        )
    )


@emoji_group.command(
    name="list",
)
async def emoji_list(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    emojis = await bot.get_emojis()

    lines = [
        f"`{name}` → {value}"
        for name, value in sorted(
            emojis.items()
        )
    ]

    description = (
        "\n".join(lines)
        or "No emojis are configured."
    )

    if len(description) > 3900:
        description = (
            description[:3900]
            + "\n\n… list truncated."
        )

    await ctx.send(
        embed=embed(
            "🎨 Bot Emoji List",
            (
                "These are the emojis currently used "
                "by Titanium White.\n\n"
                + description
            ),
        )
    )


@emoji_group.command(
    name="set",
)
async def emoji_set(
    ctx: commands.Context,
    name: str,
    emoji: str,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    name = name.lower().strip()

    if len(emoji) > 100:
        await ctx.send(
            "❌ That emoji value is too long."
        )
        return

    await bot.set_emoji(
        name,
        emoji,
    )

    await ctx.send(
        embed=embed(
            "🎨 Emoji Updated",
            (
                f"Name: `{name}`\n"
                f"New value: {emoji}\n\n"
                "This change has been saved."
            ),
        )
    )


@emoji_group.command(
    name="reset",
)
async def emoji_reset(
    ctx: commands.Context,
    name: str,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    name = name.lower().strip()

    await bot.reset_emoji(name)

    emojis = await bot.get_emojis()

    if name in emojis:
        value = emojis[name]

        await ctx.send(
            embed=embed(
                "♻️ Emoji Reset",
                (
                    f"`{name}` has been reset to "
                    f"{value}."
                ),
            )
        )

    else:
        await ctx.send(
            (
                f"♻️ `{name}` was not a default emoji "
                "and has been removed."
            )
        )


@emoji_group.command(
    name="resetall",
)
async def emoji_resetall(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    bot.emoji_cache = dict(
        DEFAULT_EMOJIS
    )

    await asyncio.to_thread(
        bot.db.bot_settings.update_one,
        {"key": "emojis"},
        {
            "$set": {
                "values": dict(DEFAULT_EMOJIS),
            }
        },
        upsert=True,
    )

    await ctx.send(
        "♻️ **All bot emojis have been restored to their defaults.**"
    )


# ============================================================
# OWNER SYNC
# ============================================================

@bot.command(
    name="ownersync",
)
async def ownersync(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    try:
        synced = await bot.tree.sync()

        await ctx.send(
            embed=embed(
                "🔄 Commands Synced",
                (
                    f"Successfully synchronised "
                    f"**{len(synced)}** application commands."
                ),
            )
        )

    except Exception as exc:
        logger.exception(
            "Owner sync failed"
        )

        await ctx.send(
            (
                "❌ Command synchronisation failed:\n"
                f"`{type(exc).__name__}: {exc}`"
            )
        )


# ============================================================
# OWNER GUILDS
# ============================================================

@bot.command(
    name="ownerguilds",
)
async def ownerguilds(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    if not bot.guilds:
        description = (
            "The bot is not currently in any servers."
        )

    else:
        lines = []

        for guild in bot.guilds:
            lines.append(
                (
                    f"🏠 **{guild.name}**\n"
                    f"ID: `{guild.id}`\n"
                    f"Members: `{guild.member_count or 0}`"
                )
            )

        description = "\n\n".join(lines)

    if len(description) > 3900:
        description = (
            description[:3900]
            + "\n\n… list truncated."
        )

    await ctx.send(
        embed=embed(
            f"🏠 Bot Guilds ({len(bot.guilds)})",
            description,
        )
    )


# ============================================================
# OWNER LEAVE
# ============================================================

@bot.command(
    name="ownerleave",
)
async def ownerleave(
    ctx: commands.Context,
    guild_id: str,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    try:
        target_id = int(guild_id)

    except ValueError:
        await ctx.send(
            "❌ Invalid guild ID."
        )
        return

    guild = bot.get_guild(target_id)

    if guild is None:
        await ctx.send(
            "❌ I am not currently in that server."
        )
        return

    guild_name = guild.name

    await guild.leave()

    await ctx.send(
        embed=embed(
            "👋 Left Server",
            (
                f"I left **{guild_name}** "
                f"(`{target_id}`)."
            ),
        )
    )


# ============================================================
# OWNER SAY
# ============================================================

@bot.command(
    name="ownersay",
)
async def ownersay(
    ctx: commands.Context,
    channel: discord.TextChannel,
    *,
    message: str,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    try:
        await channel.send(
            message[:2000]
        )

        await ctx.send(
            (
                f"✅ Message sent successfully "
                f"to {channel.mention}."
            )
        )

    except discord.Forbidden:
        await ctx.send(
            (
                "❌ I don't have permission to send "
                "messages in that channel."
            )
        )


# ============================================================
# OWNER SHUTDOWN
# ============================================================

@bot.command(
    name="shutdown",
)
async def shutdown(
    ctx: commands.Context,
):
    if not is_owner(
        ctx.author.id
    ):
        await ctx.send(
            owner_denied_message()
        )
        return

    await ctx.send(
        "🔴 **Titanium White is shutting down.**"
    )

    logger.warning(
        "Bot shutdown requested by owner %s (%s)",
        ctx.author,
        ctx.author.id,
    )

    await bot.close()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    bot.run(
        settings.discord_token
    )