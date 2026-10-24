import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from utils import (
    error_embed, success_embed, info_embed,
    has_permission, is_admin, PermissionLevel,
    get_logger, generate_uuid, generate_password, format_bytes
)
from database import async_session_maker, User
from config import settings
from sqlalchemy import select

logger = get_logger("utils")

class UtilsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.hybrid_command(name="ping", description="Check bot latency")
    async def ping(self, ctx: commands.Context):
        latency = round(self.bot.latency * 1000, 2)
        embed = info_embed("Pong!", f"Latency: {latency}ms")
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="uuid", description="Generate a UUID")
    async def uuid_cmd(self, ctx: commands.Context):
        await ctx.send(embed=success_embed("UUID", f"`{generate_uuid()}`"))
    
    @commands.hybrid_command(name="password", description="Generate a secure password")
    @commands.guild_only()
    async def password(self, ctx: commands.Context, length: int = 16):
        if length < 8 or length > 64:
            await ctx.send(embed=error_embed("Invalid Length", "Length must be 8-64"), ephemeral=True)
            return
        pwd = generate_password(length)
        await ctx.send(embed=success_embed("Password", f"||{pwd}||"), ephemeral=True)
    
    @commands.hybrid_command(name="apikey", description="Generate an API key")
    @commands.guild_only()
    async def apikey(self, ctx: commands.Context):
        key = f"arqon_{__import__('secrets').token_urlsafe(32)}"
        await ctx.send(embed=success_embed("API Key", f"`{key}`"), ephemeral=True)
    
    @commands.hybrid_command(name="serverinfo", description="Show server information")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context):
        guild = ctx.guild
        embed = info_embed(f"Server Info: {guild.name}", f"ID: {guild.id}")
        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        embed.add_field(name="Members", value=guild.member_count, inline=True)
        embed.add_field(name="Channels", value=len(guild.channels), inline=True)
        embed.add_field(name="Roles", value=len(guild.roles), inline=True)
        embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
        embed.add_field(name="Created", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="whois", description="Show Discord user information")
    @commands.guild_only()
    async def whois(self, ctx: commands.Context, user: discord.User = None):
        user = user or ctx.author
        member = ctx.guild.get_member(user.id)
        
        embed = info_embed(f"User Info: {user}", f"ID: {user.id}")
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Name", value=user.name, inline=True)
        embed.add_field(name="Bot", value="Yes" if user.bot else "No", inline=True)
        embed.add_field(name="Created", value=user.created_at.strftime("%Y-%m-%d"), inline=True)
        
        if member:
            embed.add_field(name="Joined", value=member.joined_at.strftime("%Y-%m-%d"), inline=True)
            embed.add_field(name="Roles", value=len(member.roles) - 1, inline=True)
            embed.add_field(name="Top Role", value=member.top_role.mention, inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="avatar", description="Get user avatar")
    @commands.guild_only()
    async def avatar(self, ctx: commands.Context, user: discord.User = None):
        user = user or ctx.author
        embed = info_embed(f"Avatar: {user}")
        embed.set_image(url=user.display_avatar.url)
        await ctx.send(embed=embed)
    
    @commands.hybrid_command(name="calculate", description="Calculate resources needed")
    @commands.guild_only()
    async def calculate(self, ctx: commands.Context, ram: int, cpu: int, disk: int, count: int = 1):
        if not has_permission(ctx.author, PermissionLevel.VPS_USER):
            await ctx.send(embed=error_embed("Permission Denied", "VPS User role required"), ephemeral=True)
            return
        
        total_ram = ram * count
        total_cpu = cpu * count
        total_disk = disk * count
        
        embed = info_embed("Resource Calculation", f"For {count} VPS instance(s)")
        embed.add_field(name="Per Instance", value=f"RAM: {ram}MB | CPU: {cpu} | Disk: {disk}GB", inline=False)
        embed.add_field(name="Total", value=f"RAM: {total_ram}MB | CPU: {total_cpu} | Disk: {total_disk}GB", inline=False)
        embed.add_field(name="Recommended Node", value=f"Min {total_ram}MB RAM, {total_cpu} cores, {total_disk}GB disk", inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(UtilsCog(bot))