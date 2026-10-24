import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import asyncio

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, is_admin, PermissionLevel,
    get_logger
)
from database import async_session_maker, GuardianLog, AuditLog, TicketMessage
from config import settings
from sqlalchemy import select, func, desc

logger = get_logger("logs")


def _read_log_file(path: str) -> list:
    """Blocking helper — always call via asyncio.to_thread."""
    with open(path, "r", errors="replace") as f:
        return f.readlines()

class LogsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.hybrid_command(name="logs", description="View bot logs (Admin)")
    @commands.guild_only()
    async def logs(self, ctx: commands.Context, log_type: str = "bot", lines: int = 50):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        
        log_files = {
            "bot": "logs/bot.log",
            "guardian": "logs/guardian.log",
            "tickets": "logs/tickets.log",
            "nodes": "logs/node.log",
            "ptero": "logs/ptero.log",
            "errors": "logs/errors.log",
        }
        
        if log_type not in log_files:
            await ctx.send(embed=error_embed("Invalid Type", f"Types: {', '.join(log_files.keys())}"), ephemeral=True)
            return
        
        import os
        log_path = log_files[log_type]
        if not os.path.exists(log_path):
            await ctx.send(embed=error_embed("Not Found", f"Log file {log_path} not found"), ephemeral=True)
            return
        
        # read in a thread — a synchronous read of a 10MB log would block the
        # whole event loop (every shard and task) for the duration
        try:
            all_lines = await asyncio.to_thread(_read_log_file, log_path)
        except OSError as exc:
            await ctx.send(embed=error_embed("Read Error", f"Could not read {log_path}: {exc}"), ephemeral=True)
            return
        
        recent = all_lines[-lines:] if len(all_lines) > lines else all_lines
        content = "".join(recent)
        
        if len(content) > 1900:
            content = content[-1900:]
            content = "...\n" + content
        
        embed = info_embed(f"Logs: {log_type}", f"Last {len(recent)} lines")
        embed.description = f"```\n{content}\n```"
        await ctx.send(embed=embed, ephemeral=True)
    
    @commands.hybrid_command(name="audit-logs", description="View audit logs (Admin)")
    @commands.guild_only()
    async def audit_logs(self, ctx: commands.Context, limit: int = 20):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        
        async with async_session_maker() as session:
            logs = await session.execute(
                select(AuditLog)
                .where(AuditLog.guild_id == ctx.guild.id)
                .order_by(desc(AuditLog.created_at))
                .limit(limit)
            )
            logs = logs.scalars().all()
        
        if not logs:
            await ctx.send(embed=info_embed("No Logs", "No audit logs found"), ephemeral=True)
            return
        
        embed = info_embed("Audit Logs", f"Last {len(logs)} entries")
        for log in logs:
            embed.add_field(
                name=f"{log.action} - {log.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
                value=f"User: <@{log.user_id}> | Target: {log.target_type} {log.target_id or 'N/A'}",
                inline=False
            )
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(LogsCog(bot))