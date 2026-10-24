import discord
from discord.ext import commands
from typing import Optional

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, is_admin, is_staff, PermissionLevel,
    get_logger,
)
from utils.node_client import NodeClient, NodeConnectionError
from database import async_session_maker, VPSInstance, User, Node
from sqlalchemy import select

logger = get_logger("firewall")


async def _resolve_vps(vps_id: str, discord_user_id: int) -> Optional[VPSInstance]:
    """Find a VPS by numeric id or uuid prefix, verifying ownership via User join."""
    async with async_session_maker() as session:
        query = select(VPSInstance).where(VPSInstance.deleted_at.is_(None))
        if vps_id.isdigit():
            query = query.where(VPSInstance.id == int(vps_id))
        else:
            query = query.where(VPSInstance.uuid.like(f"{vps_id}%"))

        query = query.join(User, VPSInstance.owner_id == User.id).where(User.discord_id == discord_user_id)
        return (await session.execute(query)).scalar_one_or_none()


class FirewallCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="fw-allow", description="Allow a port on your VPS firewall")
    @commands.guild_only()
    async def fw_allow(self, ctx: commands.Context, vps_id: str, port: int, protocol: str = "tcp",
                       ip: str = "0.0.0.0/0"):
        await self._add_rule(ctx, "allow", vps_id, port, protocol, ip)

    @commands.hybrid_command(name="fw-deny", description="Deny a port on your VPS firewall")
    @commands.guild_only()
    async def fw_deny(self, ctx: commands.Context, vps_id: str, port: int, protocol: str = "tcp",
                      ip: str = "0.0.0.0/0"):
        await self._add_rule(ctx, "deny", vps_id, port, protocol, ip)

    @commands.hybrid_command(name="fw-remove", description="Remove a firewall rule")
    @commands.guild_only()
    async def fw_remove(self, ctx: commands.Context, vps_id: str, port: int, protocol: str = "tcp"):
        if not has_permission(ctx.author, PermissionLevel.VPS_USER):
            await ctx.send(embed=error_embed("Permission Denied", "VPS User role required"), ephemeral=True)
            return
        if protocol not in ("tcp", "udp"):
            await ctx.send(embed=error_embed("Invalid Protocol", "tcp or udp"), ephemeral=True)
            return

        vps = await _resolve_vps(vps_id, ctx.author.id)
        if not vps:
            await ctx.send(embed=error_embed("Not Found", "VPS not found (or not yours)."), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            async with async_session_maker() as session:
                node = await session.get(Node, vps.node_id)
            client = NodeClient(node)
            # The rule may have been created as allow OR deny — the agent now
            # clears both targets for this port/protocol, so removing always works.
            await client.firewall_remove(vps.uuid, port, protocol, action="allow")
            await ctx.send(embed=success_embed("Rule Removed", f"`{port}/{protocol}` rule removed."), ephemeral=True)
        except NodeConnectionError as exc:
            await ctx.send(embed=error_embed("Node Error", str(exc)), ephemeral=True)

    @commands.hybrid_command(name="fw-list", description="List firewall rules for a VPS")
    @commands.guild_only()
    async def fw_list(self, ctx: commands.Context, vps_id: str):
        if not has_permission(ctx.author, PermissionLevel.VPS_USER):
            await ctx.send(embed=error_embed("Permission Denied", "VPS User role required"), ephemeral=True)
            return

        vps = await _resolve_vps(vps_id, ctx.author.id)
        if not vps:
            await ctx.send(embed=error_embed("Not Found", "VPS not found (or not yours)."), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            async with async_session_maker() as session:
                node = await session.get(Node, vps.node_id)
            client = NodeClient(node)
            data = await client.firewall_list(vps.uuid)
            rules = data.get("rules", [])

            embed = info_embed(f"🧱 Firewall Rules — {vps.name}", f"{len(rules)} active rule(s)")
            for rule in rules[:20]:
                action = "✅ ALLOW" if rule.get("action") == "allow" else "⛔ DENY"
                embed.add_field(
                    name=f"{action} {rule.get('port')}/{rule.get('protocol')}",
                    value=f"Source: `{rule.get('source', 'any')}`",
                    inline=True,
                )
            if not rules:
                embed.description = "No custom rules — default policy applies."
            await ctx.send(embed=embed, ephemeral=True)
        except NodeConnectionError as exc:
            await ctx.send(embed=error_embed("Node Error", str(exc)), ephemeral=True)

    async def _add_rule(self, ctx: commands.Context, action: str, vps_id: str,
                        port: int, protocol: str, ip: str):
        if not has_permission(ctx.author, PermissionLevel.VPS_USER):
            await ctx.send(embed=error_embed("Permission Denied", "VPS User role required"), ephemeral=True)
            return
        if protocol not in ("tcp", "udp"):
            await ctx.send(embed=error_embed("Invalid Protocol", "tcp or udp"), ephemeral=True)
            return
        if not (1 <= port <= 65535):
            await ctx.send(embed=error_embed("Invalid Port", "1-65535"), ephemeral=True)
            return

        vps = await _resolve_vps(vps_id, ctx.author.id)
        if not vps:
            await ctx.send(embed=error_embed("Not Found", "VPS not found (or not yours)."), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            async with async_session_maker() as session:
                node = await session.get(Node, vps.node_id)
            client = NodeClient(node)
            await client.firewall_add(vps.uuid, action, port, protocol, ip)
            emoji = "✅" if action == "allow" else "⛔"
            await ctx.send(embed=success_embed(
                "Rule Added",
                f"{emoji} **{action.upper()}** `{port}/{protocol}` from `{ip}` on **{vps.name}**.",
            ), ephemeral=True)
        except NodeConnectionError as exc:
            await ctx.send(embed=error_embed("Node Error", str(exc)), ephemeral=True)


async def setup(bot):
    await bot.add_cog(FirewallCog(bot))
