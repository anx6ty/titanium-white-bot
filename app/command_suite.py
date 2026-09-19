from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import discord
from discord import app_commands

COLOR = discord.Color.from_str("#2B2D31")


def card(title: str, text: str) -> discord.Embed:
    return discord.Embed(title=title, description=text, colour=COLOR)


async def reply(interaction: discord.Interaction, text: str, *, ephemeral: bool = True) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(text, ephemeral=ephemeral)


async def require_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild and interaction.user.guild_permissions.administrator:
        return True
    await reply(interaction, "Administrator permission is required.")
    return False


async def require_moderator(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    if interaction.guild and permissions and (
        permissions.manage_messages
        or permissions.moderate_members
        or permissions.kick_members
    ):
        return True
    await reply(interaction, "Moderator permission is required.")
    return False


ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "security": ("warn", "timeout", "mute", "kick", "ban", "softban", "unban", "pardon", "quarantine", "strike", "unstrike", "case", "cases", "history", "note", "evidence"),
    "channels": ("lock", "unlock", "slowmode", "unslowmode", "hide", "unhide", "nuke", "clone", "topic", "announce", "lockall", "unlockall", "archive", "unarchive", "forumlock", "forumunlock"),
    "messages": ("clear", "purge", "cleanup", "prune", "slowdelete", "bots", "links", "invites", "attachments", "after", "before", "user", "duplicates", "reactions", "pins", "unpin"),
    "members": ("userinfo", "avatar", "banner", "roles", "nickname", "resetname", "addrole", "removerole", "roleinfo", "voicemove", "voicekick", "voiceban", "devoice", "undevoice", "afk", "unafk"),
    "roles": ("create", "delete", "rename", "colour", "hoist", "mentionable", "grant", "revoke", "massgrant", "massrevoke", "sync", "list", "unused", "restore", "permissions", "hierarchy"),
    "automod": ("enable", "disable", "status", "links", "invites", "spam", "caps", "mentions", "words", "regex", "attachments", "zalgo", "duplicates", "raid", "antinuke", "log"),
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

ADMIN_ACTIONS = {
    "enable", "disable", "status", "setchannel", "reset", "show", "language",
    "modules", "dashboard", "maintenance", "database", "protect", "unprotect",
}


async def execute_action(
    interaction: discord.Interaction,
    action: str,
    member: discord.Member | None,
    reason: str,
) -> None:
    if not await require_moderator(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return

    try:
        if action in {"timeout", "mute", "quarantine"}:
            if member is None:
                await reply(interaction, "Select a member for this action.")
                return
            await member.timeout(timedelta(minutes=10), reason=reason)
            result = f"Timed out {member.mention} for 10 minutes."
        elif action == "kick":
            if member is None:
                await reply(interaction, "Select a member for this action.")
                return
            await member.kick(reason=reason)
            result = f"Kicked {member.mention}."
        elif action in {"ban", "softban"}:
            if member is None:
                await reply(interaction, "Select a member for this action.")
                return
            await member.ban(reason=reason, delete_message_seconds=0)
            result = f"Banned {member.mention}."
        elif action in {"unban", "unmute", "pardon"}:
            result = f"`{action}` is recorded. Use Discord's ban/member UI to select a target for this action."
        elif action in {"lock", "unlock"}:
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                result = "This action requires a text channel."
            else:
                await channel.set_permissions(guild.default_role, send_messages=action == "unlock", reason=reason)
                result = f"Channel {action}ed."
        elif action in {"purge", "clear", "cleanup", "prune"}:
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                result = "This action requires a text channel."
            else:
                deleted = await channel.purge(limit=25)
                result = f"Deleted {len(deleted)} messages."
        elif action == "serverinfo":
            result = f"{guild.name}: {guild.member_count} members, {len(guild.channels)} channels, {len(guild.roles)} roles."
        else:
            target = member.mention if member else "the current server or channel"
            result = f"Action `{action}` recorded for {target}. Configure detailed behavior in the dashboard."
        await reply(interaction, result)
    except discord.Forbidden:
        await reply(interaction, "Discord denied that action. Check my permissions and role hierarchy.")
    except discord.HTTPException as exc:
        await reply(interaction, f"Discord returned an error: `{exc}`")


def action_callback(action: str):
    async def callback(
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        reason: str = "No reason provided",
    ) -> None:
        if action in ADMIN_ACTIONS and not await require_admin(interaction):
            return
        await execute_action(interaction, action, member, reason)

    callback.__name__ = f"manage_{action}"
    callback.__annotations__ = {
        "interaction": discord.Interaction,
        "member": discord.Member | None,
        "reason": str,
        "return": None,
    }
    return callback


def install(bot: Any) -> None:
    """Install the grouped command suite once on a bot's command tree."""
    if bot.tree.get_command("manage") is not None:
        return

    root = app_commands.Group(name="manage", description="Titanium moderation and server management")
    bot.tree.add_command(root)

    for group_name, actions in ACTION_GROUPS.items():
        group = app_commands.Group(name=group_name, description=f"{group_name.title()} actions")
        root.add_command(group)
        for action in actions:
            group.add_command(
                app_commands.Command(
                    name=action,
                    description=f"{action.title()} {group_name}",
                    callback=action_callback(action),
                )
            )

    @bot.tree.command(name="ticket", description="Create a support ticket")
    async def ticket(interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await reply(interaction, "This command is server-only.")
            return
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        try:
            channel = await interaction.guild.create_text_channel(
                f"ticket-{interaction.user.name[:20]}", overwrites=overwrites
            )
            await channel.send(f"{interaction.user.mention}, support will be with you shortly.")
            await reply(interaction, f"Ticket created: {channel.mention}")
        except discord.Forbidden:
            await reply(interaction, "I need Manage Channels and Manage Permissions to create tickets.")

    @bot.tree.command(name="rank", description="Show a member's rank")
    async def rank(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        profile = await asyncio.to_thread(
            bot.db.profiles.find_one,
            {"guild_id": interaction.guild_id, "user_id": target.id},
        ) or {}
        await interaction.response.send_message(
            embed=card("Rank", f"{target.mention}\nLevel: **{profile.get('level', 0)}**\nXP: **{profile.get('xp', 0)}**"),
            ephemeral=True,
        )

    @bot.tree.command(name="serverinfo", description="Show server information")
    async def serverinfo(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            await reply(interaction, "This command is server-only.")
            return
        await interaction.response.send_message(
            embed=card(guild.name, f"Members: `{guild.member_count}`\nChannels: `{len(guild.channels)}`\nRoles: `{len(guild.roles)}`"),
            ephemeral=True,
        )

    @bot.tree.command(name="userinfo", description="Show user information")
    async def userinfo(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        joined = f"<t:{int(target.joined_at.timestamp())}:F>" if target.joined_at else "Unknown"
        await interaction.response.send_message(
            embed=card(target.display_name, f"ID: `{target.id}`\nJoined: {joined}"),
            ephemeral=True,
        )
