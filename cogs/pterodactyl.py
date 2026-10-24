import discord
from discord.ext import commands
from typing import Optional

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, PermissionLevel, get_logger,
)
from database import async_session_maker, MinecraftServer, ServerStatus
from config import settings
from sqlalchemy import select
from utils.ptero_api import get_pterodactyl_api, get_client_api, PterodactylAPIError

logger = get_logger("pterodactyl")


class PterodactylCog(commands.Cog):
    """Pterodactyl panel management commands."""

    def __init__(self, bot):
        self.bot = bot

    def _check_config(self) -> bool:
        return bool(settings.PTERODACTYL_URL and
                    (settings.PTERODACTYL_API_KEY or settings.PTERODACTYL_APP_API_KEY))

    @commands.hybrid_command(name="ptero-server", description="Pterodactyl server info / resources")
    @commands.guild_only()
    async def ptero_server(self, ctx: commands.Context, server_id: int):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        if not self._check_config():
            await ctx.send(embed=error_embed("Not Configured", "Pterodactyl not configured in .env"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        api = get_pterodactyl_api()
        try:
            server = await api.get_server(server_id)
        except PterodactylAPIError as exc:
            await ctx.send(embed=error_embed("Pterodactyl Error", str(exc)), ephemeral=True)
            return

        limits = server.get("limits", {})
        embed = info_embed(f"🖥️ {server.get('name', 'Server')}", f"UUID: `{server.get('uuid', '—')}`")
        embed.add_field(name="ID", value=str(server.get("id", "—")), inline=True)
        embed.add_field(name="Node", value=str(server.get("node", "—")), inline=True)
        embed.add_field(name="User", value=str(server.get("user", "—")), inline=True)
        embed.add_field(name="Memory", value=f"{limits.get('memory', 0)} MB", inline=True)
        embed.add_field(name="CPU", value=f"{limits.get('cpu', 0)}%", inline=True)
        embed.add_field(name="Disk", value=f"{limits.get('disk', 0)} MB", inline=True)
        embed.add_field(name="Docker", value=server.get("docker_image", "—"), inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="ptero-power", description="Power action via client API (identifier)")
    @commands.guild_only()
    async def ptero_power(self, ctx: commands.Context, identifier: str, action: str = "restart"):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        action = action.lower()
        if action not in ("start", "stop", "restart", "kill"):
            await ctx.send(embed=error_embed("Invalid Action", "Use: start / stop / restart / kill"), ephemeral=True)
            return

        client = get_client_api()
        if not client.configured:
            await ctx.send(embed=error_embed("Not Configured", "Client API key missing"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            await client.power(identifier, action)
            await ctx.send(embed=success_embed("Power Action Sent", f"`{action}` → `{identifier}`"), ephemeral=True)
        except PterodactylAPIError as exc:
            await ctx.send(embed=error_embed("Failed", str(exc)), ephemeral=True)

    @commands.hybrid_command(name="ptero-suspend", description="Suspend/unsuspend a server (app API)")
    @commands.guild_only()
    async def ptero_suspend(self, ctx: commands.Context, server_id: int, state: str = "suspend"):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        api = get_pterodactyl_api()
        if not api.configured:
            await ctx.send(embed=error_embed("Not Configured", "Pterodactyl not configured"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            if state == "suspend":
                await api.suspend_server(server_id)
                await ctx.send(embed=success_embed("Suspended", f"Server {server_id} suspended."), ephemeral=True)
            elif state == "unsuspend":
                await api.unsuspend_server(server_id)
                await ctx.send(embed=success_embed("Unsuspended", f"Server {server_id} unsuspended."), ephemeral=True)
            elif state == "delete":
                await api.delete_server(server_id)
                await ctx.send(embed=success_embed("Deleted", f"Server {server_id} deleted."), ephemeral=True)
            else:
                await ctx.send(embed=error_embed("Invalid State", "Use: suspend / unsuspend / delete"), ephemeral=True)
        except PterodactylAPIError as exc:
            await ctx.send(embed=error_embed("Pterodactyl Error", str(exc)), ephemeral=True)

    @commands.hybrid_command(name="ptero-nodes", description="List Pterodactyl nodes")
    @commands.guild_only()
    async def ptero_nodes(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        if not self._check_config():
            await ctx.send(embed=error_embed("Not Configured", "Pterodactyl not configured"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        api = get_pterodactyl_api()
        try:
            nodes = await api.list_nodes()
        except PterodactylAPIError as exc:
            await ctx.send(embed=error_embed("Error", str(exc)), ephemeral=True)
            return

        embed = info_embed("🌐 Pterodactyl Nodes", f"Total: {len(nodes)}")
        for node in nodes[:15]:
            attrs = node.get("attributes", {})
            status = "🟡 Maintenance" if attrs.get("maintenance_mode") else "🟢 Active"
            embed.add_field(
                name=f"{status} {attrs.get('name', '?')}",
                value=(f"FQDN: `{attrs.get('fqdn', '—')}`\n"
                       f"ID: {attrs.get('id')} • Memory: {attrs.get('memory', 0)}MB • Disk: {attrs.get('disk', 0)}MB"),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="ptero-eggs", description="List available nests/eggs")
    @commands.guild_only()
    async def ptero_eggs(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        if not self._check_config():
            await ctx.send(embed=error_embed("Not Configured", "Pterodactyl not configured"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        api = get_pterodactyl_api()
        try:
            eggs = await api.list_eggs()
        except PterodactylAPIError as exc:
            await ctx.send(embed=error_embed("Error", str(exc)), ephemeral=True)
            return

        embed = info_embed("🥚 Pterodactyl Eggs", f"Total: {len(eggs)}")
        for egg in eggs[:15]:
            attrs = egg.get("attributes", {})
            embed.add_field(
                name=f"{attrs.get('name', '?')} (ID {attrs.get('id')})",
                value=f"nest: {attrs.get('nest')} • author: {attrs.get('author', '—')}",
                inline=True,
            )
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PterodactylCog(bot))
