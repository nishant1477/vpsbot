import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, is_admin, PermissionLevel,
    get_logger, log_vps_action
)
from database import async_session_maker, PortAllocation, Node, VPSServer
from config import settings
from sqlalchemy import select, func

logger = get_logger("ports")

class PortsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.hybrid_command(name="port-add", description="Add port allocation (Admin)")
    @commands.guild_only()
    async def port_add(self, ctx: commands.Context, node_id: int, port: int, protocol: str = "tcp"):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        
        if not (settings.PORT_RANGE_START <= port <= settings.PORT_RANGE_END):
            await ctx.send(embed=error_embed("Invalid Port", f"Port must be between {settings.PORT_RANGE_START}-{settings.PORT_RANGE_END}"), ephemeral=True)
            return
        
        if protocol not in ["tcp", "udp"]:
            await ctx.send(embed=error_embed("Invalid Protocol", "Must be tcp or udp"), ephemeral=True)
            return
        
        async with async_session_maker() as session:
            existing = await session.execute(
                select(PortAllocation).where(PortAllocation.node_id == node_id, PortAllocation.port == port, PortAllocation.protocol == protocol)
            )
            if existing.scalar_one_or_none():
                await ctx.send(embed=error_embed("Exists", "Port already allocated on this node"), ephemeral=True)
                return
            
            alloc = PortAllocation(node_id=node_id, port=port, protocol=protocol, is_reserved=False)
            session.add(alloc)
            await session.commit()
        
        await ctx.send(embed=success_embed("Port Added", f"Port {port}/{protocol} added to node {node_id}"))
    
    @commands.hybrid_command(name="port-list", description="List port allocations")
    @commands.guild_only()
    async def port_list(self, ctx: commands.Context, node_id: int = None):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        
        async with async_session_maker() as session:
            query = select(PortAllocation)
            if node_id:
                query = query.where(PortAllocation.node_id == node_id)
            allocs = await session.execute(query.order_by(PortAllocation.port).limit(100))
            allocs = allocs.scalars().all()
        
        if not allocs:
            await ctx.send(embed=info_embed("No Allocations", "No port allocations found"), ephemeral=True)
            return
        
        embed = info_embed("Port Allocations", f"Total: {len(allocs)}")
        for a in allocs:
            node_name = "Unknown"
            async with async_session_maker() as session:
                node = await session.get(Node, a.node_id)
                node_name = node.name if node else "Unknown"
            
            vps_name = "Unassigned"
            if a.allocated_to_type == "vps" and a.allocated_to_id:
                async with async_session_maker() as session:
                    vps = await session.get(VPSServer, a.allocated_to_id)
                    vps_name = vps.name if vps else "Unknown"
            
            status = "Reserved" if a.is_reserved else "Free"
            embed.add_field(
                name=f"Port {a.port}/{a.protocol}",
                value=f"Node: {node_name} | {status} | VPS: {vps_name}",
                inline=True
            )
        await ctx.send(embed=embed, ephemeral=True)
    
    @commands.hybrid_command(name="port-free", description="Find free port on node")
    @commands.guild_only()
    async def port_free(self, ctx: commands.Context, node_id: int):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        
        async with async_session_maker() as session:
            used_ports = await session.execute(
                select(PortAllocation.port).where(PortAllocation.node_id == node_id)
            )
            used = set(used_ports.scalars().all())
        
        free_ports = [p for p in range(settings.PORT_RANGE_START, settings.PORT_RANGE_END + 1) if p not in used]
        
        embed = info_embed("Free Ports", f"Node {node_id} - {len(free_ports)} free ports")
        if free_ports:
            embed.add_field(name="First Available", value=str(free_ports[0]), inline=True)
            embed.add_field(name="Range", value=f"{free_ports[0]} - {free_ports[-1]}", inline=True)
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(PortsCog(bot))