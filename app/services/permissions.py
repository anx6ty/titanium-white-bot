import discord
from discord import app_commands
from discord.ext import commands


async def ensure_guild_only(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return False
    return True


def is_admin_or_dev(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    return False


async def require_admin(interaction: discord.Interaction):
    if not is_admin_or_dev(interaction):
        await interaction.response.send_message("You need Administrator permission to use this command.", ephemeral=True)
        return False
    return True


async def require_moderator(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return False
    if interaction.user.guild_permissions.moderate_members or interaction.user.guild_permissions.kick_members:
        return True
    await interaction.response.send_message("You need moderator permissions to use this command.", ephemeral=True)
    return False


async def safe_send(interaction: discord.Interaction, content: str = "", *, embed: discord.Embed | None = None, ephemeral: bool = True):
    if interaction.response.is_done():
        await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
