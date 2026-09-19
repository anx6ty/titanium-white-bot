from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from app.command_suite import install as install_command_suite
from app.config import settings
from app.database.mongo import get_db, mongo
from app.log import logger
from app.services.permissions import require_admin, require_moderator, safe_send
from app.services.whitelist import whitelist_service

COLOR = discord.Color.from_str("#2B2D31")


def embed(title: str, description: str = "", *, colour: discord.Color = COLOR) -> discord.Embed:
    return discord.Embed(title=title, description=description, colour=colour)


class ConfigView(discord.ui.View):
    def __init__(self, bot: "TitaniumBot"):
        super().__init__(timeout=180)
        self.bot = bot
        self.add_item(ConfigSelect(bot))


class ConfigSelect(discord.ui.Select):
    def __init__(self, bot: "TitaniumBot"):
        self.bot = bot
        options = [discord.SelectOption(label=x, value=x.lower()) for x in ("Security", "Leveling", "Audio", "Tickets", "Welcome", "Logging")]
        super().__init__(placeholder="Choose a configuration category", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not await require_admin(interaction):
            return
        data = await self.bot.guild_config(interaction.guild_id)
        key = self.values[0]
        value = data.get(f"{key}_enabled", data.get("automod_enabled", True))
        await interaction.response.send_message(embed=embed(f"{key.title()} configuration", f"Enabled: **{value}**\nUse `/config toggle` to change feature flags."), ephemeral=True)


class LeaveView(discord.ui.View):
    def __init__(self, bot: "TitaniumBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Leave", style=discord.ButtonStyle.primary, custom_id="leave:create")
    async def create(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(LeaveModal(self.bot))


class LeaveModal(discord.ui.Modal, title="Staff Leave Request"):
    duration = discord.ui.TextInput(label="Leave duration", placeholder="e.g. 3 days", max_length=100)
    reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, max_length=1000)
    start_date = discord.ui.TextInput(label="Start date", placeholder="YYYY-MM-DD", max_length=20)

    def __init__(self, bot: "TitaniumBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        config = await self.bot.guild_config(interaction.guild_id)
        channel = interaction.guild.get_channel(config.get("leave_log_channel")) if config.get("leave_log_channel") else None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Leave logging is not configured.", ephemeral=True)
            return
        card = embed("Staff Leave Request", f"Requested by {interaction.user.mention}")
        card.add_field(name="Duration", value=self.duration.value, inline=True)
        card.add_field(name="Start date", value=self.start_date.value, inline=True)
        card.add_field(name="Reason", value=self.reason.value, inline=False)
        await channel.send(embed=card)
        await interaction.response.send_message("Your leave request was submitted.", ephemeral=True)


class TitaniumBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        intents.message_content = True
        super().__init__(command_prefix=settings.bot_prefix, intents=intents)
        self.db = get_db()
        self.guild_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.spam: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self.started_at = time.monotonic()

    async def setup_hook(self):
        mongo.ensure_indexes()
        self.add_view(LeaveView(self))
        install_command_suite(self)
        await self.tree.sync()
        logger.info("Application commands synchronised")

    async def guild_config(self, guild_id: int) -> dict:
        value = await asyncio.to_thread(self.db.guild_settings.find_one, {"guild_id": guild_id})
        if value:
            return value
        defaults = {"guild_id": guild_id, "automod_enabled": True, "leveling_enabled": True, "music_enabled": True, "tickets_enabled": True}
        await asyncio.to_thread(self.db.guild_settings.update_one, {"guild_id": guild_id}, {"$setOnInsert": defaults}, upsert=True)
        return defaults

    async def set_config(self, guild_id: int, **values):
        await asyncio.to_thread(self.db.guild_settings.update_one, {"guild_id": guild_id}, {"$set": values}, upsert=True)

    async def on_ready(self):
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def on_member_join(self, member: discord.Member):
        config = await self.guild_config(member.guild.id)
        for role_id in config.get("auto_roles", []):
            role = member.guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Configured auto-role")
                except discord.HTTPException:
                    logger.warning("Unable to apply auto-role in %s", member.guild.id)
        channel = member.guild.get_channel(config.get("welcome_channel")) if config.get("welcome_channel") else None
        if isinstance(channel, discord.TextChannel):
            await channel.send(embed=embed("Welcome", f"Welcome {member.mention} to **{member.guild.name}**."))

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        config = await self.guild_config(message.guild.id)
        if config.get("automod_enabled", True) and not whitelist_service.is_whitelisted(message.guild.id, message.author.id):
            key = (message.guild.id, message.author.id)
            now = time.monotonic()
            bucket = self.spam[key]
            bucket.append(now)
            while bucket and now - bucket[0] > 8:
                bucket.popleft()
            blocked = len(bucket) >= 7 or (("http://" in message.content.lower() or "https://" in message.content.lower()) and config.get("anti_links", False))
            if blocked:
                try:
                    await message.delete()
                    await message.author.timeout(timedelta(minutes=1), reason="Automod violation")
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning("Automod could not act in %s", message.guild.id)
                return
        await self.process_commands(message)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error("Application command failed: %s", error)
        await safe_send(interaction, "The command could not be completed. Check my permissions and try again.")


bot = TitaniumBot()

admin_group = app_commands.Group(name="config", description="Manage server configuration")
mod_group = app_commands.Group(name="mod", description="Moderation tools")
level_group = app_commands.Group(name="level", description="Leveling tools")
bot.tree.add_command(admin_group)
bot.tree.add_command(mod_group)
bot.tree.add_command(level_group)


@bot.tree.command(name="help", description="Show Titanium White features")
async def help_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=embed("Titanium White", "Use `/manage` for the expanded command suite."), ephemeral=True)


@bot.tree.command(name="ping", description="Show bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(embed=embed("Pong", f"WebSocket: `{round(bot.latency * 1000)}ms`"), ephemeral=True)


@bot.tree.command(name="uptime", description="Show how long the bot has been online")
async def uptime(interaction: discord.Interaction):
    await interaction.response.send_message(embed=embed("Uptime", f"`{int(time.monotonic() - bot.started_at)} seconds`"), ephemeral=True)


@admin_group.command(name="panel", description="Open the interactive configuration panel")
async def config_panel(interaction: discord.Interaction):
    if await require_admin(interaction):
        await interaction.response.send_message(embed=embed("Titanium Configuration", "Choose a category below."), view=ConfigView(bot), ephemeral=True)


@admin_group.command(name="toggle", description="Enable or disable a feature")
@app_commands.describe(feature="Feature name", enabled="Whether the feature is enabled")
async def config_toggle(interaction: discord.Interaction, feature: str, enabled: bool):
    if not await require_admin(interaction):
        return
    allowed = {"automod", "leveling", "music", "tickets", "anti_links"}
    if feature not in allowed:
        await interaction.response.send_message(f"Feature must be one of: {', '.join(sorted(allowed))}.", ephemeral=True)
        return
    await bot.set_config(interaction.guild_id, **{f"{feature}_enabled" if feature != "anti_links" else feature: enabled})
    await interaction.response.send_message(f"`{feature}` is now **{'enabled' if enabled else 'disabled'}**.", ephemeral=True)


@bot.tree.command(name="setgreetvoice", description="Configure restricted voice onboarding")
@app_commands.describe(channel="Onboarding voice channel", tts_text="Greeting text")
async def setgreetvoice(interaction: discord.Interaction, channel: discord.VoiceChannel, tts_text: str):
    if await require_admin(interaction):
        await bot.set_config(interaction.guild_id, greet_channel=channel.id, greet_text=tts_text[:500])
        await interaction.response.send_message(embed=embed("Onboarding voice configured", f"Channel: {channel.mention}\nText: {tts_text[:500]}"), ephemeral=True)


@admin_group.command(name="leavelogging", description="Set the leave request log channel")
async def leavelogging(interaction: discord.Interaction, channel: discord.TextChannel):
    if await require_admin(interaction):
        await bot.set_config(interaction.guild_id, leave_log_channel=channel.id)
        await interaction.response.send_message(f"Leave requests will be sent to {channel.mention}.", ephemeral=True)


@admin_group.command(name="setup_leave", description="Post the staff leave application panel")
async def setup_leave(interaction: discord.Interaction):
    if not await require_admin(interaction):
        return
    if isinstance(interaction.channel, discord.TextChannel):
        await interaction.channel.send(embed=embed("Staff Leave", "Submit a leave request using the button below."), view=LeaveView(bot))
    await interaction.response.send_message("Leave panel posted.", ephemeral=True)


@admin_group.command(name="whitelist", description="Add or remove an automod whitelist entry")
@app_commands.describe(action="add or remove", user="User to whitelist")
@app_commands.choices(action=[app_commands.Choice(name="add", value="add"), app_commands.Choice(name="remove", value="remove")])
async def whitelist(interaction: discord.Interaction, action: app_commands.Choice[str], user: discord.Member):
    if not await require_admin(interaction):
        return
    if action.value == "add":
        await asyncio.to_thread(whitelist_service.add, interaction.guild_id, user.id, "user", "Configured by administrator", interaction.user.id)
    else:
        await asyncio.to_thread(whitelist_service.remove, interaction.guild_id, user.id)
    await interaction.response.send_message(f"Whitelist entry **{action.value}** completed for {user.mention}.", ephemeral=True)


async def moderate(interaction: discord.Interaction, member: discord.Member, reason: str, action: str):
    if not await require_moderator(interaction):
        return
    if member == interaction.user or member.top_role >= interaction.user.top_role:
        await interaction.response.send_message("You cannot moderate that member due to role hierarchy.", ephemeral=True)
        return
    try:
        if action == "warn":
            await asyncio.to_thread(bot.db.mod_logs.insert_one, {"guild_id": interaction.guild_id, "action": action, "target_id": member.id, "moderator_id": interaction.user.id, "reason": reason})
        elif action == "timeout":
            await member.timeout(timedelta(minutes=10), reason=reason)
        elif action == "kick":
            await member.kick(reason=reason)
        else:
            await member.ban(reason=reason)
        await interaction.response.send_message(embed=embed(f"{action.title()} applied", f"Target: {member.mention}\nReason: {reason}"), ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("I do not have the required permission or role position.", ephemeral=True)


for command_name, description, action in (("warn", "Warn a member", "warn"), ("timeout", "Timeout a member", "timeout"), ("kick", "Kick a member", "kick"), ("ban", "Ban a member", "ban")):
    async def handler(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided", _action=action):
        await moderate(interaction, member, reason, _action)
    handler.__name__ = command_name
    mod_group.add_command(app_commands.Command(name=command_name, description=description, callback=handler))


@level_group.command(name="rank", description="Show your level and XP")
async def rank(interaction: discord.Interaction, member: discord.Member | None = None):
    member = member or interaction.user
    profile = await asyncio.to_thread(bot.db.profiles.find_one, {"guild_id": interaction.guild_id, "user_id": member.id}) or {"xp": 0, "level": 0}
    await interaction.response.send_message(embed=embed(f"{member.display_name}'s rank", f"Level: **{profile.get('level', 0)}**\nXP: **{profile.get('xp', 0)}**"), ephemeral=True)


if __name__ == "__main__":
    bot.run(settings.discord_token)
