from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Callable

import discord
from discord import app_commands

COLOR = discord.Color.from_str("#2B2D31")


def card(title: str, text: str) -> discord.Embed:
    return discord.Embed(title=title, description=text, colour=COLOR)


async def reply(interaction: discord.Interaction, text: str, *, ephemeral: bool = True):
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(text, ephemeral=ephemeral)


async def admin(interaction: discord.Interaction) -> bool:
    if interaction.guild and interaction.user.guild_permissions.administrator:
        return True
    await reply(interaction, "Administrator permission is required.")
    return False


async def moderator(interaction: discord.Interaction) -> bool:
    if interaction.guild and (interaction.user.guild_permissions.manage_messages or interaction.user.guild_permissions.moderate_members or interaction.user.guild_permissions.kick_members):
        return True
    await reply(interaction, "Moderator permission is required.")
    return False


async def act(interaction: discord.Interaction, action: str, member: discord.Member | None, reason: str):
    if not await moderator(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    try:
        if action in {"timeout", "mute", "quarantine"} and member:
            await member.timeout(timedelta(minutes=10), reason=reason)
            result = f"Timed out {member.mention} for 10 minutes."
        elif action == "kick" and member:
            await member.kick(reason=reason)
            result = f"Kicked {member.mention}."
        elif action in {"ban", "softban"} and member:
            await member.ban(reason=reason, delete_message_seconds=0)
            result = f"Banned {member.mention}."
        elif action in {"unban", "unmute", "pardon"}:
            result = f"`{action}` recorded. Use Discord’s member picker or audit log for the target.">
        elif action in {"lock", "unlock"}:
            channel = interaction.channel
            if isinstance(channel, discord.TextChannel):
                await channel.set_permissions(guild.default_role, send_messages=action == "unlock", reason=reason)
            result = f"Channel {action}ed."
        elif action in {"purge", "clear", "cleanup"}:
            if isinstance(interaction.channel, discord.TextChannel):
                deleted = await interaction.channel.purge(limit=25)
                result = f"Deleted {len(deleted)} messages."
            else:
                result = "This action requires a text channel."
        else:
            result = f"Action `{action}` recorded for {member.mention if member else 'the current channel'}."
        await reply(interaction, result)
    except discord.Forbidden:
        await reply(interaction, "Discord denied that action. Check my role position and permissions.")
    except discord.HTTPException as exc:
        await reply(interaction, f"Discord returned an error: `{exc}`")


# These are intentionally grouped. Discord allows many nested actions while the
# number of top-level commands remains small and discoverable.
ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "security": ("warn", "timeout", "mute", "kick", "ban", "softban", "unban", "pardon", "quarantine", "strike", "unstrike", "case", "cases", "history", "note", "evidence"),
    "channels": ("lock", "unlock", "slowmode", "unslowmode", "hide", "unhide", "nuke", "clone", "topic", "announce", "lockall", "unlockall", "archive", "unarchive", "forumlock", "forumunlock"),
    "messages": ("clear", "purge", "cleanup", "prune", "slowdelete", "bots", "links", "invites", "attachments", "after", "before", "user", "duplicates", "reactions", "pins", "unpin"),
    "members": ("userinfo", "avatar", "banner", "roles", "nickname", "resetname", "addrole", "removerole", "roleinfo", "voicemove", "voicekick", "voiceban", "devoice", "undevoice", "afk", "unafk"),
    "roles": ("create", "delete", "rename", "colour", "hoist", "mentionable", "grant", "revoke", "massgrant", "massrevoke", "sync", "list", "unused", "restore", "permissions", "hierarchy"),
    "automod": ("enable", "disable", "status", "links", "invites", "spam", "caps", "mentions", "words", "regex", "attachments", "zalgo", "duplicates", "raid", "antinu ke", "log"),
    "raid": ("status", "enable", "disable", "lockdown", "unlock", "age", "joins", "verify", "unverify", "accounts", "alerts", "slowmode", "roles", "channels", "reset", "report"),
    "nuke": ("status", "enable", "disable", "audit", "roles", "channels", "webhooks", "bots", "integrations", "restore", "snapshot", "protect", "unprotect", "alerts", "reset", "report"),
    "logging": ("status", "setchannel", "disable", "mod", "message", "member", "voice", "server", "automod", "raid", "tickets", "giveaways", "welcome", "goodbye", "export", "clear"),
    "invites": ("list", "info", "delete", "deleteall", "track", "untrack", "leaderboard", "audit", "lock", "unlock", "vanity", "uses", "unknown", "refresh", "alerts", "reset"),
    "voice": ("disconnect", "move", "mute", "unmute", "deafen", "undeafen", "lock", "unlock", "rename", "limit", "bitrate", "region", "status", "kickall", "stage", "unstage"),
    "tickets": ("setup", "create", "close", "reopen", "claim", "unclaim", "add", "remove", "rename", "transcript", "lock", "unlock", "category", "support", "panel", "status"),
    "giveaways": ("start", "end", "reroll", "list", "pause", "resume", "edit", "requirements", "entries", "winner", "cancel", "schedule", "role", "channel", "duration", "status"),
    "utility": ("serverinfo", "channelinfo", "roleinfo", "botinfo", "permissions", "invite", "poll", "embed", "say", "remind", "afk", "translate", "timestamp", "snowflake", "calculate", "help"),
    "welcome": ("setchannel", "disable", "test", "message", "embed", "variables", "autorole", "removeautorole", "goodbye", "goodbyechannel", "boost", "boostmessage", "dm", "rules", "status", "reset"),
    "leveling": ("rank", "leaderboard", "profile", "setxp", "addxp", "removexp", "setlevel", "reward", "removereward", "rewards", "enable", "disable", "channel", "message", "reset", "status"),
    "music": ("play", "pause", "resume", "skip", "stop", "queue", "nowplaying", "volume", "loop", "shuffle", "seek", "remove", "clear", "jump", "filter", "disconnect"),
    "config": ("show", "reset", "language", "prefix", "permissions", "modules", "dashboard", "database", "cache", "maintenance", "timezone", "branding", "colour", "export", "import", "status"),
    "reports": ("create", "close", "assign", "unassign", "list", "view", "delete", "export", "staff", "anonymous", "status", "priority", "note", "evidence", "notify", "lock"),
    "staff": ("leave", "leavelog", "promote", "demote", "stafflist", "oncall", "offcall", "note", "announce", "poll", "meeting", "roster", "remind", "handoff", "status", "audit"),
}

# Discord command names cannot contain spaces; normalize the two descriptive names.
ACTION_GROUPS["automod"] = tuple(x.replace(" ", "") for x in ACTION_GROUPS["automod"])


def build_action(action: str) -> Callable:
    async def callback(interaction: discord.Interaction, member: discord.Member | None = None, reason: str = "No reason provided"):
        if action in {"enable", "disable", "status", "setchannel", "reset", "show", "language", "modules", "dashboard"}:
            if not await admin(interaction):
                return
        await act(interaction, action, member, reason)
    callback.__name__ = f"action_{action}"
    callback.__annotations__ = {"interaction": discord.Interaction, "member": discord.Member | None, "reason": str, "return": None}
    return callback


def install(bot) -> None:
    root = app_commands.Group(name="manage", description="Titanium moderation and server management")
    bot.tree.add_command(root)
    for group_name, actions in ACTION_GROUPS.items():
        group = app_commands.Group(name=group_name, description=f"{group_name.title()} actions")
        root.add_command(group)
        for action in actions:
            command = app_commands.Command(name=action[:32], description=f"{action.title()} {group_name}", callback=build_action(action))
            command.add_check(lambda interaction: interaction.guild is not None)
            group.add_command(command)

    @bot.tree.command(name="ticket", description="Create a support ticket")
    async def ticket(interaction: discord.Interaction):
        if interaction.guild is None:
            return await reply(interaction, "This command is server-only.")
        channel = await interaction.guild.create_text_channel(f"ticket-{interaction.user.name}", overwrites={interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False), interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True)})
        await channel.send(f"{interaction.user.mention}, support will be with you shortly.")
        await reply(interaction, f"Ticket created: {channel.mention}")

    @bot.tree.command(name="rank", description="Show a member's rank")
    async def rank(interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        profile = await asyncio.to_thread(bot.db.profiles.find_one, {"guild_id": interaction.guild_id, "user_id": target.id}) or {}
        await interaction.response.send_message(embed=card("Rank", f"{target.mention}\nLevel: **{profile.get('level', 0)}**\nXP: **{profile.get('xp', 0)}**"), ephemeral=True)

    @bot.tree.command(name="serverinfo", description="Show server information")
    async def serverinfo(interaction: discord.Interaction):
        guild = interaction.guild
        await interaction.response.send_message(embed=card(guild.name, f"Members: `{guild.member_count}`\nChannels: `{len(guild.channels)}`\nRoles: `{len(guild.roles)}`"), ephemeral=True)

    @bot.tree.command(name="userinfo", description="Show user information")
    async def userinfo(interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        await interaction.response.send_message(embed=card(target.display_name, f"ID: `{target.id}`\nJoined: <t:{int(target.joined_at.timestamp())}:F>" if target.joined_at else f"ID: `{target.id}`), ephemeral=True)
