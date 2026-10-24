import discord
from discord.ext import commands
from typing import Optional

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, is_admin, PermissionLevel,
    get_logger, get_user_permission_level,
)
from database import (
    async_session_maker, User, VPSInstance, Ticket,
    PermissionLevel as DBPermissionLevel, get_or_create_user,
)
from config import settings
from sqlalchemy import select, func, desc

logger = get_logger("users")


class UsersCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="profile", description="View your profile")
    @commands.guild_only()
    async def profile(self, ctx: commands.Context, user: discord.User = None):
        target = user or ctx.author

        async with async_session_maker() as session:
            db_user = await get_or_create_user(session, target.id, target.name)
            vps_count = (await session.execute(
                select(func.count(VPSInstance.id)).where(
                    VPSInstance.owner_id == db_user.id,
                    VPSInstance.deleted_at.is_(None)))).scalar() or 0
            ticket_count = (await session.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.creator_discord_id == target.id))).scalar() or 0
            created_at = db_user.created_at

        embed = info_embed(f"👤 Profile: {target}", f"Discord ID: `{target.id}`")
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Username", value=target.name, inline=True)
        embed.add_field(name="Total VPS", value=str(vps_count), inline=True)
        embed.add_field(name="Total Tickets", value=str(ticket_count), inline=True)
        embed.add_field(name="Banned", value="Yes" if db_user.is_banned else "No", inline=True)
        if db_user.ban_reason:
            embed.add_field(name="Ban Reason", value=db_user.ban_reason, inline=False)
        embed.add_field(name="Member Since", value=created_at.strftime("%Y-%m-%d"), inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="userinfo", description="Detailed user info (Staff)")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, user: discord.User):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_user = await get_or_create_user(session, user.id, user.name)
            vps_count = (await session.execute(
                select(func.count(VPSInstance.id)).where(
                    VPSInstance.owner_id == db_user.id,
                    VPSInstance.deleted_at.is_(None)))).scalar() or 0

        member = ctx.guild.get_member(user.id)
        perm_level = get_user_permission_level(member) if member else PermissionLevel.USER

        embed = info_embed(f"🔎 User Info: {user}", f"Discord ID: `{user.id}`")
        embed.add_field(name="Permission Level", value=perm_level.name, inline=True)
        embed.add_field(name="Total VPS", value=str(vps_count), inline=True)
        embed.add_field(name="DB Level", value=str(db_user.permission_level), inline=True)
        embed.add_field(name="Banned", value="Yes" if db_user.is_banned else "No", inline=True)
        if db_user.ban_reason:
            embed.add_field(name="Ban Reason", value=db_user.ban_reason, inline=False)
        embed.add_field(name="DB Created", value=db_user.created_at.strftime("%Y-%m-%d %H:%M"), inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="ban-user", description="Ban a user from the bot (Admin)")
    @commands.guild_only()
    async def ban_user(self, ctx: commands.Context, user: discord.User, reason: str = "No reason provided"):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        if user.id in settings.effective_owner_ids:
            await ctx.send(embed=error_embed("Denied", "You cannot ban an owner."), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_user = await get_or_create_user(session, user.id, user.name)
            db_user.is_banned = True
            db_user.ban_reason = reason
            await session.commit()

        await ctx.send(embed=success_embed("🔨 User Banned", f"{user.mention} banned.\nReason: {reason}"), ephemeral=True)

    @commands.hybrid_command(name="unban-user", description="Unban a user (Admin)")
    @commands.guild_only()
    async def unban_user(self, ctx: commands.Context, user: discord.User):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_user = await get_or_create_user(session, user.id, user.name)
            db_user.is_banned = False
            db_user.ban_reason = None
            await session.commit()

        await ctx.send(embed=success_embed("✅ User Unbanned", f"{user.mention} unbanned."), ephemeral=True)

    @commands.hybrid_command(name="add-premium", description="Grant premium (Admin)")
    @commands.guild_only()
    async def add_premium(self, ctx: commands.Context, user: discord.User):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_user = await get_or_create_user(session, user.id, user.name)
            db_user.permission_level = max(db_user.permission_level, DBPermissionLevel.PREMIUM.value)
            await session.commit()

        await ctx.send(embed=success_embed("⭐ Premium Added", f"{user.mention} is now premium."), ephemeral=True)

    @commands.hybrid_command(name="remove-premium", description="Remove premium (Admin)")
    @commands.guild_only()
    async def remove_premium(self, ctx: commands.Context, user: discord.User):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_user = await get_or_create_user(session, user.id, user.name)
            if db_user.permission_level == DBPermissionLevel.PREMIUM.value:
                db_user.permission_level = DBPermissionLevel.USER.value
            await session.commit()

        await ctx.send(embed=success_embed("Premium Removed", f"{user.mention} is no longer premium."), ephemeral=True)


async def setup(bot):
    await bot.add_cog(UsersCog(bot))
