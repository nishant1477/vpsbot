import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, is_admin, PermissionLevel,
    get_logger
)
from database import (
    async_session_maker, User, VPSInstance, VPSServer, VPSStatus,
    Node, NodeStatus, Ticket, TicketStatus,
)
from config import settings
from sqlalchemy import select, func

logger = get_logger("dashboard")

class DashboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.hybrid_command(name="dashboard", description="Get dashboard link")
    @commands.guild_only()
    async def dashboard(self, ctx: commands.Context):
        if not settings.DASHBOARD_ENABLED:
            await ctx.send(embed=info_embed("Dashboard Disabled", "Web dashboard is not enabled"), ephemeral=True)
            return

        embed = info_embed("🖥️ Arqon Dashboard", "Web dashboard for managing your services")
        embed.add_field(name="URL", value=f"http://{settings.DASHBOARD_HOST}:{settings.DASHBOARD_PORT}", inline=False)
        embed.add_field(name="Features", value="• VPS Management\n• Node Monitoring\n• AI Assistant\n• Tickets\n• Billing\n• Live Loading UX", inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="sysstats", description="View system statistics (Admin)")
    @commands.guild_only()
    async def sysstats(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            total_users = await session.execute(select(func.count(User.id)))
            total_vps = await session.execute(select(func.count(VPSInstance.id)).where(VPSInstance.deleted_at.is_(None)))
            running_vps = await session.execute(
                select(func.count(VPSInstance.id)).where(
                    VPSInstance.status == VPSStatus.RUNNING, VPSInstance.deleted_at.is_(None)))
            total_nodes = await session.execute(select(func.count(Node.id)))
            online_nodes = await session.execute(
                select(func.count(Node.id)).where(Node.status == NodeStatus.ONLINE))
            total_tickets = await session.execute(select(func.count(Ticket.id)))
            open_tickets = await session.execute(
                select(func.count(Ticket.id)).where(Ticket.status == TicketStatus.OPEN))
        
        embed = info_embed("System Statistics", "Overall system overview")
        embed.add_field(name="Users", value=str(total_users.scalar()), inline=True)
        embed.add_field(name="Total VPS", value=str(total_vps.scalar()), inline=True)
        embed.add_field(name="Running VPS", value=str(running_vps.scalar()), inline=True)
        embed.add_field(name="Nodes", value=str(total_nodes.scalar()), inline=True)
        embed.add_field(name="Online Nodes", value=str(online_nodes.scalar()), inline=True)
        embed.add_field(name="Tickets", value=str(total_tickets.scalar()), inline=True)
        embed.add_field(name="Open Tickets", value=str(open_tickets.scalar()), inline=True)
        
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(DashboardCog(bot))