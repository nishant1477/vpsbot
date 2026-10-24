import discord
from discord.ext import commands
from typing import Optional, List

from utils import (
    error_embed, success_embed, info_embed, warning_embed,
    is_admin, is_owner, has_permission, PermissionLevel,
    get_logger, log_audit,
    set_bot_admin_cache, all_bot_admins, refresh_bot_admin_row,
)
from database import async_session_maker, GuildConfig, BotAdmin, get_or_create_guild_config
from config import settings
from sqlalchemy import select

logger = get_logger("admin")

# remember pre-lockdown @everyone permissions per guild
_prev_permissions = {}


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── multi-user bot admin system ──────────────────────────────────
    # Grant ANY user bot-admin without touching Discord roles — works in
    # every server the bot is in and survives restarts (DB-backed).

    @commands.hybrid_command(name="addadmin", description="Give a user bot admin (Owner)")
    async def addadmin(self, ctx: commands.Context, user: discord.User,
                       level: str = "admin", *, reason: str = None):
        """!addadmin @user [admin|staff] — Owner only."""
        if not is_owner(ctx.author):
            await ctx.send(embed=error_embed(
                "Permission Denied",
                "Only the bot owner can grant admin.\nSet your ID in `.env` → `OWNER_IDS`."),
                ephemeral=True)
            return

        level = (level or "admin").lower()
        if level not in ("admin", "staff"):
            await ctx.send(embed=error_embed(
                "Invalid Level", "Level must be `admin` or `staff`."), ephemeral=True)
            return

        if user.bot:
            await ctx.send(embed=error_embed("Not Possible", "Bots cannot be admins."), ephemeral=True)
            return

        if user.id in settings.effective_owner_ids:
            await ctx.send(embed=info_embed(
                "Already Owner", f"{user.mention} is already a bot owner — owners outrank admins."),
                ephemeral=True)
            return

        async with async_session_maker() as session:
            existing = (await session.execute(
                select(BotAdmin).where(BotAdmin.discord_id == user.id))).scalar_one_or_none()
            if existing:
                existing.level = level
                existing.added_by = ctx.author.id
                existing.reason = reason
            else:
                session.add(BotAdmin(
                    discord_id=user.id,
                    level=level,
                    added_by=ctx.author.id,
                    reason=reason,
                ))
            await session.commit()

        refresh_bot_admin_row(user.id, level)
        logger.info(f"{ctx.author} granted {level} to {user} ({user.id})")
        # tamper-evident audit trail for privilege grants (#20)
        try:
            from utils.security_core import chain_append
            await chain_append("admin_granted", {
                "target": user.id, "level": level,
                "by": ctx.author.id, "reason": reason or "",
            })
        except Exception:
            pass

        emoji = "👑" if level == "admin" else "🧰"
        await ctx.send(embed=success_embed(
            f"{emoji} Admin Granted",
            f"{user.mention} is now a bot **{level}**!\n"
            f"Granted by {ctx.author.mention}"
            + (f" • Reason: *{reason}*" if reason else "")))
        try:
            await user.send(embed=success_embed(
                f"{emoji} You are now a bot {level}!",
                f"You were granted **{level}** access on **{self.bot.user.name}** by "
                f"{ctx.author}. Enjoy the powers — use them wisely."))
        except Exception:
            pass

    @commands.hybrid_command(name="removeadmin", description="Revoke a user's bot admin (Owner)")
    async def removeadmin(self, ctx: commands.Context, user: discord.User):
        if not is_owner(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Only the bot owner can revoke admin."),
                           ephemeral=True)
            return

        async with async_session_maker() as session:
            existing = (await session.execute(
                select(BotAdmin).where(BotAdmin.discord_id == user.id))).scalar_one_or_none()
            if not existing:
                await ctx.send(embed=error_embed(
                    "Not An Admin", f"{user.mention} does not hold bot admin."), ephemeral=True)
                return
            await session.delete(existing)
            await session.commit()

        refresh_bot_admin_row(user.id, None)
        logger.info(f"{ctx.author} revoked admin from {user} ({user.id})")
        try:
            from utils.security_core import chain_append
            await chain_append("admin_revoked", {
                "target": user.id, "by": ctx.author.id,
            })
        except Exception:
            pass
        await ctx.send(embed=success_embed(
            "Admin Revoked", f"{user.mention} is no longer a bot admin."))

    @commands.hybrid_command(name="admins", description="List every bot admin & staff member")
    async def admins(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            rows = (await session.execute(
                select(BotAdmin).order_by(BotAdmin.added_at))).scalars().all()

        owners = settings.effective_owner_ids
        lines = [f"{chr(0x1F451)} <@{uid}> — **owner**" for uid in owners]
        for row in rows:
            icon = "👑" if row.level == "admin" else "🧰"
            added = f" • by <@{row.added_by}>" if row.added_by else ""
            lines.append(f"{icon} <@{row.discord_id}> — **{row.level}**{added}")

        embed = info_embed(
            f"👥 Bot Admin Team ({len(lines)})",
            "\n".join(lines) if lines else "No admins yet — use `!addadmin @user` to grant some.")
        embed.set_footer(text="Owners can add/remove admins • /addadmin • /removeadmin")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="setup", description="Initial server setup checklist")
    @commands.guild_only()
    async def setup(self, ctx: commands.Context):
        if not is_owner(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Bot owner only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            await get_or_create_guild_config(session, ctx.guild.id)
            await session.commit()

        embed = info_embed("🧭 Arqon Bot Setup", f"Configure **{ctx.guild.name}** step by step:")
        embed.add_field(name="1. Admins", value="`!addadmin @user` — grant bot admin (no roles needed)", inline=False)
        embed.add_field(name="2. Roles", value="`!set-role admin @role` (also staff/customer/vps/premium)", inline=False)
        embed.add_field(name="3. Tickets", value="`!setup-tickets <category> <transcript-channel>` then `!ticket`", inline=False)
        embed.add_field(name="4. Guardian", value="`!guardian` — toggle anti-raid/spam/nuke", inline=False)
        embed.add_field(name="5. Nodes", value="`!node-add` — register VPS host agents", inline=False)
        embed.add_field(name="6. Monitoring", value="`!monitor <channel>` — node alerts", inline=False)
        embed.set_footer(text="Run !config at any time to review settings")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="config", description="View server configuration")
    @commands.guild_only()
    async def config(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            config = await get_or_create_guild_config(session, ctx.guild.id)
            await session.commit()

        def chan(cid):
            return f"<#{cid}>" if cid else "Not set"

        embed = info_embed("⚙️ Server Configuration", f"Settings for **{ctx.guild.name}**")
        embed.add_field(name="Prefix", value=config.prefix or "!", inline=True)
        embed.add_field(name="Language", value=config.language or "en", inline=True)
        embed.add_field(name="Timezone", value=config.timezone or "UTC", inline=True)
        embed.add_field(name="Ticket Category", value=chan(config.ticket_category_id), inline=True)
        embed.add_field(name="Ticket Logs", value=chan(config.ticket_log_channel_id), inline=True)
        embed.add_field(name="Transcripts", value=chan(config.ticket_transcript_channel_id), inline=True)
        embed.add_field(name="VPS Logs", value=chan(config.vps_log_channel_id), inline=True)
        embed.add_field(name="Guardian Logs", value=chan(config.guardian_log_channel_id), inline=True)
        embed.add_field(name="Guardian", value="✅" if config.guardian_enabled else "❌", inline=True)
        embed.add_field(name="Anti-Raid", value="✅" if config.anti_raid_enabled else "❌", inline=True)
        embed.add_field(name="Anti-Spam", value="✅" if config.anti_spam_enabled else "❌", inline=True)
        embed.add_field(name="Max Tickets/User", value=str(config.max_tickets_per_user), inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="set-role", description="Bind a Discord role to a bot permission level")
    @commands.guild_only()
    async def set_role(self, ctx: commands.Context, role_type: str, role: discord.Role):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        role_map = {
            "admin": "admin_role_ids",
            "staff": "staff_role_ids",
            "customer": "customer_role_ids",
            "vps": "vps_user_role_ids",
            "premium": "premium_user_role_ids",
        }

        key = role_map.get(role_type.lower())
        if not key:
            await ctx.send(embed=error_embed("Invalid Type", f"Types: {', '.join(role_map.keys())}"), ephemeral=True)
            return

        async with async_session_maker() as session:
            config = await get_or_create_guild_config(session, ctx.guild.id)
            current: List[int] = list(getattr(config, key) or [])
            if role.id not in current:
                current.append(role.id)
            setattr(config, key, current)
            await session.commit()

        await ctx.send(embed=success_embed(
            "Role Bound",
            f"{role_type.title()} role → {role.mention}\nApplied instantly (no restart needed).",
        ), ephemeral=True)

    @commands.hybrid_command(name="reload", description="Reload bot extensions (Owner)")
    @commands.guild_only()
    async def reload(self, ctx: commands.Context, extension: str = None):
        if not is_owner(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Owner only"), ephemeral=True)
            return

        if extension:
            try:
                await self.bot.reload_extension(f"cogs.{extension}")
                await ctx.send(embed=success_embed("Reloaded", f"Extension `{extension}` reloaded."), ephemeral=True)
            except Exception as exc:
                await ctx.send(embed=error_embed("Error", str(exc)), ephemeral=True)
        else:
            loaded, failed = 0, []
            for ext in self.bot.initial_extensions:
                try:
                    await self.bot.reload_extension(ext)
                    loaded += 1
                except Exception as exc:
                    failed.append(f"{ext}: {exc}")
            embed = success_embed("Reloaded", f"{loaded} extensions reloaded.")
            if failed:
                embed.add_field(name="Failures", value="\n".join(failed[:5])[:1000], inline=False)
            await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="sync", description="Sync slash commands (Owner)")
    @commands.guild_only()
    async def sync(self, ctx: commands.Context, guild_only: bool = False):
        if not is_owner(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Owner only"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        if guild_only:
            await self.bot.tree.sync(guild=ctx.guild)
            await ctx.followup.send(embed=success_embed("Synced", f"Commands synced to **{ctx.guild.name}**."), ephemeral=True)
        else:
            await self.bot.tree.sync()
            await ctx.followup.send(embed=success_embed("Synced", "Global commands synced."), ephemeral=True)

    @commands.hybrid_command(name="lockdown", description="Lock/unlock server channels (Admin)")
    @commands.guild_only()
    async def lockdown(self, ctx: commands.Context, enabled: bool):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        try:
            everyone = ctx.guild.default_role
            if enabled:
                _prev_permissions[ctx.guild.id] = everyone.permissions
                perms = discord.Permissions.none()
                perms.read_messages = False  # full lock: cannot even view channels
                await everyone.edit(permissions=perms, reason=f"Lockdown by {ctx.author}")
                await ctx.send(embed=success_embed(
                    "🔒 Server Locked",
                    "@everyone send/view permissions removed.\nUse `!lockdown False` to restore previous permissions."),
                    ephemeral=True)
            else:
                if ctx.guild.id in _prev_permissions:
                    await everyone.edit(permissions=_prev_permissions[ctx.guild.id],
                                        reason=f"Lockdown lifted by {ctx.author}")
                    del _prev_permissions[ctx.guild.id]
                    await ctx.send(embed=success_embed("🔓 Server Unlocked", "Previous @everyone permissions restored."), ephemeral=True)
                else:
                    perms = discord.Permissions.none()
                    perms.read_messages = True
                    perms.send_messages = True
                    perms.read_message_history = True
                    await everyone.edit(permissions=perms, reason=f"Lockdown lifted by {ctx.author}")
                    await ctx.send(embed=success_embed(
                        "🔓 Server Unlocked",
                        "Basic permissions restored (view/send/history).\n"
                        "Fine-tune remaining permissions manually — the previous snapshot was not found."),
                        ephemeral=True)
        except Exception as exc:
            await ctx.send(embed=error_embed("Error", str(exc)), ephemeral=True)

    @commands.hybrid_command(name="announce", description="Send an announcement (Admin)")
    @commands.guild_only()
    async def announce(self, ctx: commands.Context, channel: discord.TextChannel, title: str, *, message: str):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        embed = info_embed(f"📢 {title}", message)
        embed.set_footer(text=f"Announcement by {ctx.author.display_name}")
        await channel.send(embed=embed)
        await ctx.send(embed=success_embed("Sent", f"Announcement posted in {channel.mention}."), ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
