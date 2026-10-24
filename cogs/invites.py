"""Arqon Bot — invite tracking.

Powers the invite-earned plan economy (/plans).

How it works
------------
* On startup / guild join the bot snapshots every guild's invite codes and uses.
* When a member joins, the snapshot is diffed: whichever invite's `uses` went
  up is the code they used → the inviter gets +1 (logged in `invite_logs`,
  counter in `invite_stats`).
* When a member leaves, their tracked join is marked and the inviter's count
  is decremented — fake "join & leave" farming doesn't pay.
* Admins can grant bonus invites (e.g. for YouTube referrals) with
  /grantinvites.
"""
import discord
from discord.ext import commands
from typing import Dict, Optional
from datetime import datetime

from utils import (
    success_embed, error_embed, info_embed, purple_embed,
    is_admin, get_logger,
)
from utils.emoji import E
from database import (
    async_session_maker, InviteStat, InviteLog, Plan,
)
from config import settings
from sqlalchemy import select, func, desc

logger = get_logger("invites")


class InvitesCog(commands.Cog):
    """Invite tracking + leaderboard + admin grants."""

    def __init__(self, bot):
        self.bot = bot
        # {guild_id: {code: uses}}
        self._snapshot: Dict[int, Dict[str, int]] = {}
        self._ready = False

    # ── snapshot helpers ─────────────────────────────────────────────

    async def _snapshot_guild(self, guild: discord.Guild) -> None:
        try:
            if guild.me is None or not guild.me.guild_permissions.manage_guild:
                self._snapshot[guild.id] = {}
                return
            invites = await guild.invites()
            self._snapshot[guild.id] = {i.code: i.uses or 0 for i in invites}
        except (discord.Forbidden, discord.HTTPException):
            self._snapshot[guild.id] = {}

    async def _resolve_inviter(self, guild: discord.Guild) -> tuple[Optional[discord.Invite], Dict[str, int]]:
        """Diff the live invite list against the snapshot.

        Returns (matched_invite, new_snapshot).
        """
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None, self._snapshot.get(guild.id, {})

        live = {i.code: i for i in invites}
        old = self._snapshot.get(guild.id, {})

        matched: Optional[discord.Invite] = None
        for code, inv in live.items():
            before = old.get(code, 0)
            if (inv.uses or 0) > before:
                matched = inv  # newest join used this code
                break

        self._snapshot[guild.id] = {c: i.uses or 0 for c, i in live.items()}
        return matched, self._snapshot[guild.id]

    async def _get_stat(self, session, guild_id: int, inviter_id: int) -> InviteStat:
        stat = (await session.execute(
            select(InviteStat).where(
                InviteStat.guild_id == guild_id,
                InviteStat.inviter_id == inviter_id,
            ))).scalar_one_or_none()
        if not stat:
            stat = InviteStat(guild_id=guild_id, inviter_id=inviter_id)
            session.add(stat)
            await session.flush()
        return stat

    async def _total_invites(self, guild_id: int, inviter_id: int) -> InviteStat:
        async with async_session_maker() as session:
            stat = await self._get_stat(session, guild_id, inviter_id)
            await session.commit()
            await session.refresh(stat)
            return stat

    # ── events ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._snapshot_guild(guild)
        self._ready = True
        logger.info(f"Invite snapshots loaded for {len(self.bot.guilds)} guild(s)")

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._snapshot_guild(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        snap = self._snapshot.setdefault(invite.guild.id, {})
        snap[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        snap = self._snapshot.setdefault(invite.guild.id, {})
        snap.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot or member.guild is None:
            return
        if not self._ready:
            return

        invite, _snap = await self._resolve_inviter(member.guild)
        inviter_id = invite.inviter.id if (invite and invite.inviter and not invite.inviter.bot) else None

        async with async_session_maker() as session:
            session.add(InviteLog(
                guild_id=member.guild.id,
                invitee_id=member.id,
                inviter_id=inviter_id,
                code=invite.code if invite else None,
            ))
            if inviter_id:
                stat = await self._get_stat(session, member.guild.id, inviter_id)
                stat.real_invites += 1
            await session.commit()

        if inviter_id:
            logger.info(f"{member} joined {member.guild} via invite from {inviter_id}")
            # optional welcome ping channel — keep silent by default
            channel_id = getattr(settings, "INVITE_ALERT_CHANNEL_ID", None)
            if channel_id:
                try:
                    ch = self.bot.get_channel(int(channel_id))
                    if ch:
                        inviter = member.guild.get_member(inviter_id)
                        await ch.send(
                            embed=purple_embed(
                                f"{E('invite')} New Invite",
                                f"**{member.name}** just joined — invited by "
                                f"**{inviter.mention if inviter else f'<@{inviter_id}>'}**! "
                                f"{E('sparkles')}",
                            ))
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        async with async_session_maker() as session:
            log = (await session.execute(
                select(InviteLog)
                .where(
                    InviteLog.guild_id == member.guild.id,
                    InviteLog.invitee_id == member.id,
                    InviteLog.left_at.is_(None),
                )
                .order_by(desc(InviteLog.created_at))
                .limit(1))).scalar_one_or_none()
            if not log:
                return
            log.left_at = datetime.utcnow()
            if log.inviter_id:
                stat = await self._get_stat(session, member.guild.id, log.inviter_id)
                if stat.real_invites > 0:
                    stat.real_invites -= 1
            await session.commit()

    # ── commands ─────────────────────────────────────────────────────

    @commands.hybrid_command(name="invites", description="Show your (or someone's) invite count and progress")
    @commands.guild_only()
    async def invites(self, ctx: commands.Context, user: discord.User = None):
        target = user or ctx.author
        stat = await self._total_invites(ctx.guild.id, target.id)
        total = stat.real_invites + stat.bonus_invites
        available = stat.available

        # next plan the user can still unlock
        async with async_session_maker() as session:
            plans = (await session.execute(
                select(Plan)
                .where(Plan.is_active == True, Plan.required_invites > 0)  # noqa: E712
                .order_by(Plan.required_invites))).scalars().all()

        next_plan = next((p for p in plans if p.required_invites > total), None)

        embed = purple_embed(
            f"{E('invite')} Invites — {target.display_name}",
            f"{E('users')} **{total}** total invites\n"
            f"{E('gift')} **{stat.bonus_invites}** bonus • {E('success')} **{available}** available to spend\n"
            f"{E('vps')} **{stat.invites_spent}** already used for VPS plans",
        )
        if next_plan:
            missing = next_plan.required_invites - total
            bar_filled = int((total / next_plan.required_invites) * 14)
            bar = "▰" * bar_filled + "▱" * (14 - bar_filled)
            embed.add_field(
                name=f"{E('star')} Next unlock — {next_plan.name}",
                value=(f"{bar}\n"
                       f"{next_plan.ram}MB RAM • {next_plan.cpu} vCPU • {next_plan.disk}GB SSD\n"
                       f"*{missing} more invite{'s' if missing != 1 else ''} to go!*"),
                inline=False,
            )
        elif plans:
            embed.add_field(
                name=f"{E('trophy')} All plans unlocked",
                value="You have every invite plan available. Massive respect! " + E("fire"),
                inline=False,
            )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=settings.BOT_FOOTER)
        await ctx.send(embed=embed, ephemeral=user is None)

    @commands.hybrid_command(name="inviteleaderboard", aliases=["topinvites", "invtop"],
                             description="Top inviters on this server")
    @commands.guild_only()
    async def inviteleaderboard(self, ctx: commands.Context):
        async with async_session_maker() as session:
            stats = (await session.execute(
                select(InviteStat)
                .where(InviteStat.guild_id == ctx.guild.id)
                .order_by(desc(InviteStat.real_invites + InviteStat.bonus_invites))
                .limit(10))).scalars().all()

        if not stats:
            await ctx.send(embed=info_embed(
                f"{E('invite')} Leaderboard",
                "No invites tracked yet — be the first to invite your friends! " + E("sparkles")),
                ephemeral=True)
            return

        medals = [f"{E('crown')}", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        lines = []
        for i, s in enumerate(stats):
            total = s.real_invites + s.bonus_invites
            member = ctx.guild.get_member(s.inviter_id)
            name = member.display_name if member else f"`{s.inviter_id}`"
            lines.append(f"{medals[i]} **{name}** — **{total}** invites")

        embed = purple_embed(
            f"{E('trophy')} Invite Leaderboard",
            "\n".join(lines),
        )
        embed.set_footer(text=f"{settings.BOT_FOOTER} • invite friends → unlock bigger VPS plans")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="grantinvites", description="Grant bonus invites to a member (Admin)")
    @commands.guild_only()
    async def grantinvites(self, ctx: commands.Context, user: discord.User, amount: int, *, reason: str = "No reason provided"):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return
        if amount == 0:
            await ctx.send(embed=error_embed("Invalid Amount", "Amount can't be 0."), ephemeral=True)
            return

        stat = await self._total_invites(ctx.guild.id, user.id)

        async with async_session_maker() as session:
            s = await self._get_stat(session, ctx.guild.id, user.id)
            s.bonus_invites = max(0, s.bonus_invites + amount)
            new_total = s.real_invites + s.bonus_invites
            await session.commit()

        verb = "granted to" if amount > 0 else "removed from"
        await ctx.send(embed=success_embed(
            f"{E('gift')} Bonus Invites Updated",
            f"**{amount:+d}** invites {verb} {user.mention}.\n"
            f"Reason: *{reason}*\n"
            f"New total: **{new_total}** invites.",
        ), ephemeral=True)

    @commands.hybrid_command(name="resetinvites", description="Reset a member's invite stats (Admin)")
    @commands.guild_only()
    async def resetinvites(self, ctx: commands.Context, user: discord.User):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return
        async with async_session_maker() as session:
            stat = (await session.execute(
                select(InviteStat).where(
                    InviteStat.guild_id == ctx.guild.id,
                    InviteStat.inviter_id == user.id,
                ))).scalar_one_or_none()
            if not stat:
                await ctx.send(embed=info_embed("Nothing to reset", f"{user.mention} has no invite stats."), ephemeral=True)
                return
            stat.real_invites = 0
            stat.bonus_invites = 0
            stat.invites_spent = 0
            await session.commit()
        await ctx.send(embed=success_embed(
            f"{E('refresh')} Invites Reset",
            f"{user.mention}'s invite stats were wiped clean.",
        ), ephemeral=True)


async def setup(bot):
    await bot.add_cog(InvitesCog(bot))
