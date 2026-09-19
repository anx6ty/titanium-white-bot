"""
Titanium White - all-in-one Discord bot (discord.py 2.x + MongoDB)

Features: greet voice onboarding, staff leave system, moderation + strikes,
automod (anti-spam / anti-link / anti-raid / anti-nuke), whitelist, /config hub,
leveling, tickets (+ transcripts), giveaways, join-to-create VCs, welcome /
goodbye, auto-roles, logging, TTS.

Required env vars : DISCORD_TOKEN, DATABASE_URL (or MONGODB_URI)
Optional env vars : DATABASE_NAME (default: titanium_white)
"""

import asyncio
import datetime as dt
import io
import os
import random
import re
import sys
import tempfile
import time
import traceback
from collections import defaultdict, deque
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from gtts import gTTS
from pymongo import MongoClient

try:
    import certifi
    TLS_CA = certifi.where()
except Exception:  # pragma: no cover
    TLS_CA = None

# ----------------------------------------------------------------------------
# Constants & small helpers
# ----------------------------------------------------------------------------

COLOR = 0x2B2D31
ROLE_NAME = "Not Welcomed"
START_TIME = time.time()
URL_RE = re.compile(r"(https?://|www\.|discord\.gg/|discord\.com/invite/)", re.I)
DUR_RE = re.compile(r"(\d+)\s*([smhdw])", re.I)
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
I = discord.Interaction


def E(title: Optional[str] = None, desc: Optional[str] = None) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=COLOR)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_db():
    uri = os.getenv("DATABASE_URL") or os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("Missing DATABASE_URL/MONGODB_URI")
    kwargs = {"serverSelectionTimeoutMS": 8000}
    if TLS_CA:
        kwargs["tlsCAFile"] = TLS_CA
    return MongoClient(uri, **kwargs)[os.getenv("DATABASE_NAME", "titanium_white")]


def dget(doc, path: str, default=None):
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def parse_duration(text: str) -> int:
    return sum(int(n) * UNITS[u.lower()] for n, u in DUR_RE.findall(text or ""))


def fmt_duration(sec: int) -> str:
    parts = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if sec >= size:
            parts.append(f"{sec // size}{name}")
            sec %= size
    return " ".join(parts) or "0s"


def xp_for_level(level: int) -> int:
    return 5 * level * level + 50 * level + 100


def level_info(total_xp: int):
    level, xp = 0, total_xp
    while xp >= xp_for_level(level):
        xp -= xp_for_level(level)
        level += 1
    return level, xp, xp_for_level(level)


def bar(cur: int, need: int, size: int = 12) -> str:
    filled = int(size * cur / max(need, 1))
    return "▰" * filled + "▱" * (size - filled)


def fmt_vars(text: str, member: discord.Member) -> str:
    return (
        text.replace("{user}", member.mention)
        .replace("{server}", member.guild.name)
        .replace("{count}", str(member.guild.member_count))
    )


# ----------------------------------------------------------------------------
# Async wrapper around blocking pymongo
# ----------------------------------------------------------------------------

class Store:
    def __init__(self, db):
        self.db = db

    async def find_one(self, col, query):
        return await asyncio.to_thread(self.db[col].find_one, query)

    async def find(self, col, query, sort=None, limit=0):
        def run():
            cur = self.db[col].find(query)
            if sort:
                cur = cur.sort(sort)
            if limit:
                cur = cur.limit(limit)
            return list(cur)
        return await asyncio.to_thread(run)

    async def update(self, col, query, update, upsert=True):
        await asyncio.to_thread(self.db[col].update_one, query, update, upsert)

    async def insert(self, col, doc):
        await asyncio.to_thread(self.db[col].insert_one, doc)

    async def delete(self, col, query):
        await asyncio.to_thread(self.db[col].delete_many, query)

    async def count(self, col, query) -> int:
        return await asyncio.to_thread(self.db[col].count_documents, query)


# ----------------------------------------------------------------------------
# Bot
# ----------------------------------------------------------------------------

class TitaniumWhiteBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

        self.store = Store(get_db())
        self._locks: dict[int, asyncio.Lock] = {}
        self._settings: dict[int, tuple[float, dict]] = {}
        self.spam = defaultdict(lambda: deque(maxlen=12))
        self.joins = defaultdict(lambda: deque(maxlen=60))
        self.nuke = defaultdict(lambda: deque(maxlen=60))
        self.xp_cd: dict[tuple[int, int], float] = {}
        self.afk: dict[tuple[int, int], tuple[str, float]] = {}

    # ---- settings ----------------------------------------------------------

    def guild_lock(self, gid: int) -> asyncio.Lock:
        return self._locks.setdefault(gid, asyncio.Lock())

    async def settings(self, gid: int) -> dict:
        cached = self._settings.get(gid)
        if cached and time.time() - cached[0] < 20:
            return cached[1]
        doc = await self.store.find_one("settings", {"guild_id": gid}) or {"guild_id": gid}
        self._settings[gid] = (time.time(), doc)
        return doc

    async def set_setting(self, gid: int, key: str, value):
        await self.store.update("settings", {"guild_id": gid}, {"$set": {key: value}})
        self._settings.pop(gid, None)

    async def unset_setting(self, gid: int, key: str):
        await self.store.update("settings", {"guild_id": gid}, {"$unset": {key: ""}})
        self._settings.pop(gid, None)

    async def push_setting(self, gid: int, key: str, value):
        await self.store.update("settings", {"guild_id": gid}, {"$addToSet": {key: value}})
        self._settings.pop(gid, None)

    async def pull_setting(self, gid: int, key: str, value):
        await self.store.update("settings", {"guild_id": gid}, {"$pull": {key: value}})
        self._settings.pop(gid, None)

    # ---- trust / whitelist ---------------------------------------------------

    async def is_trusted(self, member: discord.Member) -> bool:
        if member.id == member.guild.owner_id or member.id == self.user.id:
            return True
        s = await self.settings(member.guild.id)
        if member.id in dget(s, "whitelist.users", []):
            return True
        wl_roles = set(dget(s, "whitelist.roles", []))
        return any(r.id in wl_roles for r in member.roles)

    async def is_exempt(self, member: discord.Member) -> bool:
        if await self.is_trusted(member):
            return True
        p = member.guild_permissions
        return p.administrator or p.manage_messages

    # ---- logging ---------------------------------------------------------------

    async def log(self, guild: discord.Guild, kind: str, embed: discord.Embed):
        s = await self.settings(guild.id)
        cid = dget(s, f"logs.{kind}")
        channel = guild.get_channel(cid) if cid else None
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ---- lifecycle -------------------------------------------------------------

    async def setup_hook(self):
        self.tree.on_error = self.on_app_command_error
        self.add_view(TicketPanelView())
        self.add_view(TicketCloseView())
        self.add_view(LeavePanelView())
        self.add_view(GiveawayView())
        self.giveaway_loop.start()
        try:
            synced = await self.tree.sync()
            print(f"[bot] Synced {len(synced)} top-level application commands.")
        except Exception as exc:
            print(f"[bot] Slash command sync failed: {exc}")

    async def on_ready(self):
        print(f"[bot] Logged in as {self.user} ({self.user.id})")

    async def on_app_command_error(self, inter: I, error: app_commands.AppCommandError):
        orig = getattr(error, "original", error)
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You don't have the permissions required for this command."
        elif isinstance(orig, discord.Forbidden):
            msg = "I'm missing permissions, or my role is below the target's role."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "You can't use this command."
        elif type(orig).__name__ == "ServerSelectionTimeoutError":
            msg = "Database is unreachable. Check MongoDB Atlas Network Access (0.0.0.0/0)."
            print(f"[bot] DB error: {orig}")
        else:
            print(f"[bot] Command error in /{getattr(inter.command, 'name', '?')}:")
            traceback.print_exception(type(orig), orig, orig.__traceback__)
            msg = f"Something went wrong (`{type(orig).__name__}`)."
        try:
            if inter.response.is_done():
                await inter.followup.send(msg, ephemeral=True)
            else:
                await inter.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    # ---- giveaway background loop ----------------------------------------------

    @tasks.loop(seconds=15)
    async def giveaway_loop(self):
        try:
            docs = await self.store.find(
                "giveaways", {"ended": False, "end_time": {"$lte": time.time()}}
            )
            for doc in docs:
                await end_giveaway(doc)
        except Exception as exc:
            print(f"[bot] giveaway loop error: {exc}")

    @giveaway_loop.before_loop
    async def _before_giveaway_loop(self):
        await self.wait_until_ready()

    # ---- member events -----------------------------------------------------------

    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if member.bot:
            return
        s = await self.settings(guild.id)

        # anti-raid
        if dget(s, "security.antiraid", False) and not await self.is_trusted(member):
            now = time.time()
            dq = self.joins[guild.id]
            dq.append(now)
            if len([t for t in dq if now - t < 10]) >= dget(s, "security.raid_limit", 7):
                try:
                    await member.kick(reason="Anti-Raid: join flood")
                    await self.log(guild, "security", E("🛡️ Anti-Raid", f"Kicked {member} (`{member.id}`) during a join flood."))
                except discord.HTTPException:
                    pass
                return

        # welcome message
        if dget(s, "welcome.enabled", False):
            ch = guild.get_channel(dget(s, "welcome.channel", 0))
            if ch:
                text = dget(s, "welcome.message", "Welcome {user} to **{server}**! You are member #{count}.")
                try:
                    await ch.send(embed=E("Welcome", fmt_vars(text, member)))
                except discord.HTTPException:
                    pass

        # greet voice onboarding or plain auto-roles
        greet = await self.store.find_one("greetvoice_configs", {"guild_id": guild.id})
        if greet:
            try:
                role = await get_or_create_role(guild)
                await member.add_roles(role, reason="Onboarding greeting required")
            except discord.HTTPException as exc:
                print(f"[bot] Failed to add {ROLE_NAME}: {exc}")
        else:
            await self.give_autoroles(member)

    async def give_autoroles(self, member: discord.Member):
        s = await self.settings(member.guild.id)
        roles = [member.guild.get_role(r) for r in dget(s, "autoroles", [])]
        roles = [r for r in roles if r and r < member.guild.me.top_role]
        if roles:
            try:
                await member.add_roles(*roles, reason="Auto-role")
            except discord.HTTPException:
                pass

    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        s = await self.settings(guild.id)
        if dget(s, "goodbye.enabled", False):
            ch = guild.get_channel(dget(s, "goodbye.channel", 0))
            if ch:
                text = dget(s, "goodbye.message", "**{user}** left **{server}**. We are now {count} members.")
                try:
                    await ch.send(embed=E("Goodbye", fmt_vars(text, member)))
                except discord.HTTPException:
                    pass
        # anti-nuke: mass kicks
        if dget(s, "security.antinuke", False):
            actor = await audit_actor(guild, discord.AuditLogAction.kick, member.id)
            if actor:
                await self.nuke_guard(guild, actor, "kicking members")

    async def on_member_ban(self, guild: discord.Guild, user):
        s = await self.settings(guild.id)
        if dget(s, "security.antinuke", False):
            actor = await audit_actor(guild, discord.AuditLogAction.ban, user.id)
            if actor:
                await self.nuke_guard(guild, actor, "banning members")

    async def on_guild_channel_delete(self, channel):
        s = await self.settings(channel.guild.id)
        if dget(s, "security.antinuke", False):
            actor = await audit_actor(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
            if actor:
                await self.nuke_guard(channel.guild, actor, "deleting channels")

    async def on_guild_role_delete(self, role: discord.Role):
        s = await self.settings(role.guild.id)
        if dget(s, "security.antinuke", False):
            actor = await audit_actor(role.guild, discord.AuditLogAction.role_delete, role.id)
            if actor:
                await self.nuke_guard(role.guild, actor, "deleting roles")

    async def on_guild_channel_create(self, channel):
        # keep the "Not Welcomed" role locked out of newly created channels
        guild = channel.guild
        greet = await self.store.find_one("greetvoice_configs", {"guild_id": guild.id})
        role = discord.utils.get(guild.roles, name=ROLE_NAME)
        if greet and role and channel.id != greet.get("channel_id"):
            try:
                await channel.set_permissions(role, view_channel=False, connect=False, speak=False, send_messages=False)
            except discord.HTTPException:
                pass

    async def nuke_guard(self, guild: discord.Guild, actor: discord.abc.User, what: str):
        member = guild.get_member(actor.id)
        if member is None or await self.is_trusted(member):
            return
        s = await self.settings(guild.id)
        now = time.time()
        dq = self.nuke[(guild.id, actor.id)]
        dq.append(now)
        if len([t for t in dq if now - t < 15]) < dget(s, "security.nuke_limit", 3):
            return
        dq.clear()
        try:
            await guild.ban(actor, reason=f"Anti-Nuke: mass {what}")
            outcome = "banned"
        except discord.HTTPException:
            outcome = "could not be punished (check my role position)"
        await self.log(guild, "security", E("🛡️ Anti-Nuke", f"{actor} (`{actor.id}`) was caught {what} and {outcome}."))

    # ---- message events ------------------------------------------------------------

    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        s = await self.settings(message.guild.id)
        await self.handle_afk(message)
        if await self.automod(message, s):
            return
        await self.give_xp(message, s)

    async def handle_afk(self, message: discord.Message):
        key = (message.guild.id, message.author.id)
        if key in self.afk:
            del self.afk[key]
            try:
                await message.channel.send(f"Welcome back {message.author.mention}, I removed your AFK.", delete_after=8)
            except discord.HTTPException:
                pass
        for user in message.mentions:
            data = self.afk.get((message.guild.id, user.id))
            if data:
                try:
                    await message.channel.send(
                        f"**{user.display_name}** is AFK: {data[0]} (<t:{int(data[1])}:R>)", delete_after=10
                    )
                except discord.HTTPException:
                    pass

    async def automod(self, message: discord.Message, s: dict) -> bool:
        member = message.author
        if not isinstance(member, discord.Member) or await self.is_exempt(member):
            return False

        if dget(s, "security.antilink", False) and URL_RE.search(message.content):
            try:
                await message.delete()
                await message.channel.send(f"{member.mention} links are not allowed here.", delete_after=5)
            except discord.HTTPException:
                pass
            return True

        if dget(s, "security.antispam", False):
            now = time.time()
            dq = self.spam[(message.guild.id, member.id)]
            dq.append((now, message))
            recent = [m for t, m in dq if now - t < 5]
            if len(recent) >= dget(s, "security.spam_limit", 6):
                dq.clear()
                try:
                    await member.timeout(dt.timedelta(minutes=5), reason="Anti-Spam")
                    for m in recent:
                        try:
                            await m.delete()
                        except discord.HTTPException:
                            pass
                    await message.channel.send(f"{member.mention} was timed out for spamming.", delete_after=8)
                    await self.log(message.guild, "security", E("🛡️ Anti-Spam", f"Timed out {member} (`{member.id}`) for 5 minutes."))
                except discord.HTTPException:
                    pass
                return True
        return False

    async def give_xp(self, message: discord.Message, s: dict):
        if not dget(s, "leveling.enabled", True):
            return
        key = (message.guild.id, message.author.id)
        now = time.time()
        if now - self.xp_cd.get(key, 0) < 60:
            return
        self.xp_cd[key] = now
        q = {"guild_id": message.guild.id, "user_id": message.author.id}
        doc = await self.store.find_one("levels", q) or {}
        old_xp = doc.get("xp", 0)
        new_xp = old_xp + random.randint(15, 25)
        old_lvl, new_lvl = level_info(old_xp)[0], level_info(new_xp)[0]
        await self.store.update("levels", q, {"$set": {"xp": new_xp, "level": new_lvl}})
        if new_lvl > old_lvl:
            ch = message.guild.get_channel(dget(s, "leveling.channel", 0)) or message.channel
            try:
                await ch.send(f"🎉 {message.author.mention} reached **level {new_lvl}**!")
            except discord.HTTPException:
                pass

    # ---- voice events ----------------------------------------------------------------

    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        await self.tempvc_handler(member, before, after)
        await self.greet_handler(member, before, after)

    async def tempvc_handler(self, member, before, after):
        guild = member.guild
        s = await self.settings(guild.id)
        trigger = dget(s, "tempvc.trigger")
        if trigger and after.channel and after.channel.id == trigger:
            category = guild.get_channel(dget(s, "tempvc.category", 0)) or after.channel.category
            try:
                ch = await guild.create_voice_channel(f"{member.display_name}'s VC", category=category, reason="Temp VC")
                await self.store.insert("tempvcs", {"guild_id": guild.id, "channel_id": ch.id, "owner_id": member.id})
                await member.move_to(ch)
            except discord.HTTPException as exc:
                print(f"[bot] temp VC failed: {exc}")
        if before.channel and before.channel != after.channel:
            humans = [m for m in before.channel.members if not m.bot]
            if not humans and await self.store.find_one("tempvcs", {"channel_id": before.channel.id}):
                try:
                    await before.channel.delete(reason="Temp VC empty")
                except discord.HTTPException:
                    pass
                await self.store.delete("tempvcs", {"channel_id": before.channel.id})

    async def greet_handler(self, member, before, after):
        if before.channel == after.channel or after.channel is None:
            return
        config = await self.store.find_one("greetvoice_configs", {"guild_id": member.guild.id})
        if not config or after.channel.id != config.get("channel_id"):
            return
        role = discord.utils.get(member.guild.roles, name=ROLE_NAME)
        prompt = config.get("prompt")
        if role is None or role not in member.roles or not prompt:
            return
        async with self.guild_lock(member.guild.id):
            if member.voice is None or member.voice.channel != after.channel or role not in member.roles:
                return
            try:
                await speak(after.channel, prompt)
            except Exception as exc:
                print(f"[bot] Greet voice flow failed: {exc}")
                return
            try:
                await member.move_to(None)
            except discord.HTTPException:
                pass
            try:
                await member.remove_roles(role, reason="Welcome flow completed")
            except discord.HTTPException as exc:
                print(f"[bot] Failed to remove {ROLE_NAME}: {exc}")
            await self.give_autoroles(member)


bot = TitaniumWhiteBot()


# ----------------------------------------------------------------------------
# Shared helper functions
# ----------------------------------------------------------------------------

async def get_or_create_role(guild: discord.Guild) -> discord.Role:
    role = discord.utils.get(guild.roles, name=ROLE_NAME)
    if role is None:
        role = await guild.create_role(name=ROLE_NAME, reason="Onboarding greeting")
    return role


async def apply_greet_permissions(guild: discord.Guild, role: discord.Role, greet_channel):
    for target in guild.channels:
        if target.id == greet_channel.id:
            continue
        try:
            await target.set_permissions(role, view_channel=False, connect=False, speak=False, send_messages=False)
        except discord.HTTPException:
            pass
    try:
        await greet_channel.set_permissions(role, view_channel=True, connect=True, speak=True)
    except discord.HTTPException as exc:
        print(f"[bot] Failed to set greet channel permissions: {exc}")


async def speak(channel: discord.VoiceChannel, text: str):
    """Text-to-speech in a voice channel (caller should hold the guild lock)."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp:
        path = temp.name
    try:
        await asyncio.to_thread(lambda: gTTS(text=text[:500], lang="en").save(path))
        voice = channel.guild.voice_client
        joined_here = False
        if voice is None:
            voice = await channel.connect()
            joined_here = True
        elif voice.channel != channel:
            await voice.move_to(channel)
        try:
            if voice.is_playing():
                voice.stop()
            voice.play(discord.FFmpegPCMAudio(path))
            while voice.is_playing():
                await asyncio.sleep(0.5)
        finally:
            if joined_here:
                await voice.disconnect(force=True)
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


async def audit_actor(guild: discord.Guild, action, target_id: int):
    try:
        async for entry in guild.audit_logs(limit=6, action=action):
            if (discord.utils.utcnow() - entry.created_at).total_seconds() > 15:
                continue
            if getattr(entry.target, "id", None) == target_id:
                return entry.user
    except discord.HTTPException:
        return None
    return None


def hierarchy_ok(inter: I, target: discord.Member) -> bool:
    guild = inter.guild
    if target.id == guild.owner_id or target.id == inter.user.id:
        return False
    if inter.user.id != guild.owner_id and inter.user.top_role <= target.top_role:
        return False
    return guild.me.top_role > target.top_role


async def add_strike(guild, member, mod, reason):
    await bot.store.insert("warnings", {
        "guild_id": guild.id, "user_id": member.id,
        "mod_id": mod.id if mod else None, "reason": reason, "time": time.time(),
    })
    count = await bot.store.count("warnings", {"guild_id": guild.id, "user_id": member.id})
    s = await bot.settings(guild.id)
    limit = dget(s, "security.strike_limit", 3)
    action = dget(s, "security.strike_action", "kick")
    result = None
    if count >= limit:
        try:
            if action == "ban":
                await guild.ban(member, reason=f"Strike limit reached ({count})")
                result = "banned"
            else:
                await member.kick(reason=f"Strike limit reached ({count})")
                result = "kicked"
        except discord.HTTPException:
            result = "action failed (check my permissions)"
    return count, result


async def build_transcript(channel: discord.TextChannel) -> str:
    lines = []
    async for m in channel.history(limit=2000, oldest_first=True):
        text = m.content or ""
        if m.attachments:
            text += " " + " ".join(a.url for a in m.attachments)
        lines.append(f"[{m.created_at:%Y-%m-%d %H:%M:%S}] {m.author} ({m.author.id}): {text}")
    return "\n".join(lines) or "(empty)"


def slash(name: str, description: str, perms: Optional[dict] = None, group: Optional[app_commands.Group] = None):
    """Register a slash command (top-level or inside a group) with permission checks."""
    def deco(fn):
        if perms:
            fn = app_commands.checks.has_permissions(**perms)(fn)
            if group is None:
                fn = app_commands.default_permissions(**perms)(fn)
        if group is None:
            fn = app_commands.guild_only(fn)
            return bot.tree.command(name=name, description=description)(fn)
        return group.command(name=name, description=description)(fn)
    return deco


def make_group(name: str, description: str) -> app_commands.Group:
    grp = app_commands.Group(name=name, description=description, guild_only=True)
    bot.tree.add_command(grp)
    return grp


# ----------------------------------------------------------------------------
# Views / modals
# ----------------------------------------------------------------------------

class LeaveModal(discord.ui.Modal, title="Staff Leave Request"):
    duration = discord.ui.TextInput(label="Leave Duration", placeholder="e.g. 3 days", max_length=50)
    start = discord.ui.TextInput(label="Start Date", placeholder="e.g. 25 Sep 2026", max_length=50)
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, max_length=800)

    async def on_submit(self, inter: I):
        s = await bot.settings(inter.guild.id)
        channel = inter.guild.get_channel(dget(s, "leave_log", 0))
        if channel is None:
            return await inter.response.send_message("Leave logging channel is not set. Ask an admin to run `/leavelogging`.", ephemeral=True)
        embed = E("📝 Staff Leave Request")
        embed.add_field(name="Staff", value=inter.user.mention, inline=True)
        embed.add_field(name="Duration", value=str(self.duration), inline=True)
        embed.add_field(name="Start Date", value=str(self.start), inline=True)
        embed.add_field(name="Reason", value=str(self.reason), inline=False)
        embed.set_thumbnail(url=inter.user.display_avatar.url)
        await channel.send(embed=embed)
        await bot.store.insert("leaves", {
            "guild_id": inter.guild.id, "user_id": inter.user.id, "duration": str(self.duration),
            "start": str(self.start), "reason": str(self.reason), "time": time.time(),
        })
        await inter.response.send_message("Your leave request has been submitted.", ephemeral=True)


class LeavePanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Leave", style=discord.ButtonStyle.primary, custom_id="tw:leave:create")
    async def create(self, inter: I, button: discord.ui.Button):
        await inter.response.send_modal(LeaveModal())


async def create_ticket(inter: I):
    guild, user = inter.guild, inter.user
    s = await bot.settings(guild.id)
    existing = await bot.store.find_one("tickets", {"guild_id": guild.id, "user_id": user.id, "open": True})
    if existing and guild.get_channel(existing["channel_id"]):
        return await inter.response.send_message(f"You already have a ticket: <#{existing['channel_id']}>", ephemeral=True)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    support = guild.get_role(dget(s, "ticket.support_role", 0))
    if support:
        overwrites[support] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    category = guild.get_channel(dget(s, "ticket.category", 0))
    await inter.response.defer(ephemeral=True)
    channel = await guild.create_text_channel(
        f"ticket-{user.name}"[:90], category=category if isinstance(category, discord.CategoryChannel) else None,
        overwrites=overwrites, reason="Ticket created",
    )
    await bot.store.insert("tickets", {"guild_id": guild.id, "user_id": user.id, "channel_id": channel.id, "open": True, "time": time.time()})
    embed = E("🎫 Ticket", f"Hello {user.mention}, a staff member will be with you shortly.\nPress the button below to close this ticket.")
    await channel.send(content=(support.mention if support else None), embed=embed, view=TicketCloseView())
    await inter.followup.send(f"Ticket created: {channel.mention}", ephemeral=True)


async def close_ticket(inter: I):
    doc = await bot.store.find_one("tickets", {"channel_id": inter.channel.id, "open": True})
    if not doc:
        return await inter.response.send_message("This is not an open ticket.", ephemeral=True)
    s = await bot.settings(inter.guild.id)
    role_id = dget(s, "ticket.support_role", 0)
    allowed = (
        inter.user.id == doc["user_id"]
        or inter.user.guild_permissions.manage_channels
        or any(r.id == role_id for r in inter.user.roles)
    )
    if not allowed:
        return await inter.response.send_message("You can't close this ticket.", ephemeral=True)
    await inter.response.send_message("Closing this ticket in 5 seconds...")
    text = await build_transcript(inter.channel)
    log_ch = inter.guild.get_channel(dget(s, "ticket.log", 0))
    if log_ch:
        file = discord.File(io.BytesIO(text.encode("utf-8")), filename=f"transcript-{inter.channel.name}.txt")
        await log_ch.send(embed=E("Ticket closed", f"Channel: `{inter.channel.name}`\nOpened by: <@{doc['user_id']}>\nClosed by: {inter.user.mention}"), file=file)
    await bot.store.update("tickets", {"channel_id": inter.channel.id}, {"$set": {"open": False}}, upsert=False)
    await asyncio.sleep(5)
    await inter.channel.delete(reason="Ticket closed")


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="tw:ticket:create")
    async def create(self, inter: I, button: discord.ui.Button):
        await create_ticket(inter)


class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="tw:ticket:close")
    async def close(self, inter: I, button: discord.ui.Button):
        await close_ticket(inter)


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Enter", emoji="🎉", style=discord.ButtonStyle.success, custom_id="tw:giveaway:enter")
    async def enter(self, inter: I, button: discord.ui.Button):
        doc = await bot.store.find_one("giveaways", {"message_id": inter.message.id})
        if not doc or doc.get("ended"):
            return await inter.response.send_message("This giveaway has ended.", ephemeral=True)
        if inter.user.id in doc.get("participants", []):
            await bot.store.update("giveaways", {"message_id": inter.message.id}, {"$pull": {"participants": inter.user.id}}, upsert=False)
            return await inter.response.send_message("You left the giveaway.", ephemeral=True)
        await bot.store.update("giveaways", {"message_id": inter.message.id}, {"$addToSet": {"participants": inter.user.id}}, upsert=False)
        await inter.response.send_message("You entered the giveaway. Good luck!", ephemeral=True)


async def end_giveaway(doc: dict, reroll: bool = False):
    guild = bot.get_guild(doc["guild_id"])
    channel = guild.get_channel(doc["channel_id"]) if guild else None
    participants = doc.get("participants", [])
    winners = random.sample(participants, min(len(participants), doc.get("winners", 1))) if participants else []
    mentions = ", ".join(f"<@{w}>" for w in winners) or "No valid entries"
    await bot.store.update("giveaways", {"message_id": doc["message_id"]}, {"$set": {"ended": True, "winner_ids": winners}}, upsert=False)
    if channel is None:
        return
    try:
        msg = await channel.fetch_message(doc["message_id"])
        embed = E(f"🎉 {doc['prize']}", f"**Ended**\nWinner(s): {mentions}\nHosted by: <@{doc['host_id']}>")
        await msg.edit(embed=embed, view=None)
    except discord.HTTPException:
        pass
    await channel.send(f"{'🔁 Reroll: ' if reroll else '🎉 '}Winner(s) of **{doc['prize']}**: {mentions}")


# ---- /config hub ------------------------------------------------------------

CONFIG_CATS = ["Security", "Leveling", "Audio", "Tickets", "Auto-roles", "Logging", "Welcome"]
TOGGLES = {
    "Security": [
        ("security.antispam", "Anti-Spam"), ("security.antilink", "Anti-Link"),
        ("security.antiraid", "Anti-Raid"), ("security.antinuke", "Anti-Nuke"),
    ],
    "Leveling": [("leveling.enabled", "Leveling")],
    "Welcome": [("welcome.enabled", "Welcome"), ("goodbye.enabled", "Goodbye")],
}


def onoff(v) -> str:
    return "🟢 On" if v else "🔴 Off"


def ch_mention(v) -> str:
    return f"<#{v}>" if v else "not set"


async def config_embed(guild: discord.Guild, cat: str) -> discord.Embed:
    s = await bot.settings(guild.id)
    embed = E(f"⚙️ Config · {cat}")
    if cat == "Security":
        embed.add_field(name="Anti-Spam", value=onoff(dget(s, "security.antispam")), inline=True)
        embed.add_field(name="Anti-Link", value=onoff(dget(s, "security.antilink")), inline=True)
        embed.add_field(name="Anti-Raid", value=onoff(dget(s, "security.antiraid")), inline=True)
        embed.add_field(name="Anti-Nuke", value=onoff(dget(s, "security.antinuke")), inline=True)
        embed.add_field(name="Strikes", value=f"{dget(s, 'security.strike_limit', 3)} → {dget(s, 'security.strike_action', 'kick')}", inline=True)
        embed.set_footer(text="Use /automod and /whitelist for detailed settings")
    elif cat == "Leveling":
        embed.add_field(name="Leveling", value=onoff(dget(s, "leveling.enabled", True)), inline=True)
        embed.add_field(name="Level-up channel", value=ch_mention(dget(s, "leveling.channel")), inline=True)
        embed.set_footer(text="Use /level for changes")
    elif cat == "Audio":
        greet = await bot.store.find_one("greetvoice_configs", {"guild_id": guild.id}) or {}
        embed.add_field(name="TTS", value="🟢 Available via `/tts`", inline=False)
        embed.add_field(name="Greet voice", value=ch_mention(greet.get("channel_id")), inline=False)
        embed.add_field(name="Music (Lavalink)", value="Not configured", inline=False)
    elif cat == "Tickets":
        embed.add_field(name="Category", value=ch_mention(dget(s, "ticket.category")), inline=True)
        embed.add_field(name="Log channel", value=ch_mention(dget(s, "ticket.log")), inline=True)
        role = dget(s, "ticket.support_role")
        embed.add_field(name="Support role", value=f"<@&{role}>" if role else "not set", inline=True)
        embed.set_footer(text="Use /ticket setup to change")
    elif cat == "Auto-roles":
        roles = dget(s, "autoroles", [])
        embed.description = ", ".join(f"<@&{r}>" for r in roles) or "No auto-roles set. Use `/autorole add`."
    elif cat == "Logging":
        embed.add_field(name="Mod log", value=ch_mention(dget(s, "logs.mod")), inline=True)
        embed.add_field(name="Security log", value=ch_mention(dget(s, "logs.security")), inline=True)
        embed.add_field(name="Leave log", value=ch_mention(dget(s, "leave_log")), inline=True)
        embed.set_footer(text="Use /logs set and /leavelogging to change")
    elif cat == "Welcome":
        embed.add_field(name="Welcome", value=f"{onoff(dget(s, 'welcome.enabled'))} · {ch_mention(dget(s, 'welcome.channel'))}", inline=False)
        embed.add_field(name="Goodbye", value=f"{onoff(dget(s, 'goodbye.enabled'))} · {ch_mention(dget(s, 'goodbye.channel'))}", inline=False)
        embed.set_footer(text="Variables: {user} {server} {count}")
    return embed


class ConfigSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(
            placeholder="Choose a category",
            options=[discord.SelectOption(label=c, default=(c == current)) for c in CONFIG_CATS],
        )

    async def callback(self, inter: I):
        await self.view.show(inter, self.values[0])


class ToggleButton(discord.ui.Button):
    def __init__(self, key: str, label: str, on: bool):
        super().__init__(label=f"{label}: {'ON' if on else 'OFF'}",
                         style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary)
        self.key = key

    async def callback(self, inter: I):
        s = await bot.settings(inter.guild.id)
        default = self.key == "leveling.enabled"
        await bot.set_setting(inter.guild.id, self.key, not dget(s, self.key, default))
        await self.view.show(inter, self.view.cat)


class ConfigView(discord.ui.View):
    def __init__(self, owner_id: int, cat: str, settings: dict):
        super().__init__(timeout=300)
        self.owner_id, self.cat = owner_id, cat
        self.add_item(ConfigSelect(cat))
        for key, label in TOGGLES.get(cat, []):
            self.add_item(ToggleButton(key, label, bool(dget(settings, key, key == "leveling.enabled"))))

    async def interaction_check(self, inter: I) -> bool:
        if inter.user.id != self.owner_id:
            await inter.response.send_message("Only the person who ran /config can use this menu.", ephemeral=True)
            return False
        return True

    async def show(self, inter: I, cat: str):
        settings = await bot.settings(inter.guild.id)
        await inter.response.edit_message(
            embed=await config_embed(inter.guild, cat),
            view=ConfigView(self.owner_id, cat, settings),
        )


# ----------------------------------------------------------------------------
# Commands: greet voice, leave, config, embed, help & utility
# ----------------------------------------------------------------------------

@slash("greetvoice", "Configure onboarding greet voice", perms=dict(administrator=True))
@app_commands.describe(channel="Voice channel to greet in", prompt="Text to convert to speech")
async def greetvoice(inter: I, channel: discord.VoiceChannel, prompt: str):
    await inter.response.defer()
    await bot.store.update("greetvoice_configs", {"guild_id": inter.guild.id},
                           {"$set": {"guild_id": inter.guild.id, "channel_id": channel.id, "prompt": prompt}})
    role = await get_or_create_role(inter.guild)
    await apply_greet_permissions(inter.guild, role, channel)
    embed = E("Greet Voice Enabled")
    embed.add_field(name="Channel", value=channel.mention, inline=False)
    embed.add_field(name="Prompt", value=prompt[:1024], inline=False)
    await inter.followup.send(embed=embed)
    try:
        if inter.guild.voice_client is None:
            await channel.connect()
        elif inter.guild.voice_client.channel != channel:
            await inter.guild.voice_client.move_to(channel)
    except discord.HTTPException as exc:
        print(f"[bot] Could not auto-join greet channel: {exc}")


@slash("setup-leave", "Post the staff leave panel here", perms=dict(administrator=True))
async def setup_leave(inter: I):
    embed = E("Staff Leave", "Need time off? Press the button below and fill in the form.")
    await inter.channel.send(embed=embed, view=LeavePanelView())
    await inter.response.send_message("Leave panel posted.", ephemeral=True)


@slash("leavelogging", "Set the channel where leave requests are sent", perms=dict(administrator=True))
async def leavelogging(inter: I, channel: discord.TextChannel):
    await bot.set_setting(inter.guild.id, "leave_log", channel.id)
    await inter.response.send_message(f"Leave requests will be sent to {channel.mention}.", ephemeral=True)


@slash("leave", "Submit a staff leave request")
async def leave_cmd(inter: I):
    await inter.response.send_modal(LeaveModal())


@slash("config", "Open the server configuration hub", perms=dict(manage_guild=True))
async def config_cmd(inter: I):
    settings = await bot.settings(inter.guild.id)
    await inter.response.send_message(
        embed=await config_embed(inter.guild, "Security"),
        view=ConfigView(inter.user.id, "Security", settings),
        ephemeral=True,
    )


@slash("embed", "Create and send a custom embed", perms=dict(manage_guild=True))
@app_commands.describe(title="Embed title", description="Embed text", color="Hex color like #2B2D31", channel="Where to send it")
async def embed_cmd(inter: I, title: str, description: str, color: str = "#2B2D31", channel: Optional[discord.TextChannel] = None):
    try:
        value = int(color.lstrip("#"), 16)
    except ValueError:
        value = COLOR
    target = channel or inter.channel
    await target.send(embed=discord.Embed(title=title, description=description.replace("\\n", "\n"), color=value))
    await inter.response.send_message("Embed sent.", ephemeral=True)


HELP_TEXT = {
    "Admin": "`/greetvoice` `/setup-leave` `/leavelogging` `/config` `/embed` `/whitelist` `/automod` `/logs` `/role`",
    "Moderation": "`/warn` `/warnings` `/clearwarns` `/timeout` `/mute` `/untimeout` `/unmute` `/kick` `/ban` `/unban` `/clear` `/slowmode` `/lock` `/unlock` `/nick`",
    "Community": "`/rank` `/leaderboard` `/level` `/ticket` `/giveaway` `/tempvc` `/welcome` `/goodbye` `/autorole` `/leave` `/afk`",
    "Voice": "`/tts` (music needs a Lavalink node)",
    "Info & fun": "`/help` `/ping` `/botinfo` `/serverinfo` `/userinfo` `/avatar` `/roleinfo` `/membercount` `/8ball` `/coinflip` `/dice` `/choose`",
}


@slash("help", "Show all commands")
async def help_cmd(inter: I):
    total = sum(1 for c in bot.tree.walk_commands() if isinstance(c, app_commands.Command))
    embed = E("Titanium White", f"{total} commands available.")
    for name, text in HELP_TEXT.items():
        embed.add_field(name=name, value=text, inline=False)
    await inter.response.send_message(embed=embed, ephemeral=True)


@slash("ping", "Check bot latency")
async def ping(inter: I):
    await inter.response.send_message(embed=E("Pong", f"{round(bot.latency * 1000)} ms"))


@slash("botinfo", "Information about the bot")
async def botinfo(inter: I):
    embed = E("Titanium White")
    embed.add_field(name="Servers", value=str(len(bot.guilds)))
    embed.add_field(name="Latency", value=f"{round(bot.latency * 1000)} ms")
    embed.add_field(name="Uptime", value=fmt_duration(int(time.time() - START_TIME)))
    await inter.response.send_message(embed=embed)


@slash("serverinfo", "Information about this server")
async def serverinfo(inter: I):
    g = inter.guild
    embed = E(g.name)
    embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
    embed.add_field(name="Members", value=str(g.member_count))
    embed.add_field(name="Channels", value=str(len(g.channels)))
    embed.add_field(name="Roles", value=str(len(g.roles)))
    embed.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"))
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await inter.response.send_message(embed=embed)


@slash("userinfo", "Information about a member")
async def userinfo(inter: I, member: Optional[discord.Member] = None):
    m = member or inter.user
    embed = E(str(m))
    embed.add_field(name="ID", value=str(m.id))
    embed.add_field(name="Joined", value=discord.utils.format_dt(m.joined_at, "R") if m.joined_at else "?")
    embed.add_field(name="Created", value=discord.utils.format_dt(m.created_at, "R"))
    embed.add_field(name="Top role", value=m.top_role.mention)
    embed.set_thumbnail(url=m.display_avatar.url)
    await inter.response.send_message(embed=embed)


@slash("avatar", "Show a member's avatar")
async def avatar(inter: I, member: Optional[discord.Member] = None):
    m = member or inter.user
    await inter.response.send_message(embed=E(f"{m.display_name}'s avatar").set_image(url=m.display_avatar.url))


@slash("roleinfo", "Information about a role")
async def roleinfo(inter: I, role: discord.Role):
    embed = E(role.name)
    embed.add_field(name="ID", value=str(role.id))
    embed.add_field(name="Members", value=str(len(role.members)))
    embed.add_field(name="Color", value=str(role.color))
    embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, "R"))
    await inter.response.send_message(embed=embed)


@slash("membercount", "Show the member count")
async def membercount(inter: I):
    await inter.response.send_message(embed=E("Members", str(inter.guild.member_count)))


@slash("afk", "Set yourself as AFK")
async def afk_cmd(inter: I, reason: str = "AFK"):
    bot.afk[(inter.guild.id, inter.user.id)] = (reason[:100], time.time())
    await inter.response.send_message(f"You are now AFK: {reason[:100]}", ephemeral=True)


@slash("8ball", "Ask the magic 8-ball")
async def eight_ball(inter: I, question: str):
    answers = ["Yes.", "No.", "Maybe.", "Definitely.", "Ask again later.", "Very doubtful.", "Without a doubt.", "Don't count on it."]
    await inter.response.send_message(embed=E(f"🎱 {question[:200]}", random.choice(answers)))


@slash("coinflip", "Flip a coin")
async def coinflip(inter: I):
    await inter.response.send_message(embed=E("Coin flip", random.choice(["Heads", "Tails"])))


@slash("dice", "Roll a dice")
async def dice(inter: I, sides: app_commands.Range[int, 2, 1000] = 6):
    await inter.response.send_message(embed=E(f"🎲 d{sides}", str(random.randint(1, sides))))


@slash("choose", "Choose between options (comma separated)")
async def choose(inter: I, options: str):
    items = [o.strip() for o in options.split(",") if o.strip()]
    if len(items) < 2:
        return await inter.response.send_message("Give at least two options separated by commas.", ephemeral=True)
    await inter.response.send_message(embed=E("I choose", random.choice(items)))


@slash("tts", "Speak text in your voice channel")
async def tts_cmd(inter: I, text: app_commands.Range[str, 1, 300]):
    if not isinstance(inter.user, discord.Member) or inter.user.voice is None or inter.user.voice.channel is None:
        return await inter.response.send_message("Join a voice channel first.", ephemeral=True)
    await inter.response.send_message("🔊 Speaking...", ephemeral=True)
    async with bot.guild_lock(inter.guild.id):
        await speak(inter.user.voice.channel, text)


# ----------------------------------------------------------------------------
# Moderation
# ----------------------------------------------------------------------------

async def deny_target(inter: I, target: discord.Member) -> bool:
    if not hierarchy_ok(inter, target):
        await inter.response.send_message("You (or I) can't act on that member because of role hierarchy.", ephemeral=True)
        return True
    return False


async def mod_report(inter: I, title: str, target, reason: str, extra: str = ""):
    embed = E(title)
    embed.add_field(name="Member", value=f"{target} (`{target.id}`)", inline=False)
    embed.add_field(name="Moderator", value=inter.user.mention, inline=True)
    embed.add_field(name="Reason", value=reason[:1000], inline=True)
    if extra:
        embed.add_field(name="Details", value=extra, inline=False)
    await inter.response.send_message(embed=embed)
    await bot.log(inter.guild, "mod", embed)


@slash("warn", "Warn a member (strikes can auto-kick/ban)", perms=dict(moderate_members=True))
async def warn(inter: I, member: discord.Member, reason: str = "No reason provided"):
    if await deny_target(inter, member):
        return
    count, result = await add_strike(inter.guild, member, inter.user, reason)
    try:
        await member.send(embed=E(f"You were warned in {inter.guild.name}", f"Reason: {reason}\nStrikes: {count}"))
    except discord.HTTPException:
        pass
    await mod_report(inter, "⚠️ Warning", member, reason, f"Strikes: {count}" + (f"\nAuto action: {result}" if result else ""))


@slash("warnings", "Show a member's warnings", perms=dict(moderate_members=True))
async def warnings_cmd(inter: I, member: discord.Member):
    docs = await bot.store.find("warnings", {"guild_id": inter.guild.id, "user_id": member.id}, sort=[("time", -1)], limit=10)
    if not docs:
        return await inter.response.send_message(f"{member} has no warnings.", ephemeral=True)
    lines = []
    for i, d in enumerate(docs):
        by = f" by <@{d['mod_id']}>" if d.get("mod_id") else ""
        lines.append(f"`{i + 1}.` <t:{int(d['time'])}:d>{by} — {d['reason']}")
    await inter.response.send_message(embed=E(f"Warnings · {member}", "\n".join(lines)), ephemeral=True)


@slash("clearwarns", "Clear all warnings of a member", perms=dict(moderate_members=True))
async def clearwarns(inter: I, member: discord.Member):
    await bot.store.delete("warnings", {"guild_id": inter.guild.id, "user_id": member.id})
    await inter.response.send_message(f"Cleared warnings for {member}.", ephemeral=True)


async def do_timeout(inter: I, member: discord.Member, duration: str, reason: str):
    if await deny_target(inter, member):
        return
    seconds = parse_duration(duration)
    if seconds <= 0 or seconds > 28 * 86400:
        return await inter.response.send_message("Use a duration like `10m`, `2h` or `1d` (max 28d).", ephemeral=True)
    await member.timeout(dt.timedelta(seconds=seconds), reason=reason)
    await mod_report(inter, "🔇 Timeout", member, reason, f"Duration: {fmt_duration(seconds)}")


@slash("timeout", "Timeout a member", perms=dict(moderate_members=True))
@app_commands.describe(duration="e.g. 10m, 2h, 1d")
async def timeout_cmd(inter: I, member: discord.Member, duration: str, reason: str = "No reason provided"):
    await do_timeout(inter, member, duration, reason)


@slash("mute", "Mute (timeout) a member", perms=dict(moderate_members=True))
async def mute(inter: I, member: discord.Member, duration: str = "1h", reason: str = "No reason provided"):
    await do_timeout(inter, member, duration, reason)


async def do_untimeout(inter: I, member: discord.Member):
    if await deny_target(inter, member):
        return
    await member.timeout(None, reason=f"Untimeout by {inter.user}")
    await mod_report(inter, "🔊 Timeout removed", member, "Timeout removed")


@slash("untimeout", "Remove a member's timeout", perms=dict(moderate_members=True))
async def untimeout(inter: I, member: discord.Member):
    await do_untimeout(inter, member)


@slash("unmute", "Unmute a member", perms=dict(moderate_members=True))
async def unmute(inter: I, member: discord.Member):
    await do_untimeout(inter, member)


@slash("kick", "Kick a member", perms=dict(kick_members=True))
async def kick(inter: I, member: discord.Member, reason: str = "No reason provided"):
    if await deny_target(inter, member):
        return
    await member.kick(reason=f"{inter.user}: {reason}")
    await mod_report(inter, "👢 Kick", member, reason)


@slash("ban", "Ban a user", perms=dict(ban_members=True))
async def ban(inter: I, user: discord.User, reason: str = "No reason provided"):
    member = inter.guild.get_member(user.id)
    if member and await deny_target(inter, member):
        return
    await inter.guild.ban(user, reason=f"{inter.user}: {reason}")
    await mod_report(inter, "🔨 Ban", user, reason)


@slash("unban", "Unban a user by ID", perms=dict(ban_members=True))
async def unban(inter: I, user_id: str, reason: str = "No reason provided"):
    if not user_id.isdigit():
        return await inter.response.send_message("Give a numeric user ID.", ephemeral=True)
    try:
        await inter.guild.unban(discord.Object(id=int(user_id)), reason=f"{inter.user}: {reason}")
    except discord.NotFound:
        return await inter.response.send_message("That user is not banned.", ephemeral=True)
    await inter.response.send_message(f"Unbanned `{user_id}`.")


@slash("clear", "Delete recent messages", perms=dict(manage_messages=True))
async def clear(inter: I, amount: app_commands.Range[int, 1, 100]):
    await inter.response.defer(ephemeral=True)
    deleted = await inter.channel.purge(limit=amount)
    await inter.followup.send(f"Deleted {len(deleted)} messages.", ephemeral=True)


@slash("slowmode", "Set channel slowmode in seconds", perms=dict(manage_channels=True))
async def slowmode(inter: I, seconds: app_commands.Range[int, 0, 21600]):
    await inter.channel.edit(slowmode_delay=seconds)
    await inter.response.send_message(f"Slowmode set to {seconds}s.")


@slash("lock", "Lock a channel for @everyone", perms=dict(manage_channels=True))
async def lock(inter: I, channel: Optional[discord.TextChannel] = None):
    ch = channel or inter.channel
    await ch.set_permissions(inter.guild.default_role, send_messages=False)
    await inter.response.send_message(f"🔒 {ch.mention} locked.")


@slash("unlock", "Unlock a channel for @everyone", perms=dict(manage_channels=True))
async def unlock(inter: I, channel: Optional[discord.TextChannel] = None):
    ch = channel or inter.channel
    await ch.set_permissions(inter.guild.default_role, send_messages=None)
    await inter.response.send_message(f"🔓 {ch.mention} unlocked.")


@slash("nick", "Change a member's nickname", perms=dict(manage_nicknames=True))
async def nick(inter: I, member: discord.Member, nickname: Optional[str] = None):
    if await deny_target(inter, member):
        return
    await member.edit(nick=nickname, reason=f"By {inter.user}")
    await inter.response.send_message(f"Nickname updated for {member}.", ephemeral=True)


# ---- role management -----------------------------------------------------------

role_group = make_group("role", "Manage member roles")


def role_ok(inter: I, role: discord.Role) -> bool:
    if role >= inter.guild.me.top_role or role.is_default() or role.managed:
        return False
    return inter.user.id == inter.guild.owner_id or role < inter.user.top_role


@slash("add", "Give a role to a member", perms=dict(manage_roles=True), group=role_group)
async def role_add(inter: I, member: discord.Member, role: discord.Role):
    if not role_ok(inter, role):
        return await inter.response.send_message("I can't manage that role (hierarchy).", ephemeral=True)
    await member.add_roles(role, reason=f"By {inter.user}")
    await inter.response.send_message(f"Added {role.mention} to {member.mention}.", allowed_mentions=discord.AllowedMentions.none())


@slash("remove", "Remove a role from a member", perms=dict(manage_roles=True), group=role_group)
async def role_remove(inter: I, member: discord.Member, role: discord.Role):
    if not role_ok(inter, role):
        return await inter.response.send_message("I can't manage that role (hierarchy).", ephemeral=True)
    await member.remove_roles(role, reason=f"By {inter.user}")
    await inter.response.send_message(f"Removed {role.mention} from {member.mention}.", allowed_mentions=discord.AllowedMentions.none())


# ----------------------------------------------------------------------------
# Security: whitelist, automod, logs
# ----------------------------------------------------------------------------

wl_group = make_group("whitelist", "Bypass anti-nuke and automod")
ADMIN = dict(administrator=True)


@slash("add-user", "Whitelist a user", perms=ADMIN, group=wl_group)
async def wl_add_user(inter: I, user: discord.Member):
    await bot.push_setting(inter.guild.id, "whitelist.users", user.id)
    await inter.response.send_message(f"Whitelisted {user.mention}.", ephemeral=True)


@slash("remove-user", "Remove a user from the whitelist", perms=ADMIN, group=wl_group)
async def wl_remove_user(inter: I, user: discord.Member):
    await bot.pull_setting(inter.guild.id, "whitelist.users", user.id)
    await inter.response.send_message(f"Removed {user.mention} from the whitelist.", ephemeral=True)


@slash("add-role", "Whitelist a role", perms=ADMIN, group=wl_group)
async def wl_add_role(inter: I, role: discord.Role):
    await bot.push_setting(inter.guild.id, "whitelist.roles", role.id)
    await inter.response.send_message(f"Whitelisted {role.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@slash("remove-role", "Remove a role from the whitelist", perms=ADMIN, group=wl_group)
async def wl_remove_role(inter: I, role: discord.Role):
    await bot.pull_setting(inter.guild.id, "whitelist.roles", role.id)
    await inter.response.send_message(f"Removed {role.mention} from the whitelist.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@slash("list", "Show the whitelist", perms=ADMIN, group=wl_group)
async def wl_list(inter: I):
    s = await bot.settings(inter.guild.id)
    users = ", ".join(f"<@{u}>" for u in dget(s, "whitelist.users", [])) or "none"
    roles = ", ".join(f"<@&{r}>" for r in dget(s, "whitelist.roles", [])) or "none"
    await inter.response.send_message(embed=E("Whitelist", f"**Users:** {users}\n**Roles:** {roles}"), ephemeral=True)


am_group = make_group("automod", "Security and automod settings")


@slash("status", "Show security settings", perms=ADMIN, group=am_group)
async def am_status(inter: I):
    await inter.response.send_message(embed=await config_embed(inter.guild, "Security"), ephemeral=True)


def make_toggle(key: str, label: str, desc: str):
    @slash(key, desc, perms=ADMIN, group=am_group)
    async def _toggle(inter: I, enabled: bool):
        await bot.set_setting(inter.guild.id, f"security.{key}", enabled)
        await inter.response.send_message(f"{label} is now {'enabled' if enabled else 'disabled'}.", ephemeral=True)
    return _toggle


make_toggle("antispam", "Anti-Spam", "Enable or disable anti-spam")
make_toggle("antilink", "Anti-Link", "Enable or disable anti-link")
make_toggle("antiraid", "Anti-Raid", "Enable or disable anti-raid")
make_toggle("antinuke", "Anti-Nuke", "Enable or disable anti-nuke")


@slash("strikes", "Set the strike limit and the action taken", perms=ADMIN, group=am_group)
async def am_strikes(inter: I, limit: app_commands.Range[int, 1, 20], action: Literal["kick", "ban"]):
    await bot.set_setting(inter.guild.id, "security.strike_limit", limit)
    await bot.set_setting(inter.guild.id, "security.strike_action", action)
    await inter.response.send_message(f"At {limit} strikes members will be {action}ed.", ephemeral=True)


@slash("limits", "Set spam / raid / nuke thresholds", perms=ADMIN, group=am_group)
@app_commands.describe(spam="Messages in 5s", raid="Joins in 10s", nuke="Destructive actions in 15s")
async def am_limits(inter: I, spam: app_commands.Range[int, 3, 30] = 6, raid: app_commands.Range[int, 3, 50] = 7, nuke: app_commands.Range[int, 2, 20] = 3):
    await bot.set_setting(inter.guild.id, "security.spam_limit", spam)
    await bot.set_setting(inter.guild.id, "security.raid_limit", raid)
    await bot.set_setting(inter.guild.id, "security.nuke_limit", nuke)
    await inter.response.send_message(f"Limits saved: spam {spam}, raid {raid}, nuke {nuke}.", ephemeral=True)


logs_group = make_group("logs", "Logging channels")


@slash("set", "Set a log channel", perms=ADMIN, group=logs_group)
async def logs_set(inter: I, kind: Literal["mod", "security"], channel: discord.TextChannel):
    await bot.set_setting(inter.guild.id, f"logs.{kind}", channel.id)
    await inter.response.send_message(f"{kind.title()} logs will go to {channel.mention}.", ephemeral=True)


@slash("disable", "Disable a log channel", perms=ADMIN, group=logs_group)
async def logs_disable(inter: I, kind: Literal["mod", "security"]):
    await bot.unset_setting(inter.guild.id, f"logs.{kind}")
    await inter.response.send_message(f"{kind.title()} logs disabled.", ephemeral=True)


# ----------------------------------------------------------------------------
# Welcome, goodbye, auto-roles
# ----------------------------------------------------------------------------

def make_greeting_group(kind: str):
    grp = make_group(kind, f"Configure {kind} messages")

    @slash("set", f"Enable {kind} messages", perms=dict(manage_guild=True), group=grp)
    @app_commands.describe(channel="Channel", message="Use {user} {server} {count}")
    async def _set(inter: I, channel: discord.TextChannel, message: Optional[str] = None):
        await bot.set_setting(inter.guild.id, f"{kind}.enabled", True)
        await bot.set_setting(inter.guild.id, f"{kind}.channel", channel.id)
        if message:
            await bot.set_setting(inter.guild.id, f"{kind}.message", message)
        await inter.response.send_message(f"{kind.title()} messages enabled in {channel.mention}.", ephemeral=True)

    @slash("test", f"Preview the {kind} message", perms=dict(manage_guild=True), group=grp)
    async def _test(inter: I):
        s = await bot.settings(inter.guild.id)
        default = "Welcome {user} to **{server}**! You are member #{count}." if kind == "welcome" else "**{user}** left **{server}**."
        text = dget(s, f"{kind}.message", default)
        await inter.response.send_message(embed=E(kind.title(), fmt_vars(text, inter.user)), ephemeral=True)

    @slash("disable", f"Disable {kind} messages", perms=dict(manage_guild=True), group=grp)
    async def _disable(inter: I):
        await bot.set_setting(inter.guild.id, f"{kind}.enabled", False)
        await inter.response.send_message(f"{kind.title()} messages disabled.", ephemeral=True)


make_greeting_group("welcome")
make_greeting_group("goodbye")

autorole_group = make_group("autorole", "Roles given automatically on join")


@slash("add", "Add an auto-role", perms=dict(manage_roles=True), group=autorole_group)
async def autorole_add(inter: I, role: discord.Role):
    if not role_ok(inter, role):
        return await inter.response.send_message("I can't assign that role (hierarchy).", ephemeral=True)
    await bot.push_setting(inter.guild.id, "autoroles", role.id)
    await inter.response.send_message(f"{role.mention} will be given to new members.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@slash("remove", "Remove an auto-role", perms=dict(manage_roles=True), group=autorole_group)
async def autorole_remove(inter: I, role: discord.Role):
    await bot.pull_setting(inter.guild.id, "autoroles", role.id)
    await inter.response.send_message(f"Removed {role.mention} from auto-roles.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@slash("list", "List auto-roles", perms=dict(manage_roles=True), group=autorole_group)
async def autorole_list(inter: I):
    s = await bot.settings(inter.guild.id)
    roles = ", ".join(f"<@&{r}>" for r in dget(s, "autoroles", [])) or "none"
    await inter.response.send_message(embed=E("Auto-roles", roles), ephemeral=True)


# ----------------------------------------------------------------------------
# Leveling
# ----------------------------------------------------------------------------

@slash("rank", "Show your level and XP")
async def rank(inter: I, member: Optional[discord.Member] = None):
    m = member or inter.user
    doc = await bot.store.find_one("levels", {"guild_id": inter.guild.id, "user_id": m.id}) or {}
    xp = doc.get("xp", 0)
    level, cur, need = level_info(xp)
    position = await bot.store.count("levels", {"guild_id": inter.guild.id, "xp": {"$gt": xp}}) + 1
    embed = E(f"{m.display_name}", f"Level **{level}** · Rank **#{position}**\n{bar(cur, need)} `{cur}/{need} XP`\nTotal XP: `{xp}`")
    embed.set_thumbnail(url=m.display_avatar.url)
    await inter.response.send_message(embed=embed)


@slash("leaderboard", "Top members by XP")
async def leaderboard(inter: I):
    docs = await bot.store.find("levels", {"guild_id": inter.guild.id}, sort=[("xp", -1)], limit=10)
    if not docs:
        return await inter.response.send_message("No XP data yet.", ephemeral=True)
    lines = [f"`{i + 1}.` <@{d['user_id']}> — level {level_info(d['xp'])[0]} (`{d['xp']} XP`)" for i, d in enumerate(docs)]
    await inter.response.send_message(embed=E("🏆 Leaderboard", "\n".join(lines)), allowed_mentions=discord.AllowedMentions.none())


level_group = make_group("level", "Leveling settings")


@slash("channel", "Set the level-up announcement channel", perms=dict(manage_guild=True), group=level_group)
async def level_channel(inter: I, channel: discord.TextChannel):
    await bot.set_setting(inter.guild.id, "leveling.channel", channel.id)
    await inter.response.send_message(f"Level-ups will be announced in {channel.mention}.", ephemeral=True)


@slash("toggle", "Enable or disable leveling", perms=dict(manage_guild=True), group=level_group)
async def level_toggle(inter: I, enabled: bool):
    await bot.set_setting(inter.guild.id, "leveling.enabled", enabled)
    await inter.response.send_message(f"Leveling {'enabled' if enabled else 'disabled'}.", ephemeral=True)


@slash("setxp", "Set a member's XP", perms=dict(manage_guild=True), group=level_group)
async def level_setxp(inter: I, member: discord.Member, xp: app_commands.Range[int, 0, 10_000_000]):
    await bot.store.update("levels", {"guild_id": inter.guild.id, "user_id": member.id}, {"$set": {"xp": xp, "level": level_info(xp)[0]}})
    await inter.response.send_message(f"Set {member.mention} to {xp} XP.", ephemeral=True)


@slash("reset", "Reset a member's XP", perms=dict(manage_guild=True), group=level_group)
async def level_reset(inter: I, member: discord.Member):
    await bot.store.delete("levels", {"guild_id": inter.guild.id, "user_id": member.id})
    await inter.response.send_message(f"Reset XP for {member.mention}.", ephemeral=True)


# ----------------------------------------------------------------------------
# Tickets
# ----------------------------------------------------------------------------

ticket_group = make_group("ticket", "Ticket system")


@slash("setup", "Post the ticket panel and configure tickets", perms=ADMIN, group=ticket_group)
@app_commands.describe(channel="Where to post the panel", category="Category for new tickets", support_role="Staff role", log_channel="Transcript channel")
async def ticket_setup(inter: I, channel: discord.TextChannel, category: Optional[discord.CategoryChannel] = None,
                       support_role: Optional[discord.Role] = None, log_channel: Optional[discord.TextChannel] = None):
    gid = inter.guild.id
    if category:
        await bot.set_setting(gid, "ticket.category", category.id)
    if support_role:
        await bot.set_setting(gid, "ticket.support_role", support_role.id)
    if log_channel:
        await bot.set_setting(gid, "ticket.log", log_channel.id)
    await channel.send(embed=E("Support", "Need help? Press the button below to open a ticket."), view=TicketPanelView())
    await inter.response.send_message("Ticket panel posted.", ephemeral=True)


@slash("create", "Open a ticket", group=ticket_group)
async def ticket_create(inter: I):
    await create_ticket(inter)


@slash("close", "Close the current ticket", group=ticket_group)
async def ticket_close(inter: I):
    await close_ticket(inter)


@slash("add", "Add a member to this ticket", perms=dict(manage_channels=True), group=ticket_group)
async def ticket_add(inter: I, member: discord.Member):
    if not await bot.store.find_one("tickets", {"channel_id": inter.channel.id, "open": True}):
        return await inter.response.send_message("This is not an open ticket.", ephemeral=True)
    await inter.channel.set_permissions(member, view_channel=True, send_messages=True)
    await inter.response.send_message(f"Added {member.mention}.")


@slash("remove", "Remove a member from this ticket", perms=dict(manage_channels=True), group=ticket_group)
async def ticket_remove(inter: I, member: discord.Member):
    if not await bot.store.find_one("tickets", {"channel_id": inter.channel.id, "open": True}):
        return await inter.response.send_message("This is not an open ticket.", ephemeral=True)
    await inter.channel.set_permissions(member, overwrite=None)
    await inter.response.send_message(f"Removed {member.mention}.")


# ----------------------------------------------------------------------------
# Giveaways
# ----------------------------------------------------------------------------

giveaway_group = make_group("giveaway", "Giveaways")
MANAGE = dict(manage_guild=True)


@slash("start", "Start a giveaway", perms=MANAGE, group=giveaway_group)
@app_commands.describe(prize="What is being given away", duration="e.g. 30m, 2h, 1d", winners="Number of winners")
async def giveaway_start(inter: I, prize: str, duration: str, winners: app_commands.Range[int, 1, 20] = 1, channel: Optional[discord.TextChannel] = None):
    seconds = parse_duration(duration)
    if seconds < 10:
        return await inter.response.send_message("Duration must be at least 10 seconds (e.g. `30m`, `2h`).", ephemeral=True)
    target = channel or inter.channel
    end_time = time.time() + seconds
    embed = E(f"🎉 {prize}", f"Press **Enter** to join!\nEnds: <t:{int(end_time)}:R>\nWinners: **{winners}**\nHosted by: {inter.user.mention}")
    msg = await target.send(embed=embed, view=GiveawayView())
    await bot.store.insert("giveaways", {
        "guild_id": inter.guild.id, "channel_id": target.id, "message_id": msg.id, "prize": prize,
        "end_time": end_time, "winners": winners, "participants": [], "ended": False, "host_id": inter.user.id,
    })
    await inter.response.send_message(f"Giveaway started in {target.mention}.", ephemeral=True)


@slash("end", "End a giveaway now", perms=MANAGE, group=giveaway_group)
@app_commands.describe(message_id="ID of the giveaway message")
async def giveaway_end(inter: I, message_id: str):
    doc = await bot.store.find_one("giveaways", {"guild_id": inter.guild.id, "message_id": int(message_id) if message_id.isdigit() else 0})
    if not doc or doc.get("ended"):
        return await inter.response.send_message("No active giveaway with that message ID.", ephemeral=True)
    await inter.response.send_message("Ending giveaway...", ephemeral=True)
    await end_giveaway(doc)


@slash("reroll", "Pick new winners for an ended giveaway", perms=MANAGE, group=giveaway_group)
async def giveaway_reroll(inter: I, message_id: str):
    doc = await bot.store.find_one("giveaways", {"guild_id": inter.guild.id, "message_id": int(message_id) if message_id.isdigit() else 0})
    if not doc or not doc.get("ended"):
        return await inter.response.send_message("No ended giveaway with that message ID.", ephemeral=True)
    await inter.response.send_message("Rerolling...", ephemeral=True)
    await end_giveaway(doc, reroll=True)


@slash("list", "List active giveaways", perms=MANAGE, group=giveaway_group)
async def giveaway_list(inter: I):
    docs = await bot.store.find("giveaways", {"guild_id": inter.guild.id, "ended": False})
    if not docs:
        return await inter.response.send_message("No active giveaways.", ephemeral=True)
    lines = [f"**{d['prize']}** — ends <t:{int(d['end_time'])}:R> — ID `{d['message_id']}`" for d in docs]
    await inter.response.send_message(embed=E("Active giveaways", "\n".join(lines)), ephemeral=True)


# ----------------------------------------------------------------------------
# Temporary voice channels
# ----------------------------------------------------------------------------

tempvc_group = make_group("tempvc", "Join-to-create voice channels")


@slash("setup", "Set the join-to-create voice channel", perms=ADMIN, group=tempvc_group)
async def tempvc_setup(inter: I, trigger: discord.VoiceChannel, category: Optional[discord.CategoryChannel] = None):
    await bot.set_setting(inter.guild.id, "tempvc.trigger", trigger.id)
    if category:
        await bot.set_setting(inter.guild.id, "tempvc.category", category.id)
    await inter.response.send_message(f"Joining {trigger.mention} now creates a temporary channel.", ephemeral=True)


@slash("disable", "Disable join-to-create channels", perms=ADMIN, group=tempvc_group)
async def tempvc_disable(inter: I):
    await bot.unset_setting(inter.guild.id, "tempvc.trigger")
    await inter.response.send_message("Temporary voice channels disabled.", ephemeral=True)


# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------

def main():
    try:
        token = require_env("DISCORD_TOKEN")
    except Exception as exc:
        print(f"[bot] Startup failed: {exc}")
        sys.exit(1)
    bot.run(token)


if __name__ == "__main__":
    main()
