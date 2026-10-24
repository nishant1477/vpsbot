import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
from typing import Optional, List, Dict, Tuple
import asyncio
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from utils import (
    error_embed, success_embed, info_embed, warning_embed,
    has_permission, is_staff, is_admin, is_owner, PermissionLevel,
    log_security_event, get_logger,
)
from database import async_session_maker, GuardianLog, GuildConfig, UserWarning
from config import settings
from sqlalchemy import select, func, desc, delete as sa_delete

logger = get_logger("guardian")

# matches discord.gg/xxx, discord.com/invite/xxx, discordapp.com/invite/xxx
INVITE_RE = re.compile(
    r"(?:discord\.(?:gg|io|me|li)|discord(?:app)?\.com/invite)/\S+",
    re.IGNORECASE,
)


class GuardianCog(commands.Cog):
    """Guardian v2 security suite.

    Protections:
      • anti-raid        — mass joins alert / optional auto-response
      • anti-spam        — flood detection with an ESCALATING punishment ladder
      • dupe-spam        — same message repeated 4x in 60s
      • anti-mention     — mass ping protection
      • anti-invite      — Discord invite-link filter (toggle)
      • anti-bot         — unauthorised bots added by non-admins are kicked
      • account-age gate — kick brand-new accounts (configurable)
      • anti-nuke        — mass bans / kicks / channel & role deletes / webhook
                           wipes, with configurable response (strip/kick/ban)
                           + instant owner DM alerts
    """

    def __init__(self, bot):
        self.bot = bot
        self.join_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.message_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.mention_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.dupe_tracker: Dict[int, List[Tuple[datetime, str]]] = defaultdict(list)
        self.spam_strikes: Dict[int, List[datetime]] = defaultdict(list)
        self.channel_delete_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.role_delete_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.webhook_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.kick_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.ban_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.whitelist_cache: set = set()
        self._load_whitelist()
        self.cleanup_task.start()

    # ── whitelist ────────────────────────────────────────────────────

    def _load_whitelist(self):
        self.whitelist_cache = set(settings.WHITELIST_USER_IDS) | set(settings.effective_owner_ids)

    async def _load_whitelist_from_db(self):
        """Merge the persisted per-guild whitelist (survives restarts)."""
        try:
            async with async_session_maker() as session:
                rows = (await session.execute(
                    select(GuildConfig.whitelist_user_ids))).all()
            for (ids,) in rows:
                for uid in ids or []:
                    try:
                        self.whitelist_cache.add(int(uid))
                    except (TypeError, ValueError):
                        continue
        except Exception as exc:
            logger.debug(f"whitelist DB load failed: {exc}")

    async def _persist_whitelist(self, user_id: int, add: bool) -> bool:
        """Add/remove a user from EVERY guild's persisted whitelist."""
        try:
            async with async_session_maker() as session:
                rows = (await session.execute(select(GuildConfig))).scalars().all()
                for cfg in rows:
                    ids = [int(u) for u in (cfg.whitelist_user_ids or [])
                           if str(u).isdigit()]
                    changed = False
                    if add and user_id not in ids:
                        ids.append(user_id)
                        changed = True
                    elif not add and user_id in ids:
                        ids.remove(user_id)
                        changed = True
                    if changed:
                        cfg.whitelist_user_ids = ids
                await session.commit()
            return True
        except Exception as exc:
            logger.error(f"whitelist persist failed: {exc}")
            return False

    def is_whitelisted(self, user_id: int) -> bool:
        return user_id in self.whitelist_cache

    def cog_unload(self):
        self.cleanup_task.cancel()

    @tasks.loop(minutes=5)
    async def cleanup_task(self):
        now = datetime.now(timezone.utc)
        trackers = [self.join_tracker, self.message_tracker, self.mention_tracker,
                    self.dupe_tracker, self.spam_strikes,
                    self.channel_delete_tracker, self.role_delete_tracker,
                    self.webhook_tracker, self.kick_tracker, self.ban_tracker]
        for tracker in trackers:
            for key in list(tracker.keys()):
                tracker[key] = [t for t in tracker[key]
                                if (now - t[0] if isinstance(t, tuple) else now - t)
                                .total_seconds() < 3600]
                if not tracker[key]:
                    del tracker[key]

    # ── helpers ──────────────────────────────────────────────────────

    async def has_staff_perms(self, member) -> bool:
        if isinstance(member, discord.Member):
            if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
                return True
            return is_staff(member)
        return False

    async def check_guild_config(self, guild_id: int, setting: str) -> bool:
        # guild-level toggle; falls back to global setting
        async with async_session_maker() as session:
            config = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one_or_none()
            if config is None:
                return True
            return bool(getattr(config, setting, True))

    async def get_guild_setting(self, guild_id: int, attr: str, default=None):
        """Read any per-guild config column with a fallback default."""
        async with async_session_maker() as session:
            config = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one_or_none()
        if config is None:
            return default
        value = getattr(config, attr, None)
        return default if value is None else value

    async def set_guild_setting(self, guild_id: int, attr: str, value) -> bool:
        """Write any per-guild config column; returns True on success."""
        try:
            async with async_session_maker() as session:
                config = (await session.execute(
                    select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one_or_none()
                if not config:
                    config = GuildConfig(guild_id=guild_id)
                    session.add(config)
                setattr(config, attr, value)
                await session.commit()
            return True
        except Exception as exc:
            logger.error(f"set_guild_setting({attr}) failed: {exc}")
            return False

    async def alert_owners(self, guild: discord.Guild, actor, action: str, containment: str):
        """DM every bot owner + the guild owner when a nuke is contained."""
        if not settings.ANTI_NUKE_ALERT_OWNERS:
            return
        embed = error_embed(
            "🚨 NUKE ATTEMPT CONTAINED",
            f"**Server:** {guild.name} (`{guild.id}`)\n"
            f"**Attacker:** {actor} (`{actor.id}`)\n"
            f"**Detected:** {action.replace('_', ' ')}\n"
            f"**Response:** {containment}\n\n"
            f"Review the audit log and re-secure roles immediately.")
        embed.set_footer(text=f"{settings.BOT_NAME} • Guardian v2")
        targets = set(settings.effective_owner_ids)
        if guild.owner_id:
            targets.add(guild.owner_id)
        for uid in targets:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                if user:
                    await user.send(embed=embed)
            except Exception:
                continue  # DMs closed — best effort

    async def log_action(self, guild_id: int, action: str, target_id: int = None,
                         target_name: str = None, moderator_id: int = None,
                         moderator_name: str = None, reason: str = None,
                         punishment: str = None, details: dict = None):
        try:
            async with async_session_maker() as session:
                session.add(GuardianLog(
                    guild_id=guild_id,
                    action=action,
                    target_id=target_id,
                    target_name=target_name,
                    moderator_id=moderator_id,
                    moderator_name=moderator_name,
                    reason=reason,
                    details=details or {},
                    punishment=punishment,
                    triggered_by="guardian",
                ))
                await session.commit()
        except Exception as exc:
            logger.error(f"Guardian log write failed: {exc}")

        log_security_event(action, guild_id, target_id, extra=reason or "")

    async def get_log_channel(self, guild) -> Optional[discord.TextChannel]:
        async with async_session_maker() as session:
            config = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild.id))).scalar_one_or_none()
            channel_id = config.guardian_log_channel_id if config else None
        if channel_id:
            return guild.get_channel(int(channel_id))
        return None

    async def punish(self, member: discord.Member, punishment: str, reason: str) -> bool:
        """Apply a punishment; returns success."""
        try:
            if punishment == "ban":
                await member.ban(reason=reason)
            elif punishment == "kick":
                await member.kick(reason=reason)
            elif punishment == "timeout":
                await member.timeout(timedelta(hours=1), reason=reason)
            elif punishment == "mute":
                mute_role = discord.utils.get(member.guild.roles, name="Muted")
                if mute_role:
                    await member.add_roles(mute_role, reason=reason)
                    await asyncio.sleep(600)
                    try:
                        await member.remove_roles(mute_role, reason="mute expired")
                    except Exception:
                        pass
                else:
                    await member.timeout(timedelta(minutes=10), reason=reason)
            elif punishment == "warn":
                pass  # logged only
            return True
        except Exception as exc:
            logger.warning(f"Punish failed ({punishment}) on {member}: {exc}")
            return False

    # ── anti-raid ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        await self._load_whitelist_from_db()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not settings.GUARDIAN_ENABLED:
            return

        # ── anti-bot: unauthorised bots are kicked instantly ────────────
        if member.bot and settings.ANTI_BOT_ENABLED and \
                await self.check_guild_config(member.guild.id, "anti_bot_enabled"):
            await self.handle_bot_join(member)
            return

        if self.is_whitelisted(member.id):
            return

        # ── account-age gate ─────────────────────────────────────────────
        min_days = await self.get_guild_setting(
            member.guild.id, "anti_min_account_days", settings.ANTI_MIN_ACCOUNT_DAYS) or 0
        if min_days > 0:
            age_days = (datetime.now(timezone.utc) - member.created_at).days
            if age_days < min_days:
                try:
                    await member.send(embed=warning_embed(
                        "⛔ Account Too New",
                        f"Your Discord account must be at least **{min_days} day(s)** old "
                        f"to join **{member.guild.name}**. Try again later."))
                except Exception:
                    pass
                try:
                    await member.kick(reason=f"Guardian: account age {age_days}d < {min_days}d")
                    await self.log_action(
                        member.guild.id, "ANTI_ALT_TRIGGERED",
                        target_id=member.id, target_name=str(member),
                        punishment="kicked",
                        reason=f"account too new ({age_days}d < {min_days}d)")
                except Exception as exc:
                    logger.debug(f"account-age kick failed: {exc}")
                return

        if not await self.check_guild_config(member.guild.id, "anti_raid_enabled"):
            return

        now = datetime.now(timezone.utc)
        self.join_tracker[member.guild.id].append(now)

        recent_joins = len([t for t in self.join_tracker[member.guild.id]
                            if (now - t).total_seconds() <= settings.ANTI_RAID_WINDOW])

        if recent_joins >= settings.ANTI_RAID_THRESHOLD:
            await self.trigger_anti_raid(member.guild, recent_joins)

    async def handle_bot_join(self, bot_member: discord.Member):
        """Kick bots that were added by users without staff/admin powers."""
        guild = bot_member.guild
        await asyncio.sleep(1.5)  # let the audit log settle

        actor = None
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add, limit=1):
                if entry.target and entry.target.id == bot_member.id:
                    actor = entry.user
                break
        except Exception:
            pass

        if actor and (self.is_whitelisted(actor.id) or await self.has_staff_perms(actor)):
            return  # added by trusted staff — allowed

        try:
            await bot_member.kick(reason="Guardian: bot added by unauthorised user")
        except Exception as exc:
            logger.debug(f"anti-bot kick failed: {exc}")
            return

        await self.log_action(
            guild.id, "ANTI_BOT_TRIGGERED",
            target_id=bot_member.id, target_name=str(bot_member),
            moderator_id=actor.id if actor else None,
            moderator_name=str(actor) if actor else None,
            punishment="bot_kicked",
            reason="bot added by non-admin")
        channel = await self.get_log_channel(guild) or guild.system_channel
        if channel:
            try:
                await channel.send(embed=warning_embed(
                    "🤖 Unauthorised Bot Removed",
                    f"Bot **{bot_member}** was added by "
                    f"{actor.mention if actor else 'an unknown user'} — who lacks admin powers.\n"
                    f"The bot has been kicked. Whitelist the adder with `!whitelist add` if this was intended."))
            except Exception:
                pass

    async def trigger_anti_raid(self, guild: discord.Guild, joins: int):
        self.join_tracker[guild.id].clear()
        await self.log_action(
            guild.id, "ANTI_RAID_TRIGGERED",
            reason=f"{joins} joins in {settings.ANTI_RAID_WINDOW}s",
            punishment="alert",
        )
        channel = await self.get_log_channel(guild) or guild.system_channel
        if channel:
            try:
                embed = warning_embed(
                    "🛡️ Anti-Raid Triggered",
                    f"**{joins}** members joined within {settings.ANTI_RAID_WINDOW}s.\n"
                    f"Recommended: enable verification or lockdown (`!lockdown`).",
                )
                await channel.send(embed=embed)
            except Exception:
                pass

    # ── anti-spam / anti-mention ─────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not settings.GUARDIAN_ENABLED or not message.guild:
            return
        if message.author.bot:
            return
        if self.is_whitelisted(message.author.id):
            return
        if await self.has_staff_perms(message.author):
            return

        now = datetime.now(timezone.utc)
        self.message_tracker[message.author.id].append(now)

        # anti-spam (with escalating punishment ladder)
        if await self.check_guild_config(message.guild.id, "anti_spam_enabled"):
            recent = len([t for t in self.message_tracker[message.author.id]
                          if (now - t).total_seconds() <= settings.ANTI_SPAM_WINDOW])
            if recent >= settings.ANTI_SPAM_THRESHOLD:
                await self.trigger_anti_spam(message, recent)
                return

        # dupe-spam: identical message 4x within 60s
        if message.content and len(message.content.strip()) >= 3:
            self.dupe_tracker[message.author.id].append((now, message.content.strip()))
            self.dupe_tracker[message.author.id] = [
                (t, c) for (t, c) in self.dupe_tracker[message.author.id]
                if (now - t).total_seconds() <= 60]
            same = sum(1 for _, c in self.dupe_tracker[message.author.id]
                       if c == message.content.strip())
            if same >= 4:
                self.dupe_tracker[message.author.id].clear()
                await self.trigger_dupe_spam(message, same)
                return

        # anti-invite: block Discord invite links
        if await self.check_guild_config(message.guild.id, "anti_invite_enabled"):
            if INVITE_RE.search(message.content or ""):
                try:
                    await message.delete()
                    await message.channel.send(
                        embed=warning_embed(
                            "🔗 Invite Link Blocked",
                            f"{message.author.mention} posting server invites is not allowed here."),
                        delete_after=5)
                    await self.log_action(
                        message.guild.id, "ANTI_INVITE_TRIGGERED",
                        target_id=message.author.id, target_name=str(message.author),
                        punishment="msg_deleted", reason="posted an invite link")
                except Exception:
                    pass
                return

        # anti-mention
        if await self.check_guild_config(message.guild.id, "anti_mention_enabled"):
            mentions = len(message.mentions) + len(message.role_mentions)
            if mentions > 0:
                self.mention_tracker[message.author.id].append(now)
                recent_mentions = len([t for t in self.mention_tracker[message.author.id]
                                       if (now - t).total_seconds() <= settings.ANTI_SPAM_WINDOW])
                if recent_mentions >= settings.ANTI_MENTION_LIMIT:
                    await self.trigger_anti_mention(message, mentions)

    async def trigger_anti_spam(self, message: discord.Message, count: int):
        """Flood hit — escalate: warn → 10m timeout → 1h timeout → kick → ban."""
        self.message_tracker[message.author.id].clear()
        member = message.author
        now = datetime.now(timezone.utc)

        # strikes decay after 1 hour
        self.spam_strikes[member.id] = [
            t for t in self.spam_strikes[member.id] if (now - t).total_seconds() < 3600]
        self.spam_strikes[member.id].append(now)
        strikes = len(self.spam_strikes[member.id])

        ladder = ["warn", "timeout", "timeout60", "kick", "ban"]
        if settings.ANTI_SPAM_ESCALATION:
            step = ladder[min(strikes - 1, len(ladder) - 1)]
        else:
            step = "timeout"

        label = {
            "warn": "Warning",
            "timeout": "10m timeout",
            "timeout60": "1h timeout",
            "kick": "kicked",
            "ban": "banned",
        }.get(step, step)

        try:
            await message.channel.send(
                embed=warning_embed(
                    "🛡️ Anti-Spam",
                    f"{member.mention} slow down! "
                    f"({count} messages in {settings.ANTI_SPAM_WINDOW}s)\n"
                    f"Strike **{strikes}** → {label}"),
                delete_after=6,
            )

            if step == "warn":
                pass  # verbal warning only
            elif step == "timeout":
                await member.timeout(timedelta(minutes=10), reason="Guardian: anti-spam (strike %d)" % strikes)
            elif step == "timeout60":
                await member.timeout(timedelta(hours=1), reason="Guardian: anti-spam (strike %d)" % strikes)
            elif step == "kick":
                await member.kick(reason="Guardian: anti-spam escalation")
            elif step == "ban":
                await member.ban(reason="Guardian: anti-spam escalation", delete_message_days=1)

            await self.log_action(
                message.guild.id, "ANTI_SPAM_TRIGGERED",
                target_id=member.id, target_name=str(member),
                punishment=step, reason=f"strike {strikes}: {count} msgs/{settings.ANTI_SPAM_WINDOW}s")
        except Exception as exc:
            logger.debug(f"anti-spam action failed: {exc}")

    async def trigger_dupe_spam(self, message: discord.Message, count: int):
        """Same message repeated over and over — light timeout + cleanup."""
        try:
            await message.delete()
            await message.author.timeout(timedelta(minutes=5), reason="Guardian: dupe-spam")
            await message.channel.send(
                embed=warning_embed(
                    "🛡️ Dupe-Spam",
                    f"{message.author.mention} stop repeating the same message "
                    f"({count}x in 60s) — 5 minute timeout."),
                delete_after=6)
            await self.log_action(
                message.guild.id, "ANTI_DUPE_TRIGGERED",
                target_id=message.author.id, target_name=str(message.author),
                punishment="timeout_5m", reason=f"{count} identical messages/60s")
        except Exception as exc:
            logger.debug(f"dupe-spam action failed: {exc}")

    async def trigger_anti_mention(self, message: discord.Message, mentions: int):
        self.mention_tracker[message.author.id].clear()
        try:
            await message.delete()
            await message.channel.send(
                embed=warning_embed("🛡️ Anti-Mention",
                                    f"{message.author.mention} mass-mentioning is not allowed "
                                    f"({mentions} mentions)."),
                delete_after=5,
            )
            await self.log_action(message.guild.id, "ANTI_MENTION_TRIGGERED",
                                  target_id=message.author.id,
                                  target_name=str(message.author),
                                  punishment="msg_deleted",
                                  reason=f"{mentions} mentions")
        except Exception:
            pass

    # ── anti-nuke ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if not settings.GUARDIAN_ENABLED:
            return
        if not await self.check_guild_config(guild.id, "anti_nuke_enabled"):
            return

        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=1):
                if not entry.target or entry.target.id != user.id:
                    continue
                if self.is_whitelisted(entry.user.id) or await self.has_staff_perms(entry.user):
                    return
                now = datetime.now(timezone.utc)
                self.ban_tracker[guild.id].append(now)
                recent_bans = len([t for t in self.ban_tracker[guild.id]
                                   if (now - t).total_seconds() <= 10])
                if recent_bans >= 3:
                    await self.trigger_anti_nuke(guild, entry.user, "mass_ban")
                return
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not settings.GUARDIAN_ENABLED:
            return
        if not await self.check_guild_config(member.guild.id, "anti_nuke_enabled"):
            return

        await asyncio.sleep(1)
        try:
            async for entry in member.guild.audit_logs(action=discord.AuditLogAction.kick, limit=1):
                if not entry.target or entry.target.id != member.id:
                    continue
                if self.is_whitelisted(entry.user.id) or await self.has_staff_perms(entry.user):
                    return
                now = datetime.now(timezone.utc)
                self.kick_tracker[member.guild.id].append(now)
                recent_kicks = len([t for t in self.kick_tracker[member.guild.id]
                                    if (now - t).total_seconds() <= 10])
                if recent_kicks >= 3:
                    await self.trigger_anti_nuke(member.guild, entry.user, "mass_kick")
                return
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not settings.GUARDIAN_ENABLED:
            return
        if not await self.check_guild_config(channel.guild.id, "anti_nuke_enabled"):
            return

        await asyncio.sleep(1)
        try:
            async for entry in channel.guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=1):
                if self.is_whitelisted(entry.user.id) or await self.has_staff_perms(entry.user):
                    return
                now = datetime.now(timezone.utc)
                self.channel_delete_tracker[channel.guild.id].append(now)
                recent = len([t for t in self.channel_delete_tracker[channel.guild.id]
                              if (now - t).total_seconds() <= 10])
                if recent >= 3:
                    await self.trigger_anti_nuke(channel.guild, entry.user, "mass_channel_delete")
                return
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not settings.GUARDIAN_ENABLED:
            return
        if not await self.check_guild_config(role.guild.id, "anti_nuke_enabled"):
            return

        await asyncio.sleep(1)
        try:
            async for entry in role.guild.audit_logs(action=discord.AuditLogAction.role_delete, limit=1):
                if self.is_whitelisted(entry.user.id) or await self.has_staff_perms(entry.user):
                    return
                now = datetime.now(timezone.utc)
                self.role_delete_tracker[role.guild.id].append(now)
                recent = len([t for t in self.role_delete_tracker[role.guild.id]
                              if (now - t).total_seconds() <= 10])
                if recent >= 3:
                    await self.trigger_anti_nuke(role.guild, entry.user, "mass_role_delete")
                return
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        """Catch webhook-wipe nukes and webhook-spam attacks."""
        if not settings.GUARDIAN_ENABLED or not settings.ANTI_WEBHOOK_ENABLED:
            return
        if not await self.check_guild_config(channel.guild.id, "anti_webhook_enabled"):
            return

        await asyncio.sleep(1.5)  # audit-log propagation
        try:
            async for entry in channel.guild.audit_logs(limit=3):
                if entry.action not in (discord.AuditLogAction.webhook_delete,
                                        discord.AuditLogAction.webhook_create):
                    continue
                actor = entry.user
                if actor.bot:
                    return
                if self.is_whitelisted(actor.id) or await self.has_staff_perms(actor):
                    return
                now = datetime.now(timezone.utc)
                self.webhook_tracker[channel.guild.id].append(now)
                recent = len([t for t in self.webhook_tracker[channel.guild.id]
                              if (now - t).total_seconds() <= 10])
                kind = "mass_webhook_delete" if entry.action == discord.AuditLogAction.webhook_delete \
                    else "mass_webhook_create"
                if recent >= settings.ANTI_NUKE_THRESHOLD:
                    await self.trigger_anti_nuke(channel.guild, actor, kind)
                return
        except Exception:
            pass

    async def trigger_anti_nuke(self, guild: discord.Guild, actor, action: str):
        """Contain a nuke attempt using the configured response mode.

        Modes (per-guild via !guardian-punish):
          strip — remove every role from the attacker (default, safest)
          kick  — kick the attacker
          ban   — ban the attacker
        Plus instant DM alerts to the bot owners and the guild owner.
        """
        mode = (await self.get_guild_setting(
            guild.id, "anti_nuke_action", settings.ANTI_NUKE_ACTION) or "strip").lower()
        containment = "attempted"
        member = guild.get_member(actor.id)

        try:
            if member and mode == "ban":
                await member.ban(reason=f"Guardian anti-nuke: {action}", delete_message_days=0)
                containment = "banned"
            elif member and mode == "kick":
                await member.kick(reason=f"Guardian anti-nuke: {action}")
                containment = "kicked"
            elif member:  # strip (default)
                perms = discord.Permissions.none()
                perms.view_channel = True
                await member.edit(roles=[], reason=f"Guardian anti-nuke: {action}")
                containment = "roles_stripped"
            await self.log_action(guild.id, "ANTI_NUKE_TRIGGERED",
                                  target_id=actor.id, target_name=str(actor),
                                  punishment=containment, reason=action)
        except Exception as exc:
            logger.warning(f"anti-nuke containment failed: {exc}")
            await self.log_action(guild.id, "ANTI_NUKE_FAILED",
                                  target_id=actor.id, target_name=str(actor),
                                  punishment="none", reason=f"{action}: {exc}")

        # register with the Security Suite containment registry (de-dup layer)
        try:
            from utils.security_core import note_containment
            note_containment(guild.id, actor.id, action)
        except Exception:
            pass

        # DM the owners instantly — they are usually asleep when nukes happen
        try:
            await self.alert_owners(guild, actor, action, containment)
        except Exception as exc:
            logger.debug(f"owner alert failed: {exc}")

        log_channel = await self.get_log_channel(guild) or guild.system_channel
        if log_channel:
            try:
                embed = error_embed(
                    "🚨 Anti-Nuke Triggered",
                    f"{actor.mention} attempted **{action.replace('_', ' ')}**.\n"
                    f"Response mode: **{mode}** → {containment}.\n"
                    f"Owners have been alerted.")
                await log_channel.send(embed=embed)
            except Exception:
                pass

    # ── commands ─────────────────────────────────────────────────────

    @commands.hybrid_command(name="guardian", description="Guardian security dashboard")
    @commands.guild_only()
    async def guardian(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            config = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == ctx.guild.id))).scalar_one_or_none()
            recent_logs = (await session.execute(
                select(GuardianLog)
                .where(GuardianLog.guild_id == ctx.guild.id)
                .order_by(desc(GuardianLog.created_at))
                .limit(8))).scalars().all()

        embed = info_embed("🛡️ Guardian Security", f"Protection status for **{ctx.guild.name}**")

        if config is not None:
            toggles = config
        else:
            # An un-persisted GuildConfig() has NULL columns (defaults apply at
            # flush, not construction) — fall back to the settings defaults so
            # the panel agrees with what check_guild_config actually enforces.
            class _Defaults:
                anti_raid_enabled = settings.ANTI_RAID_ENABLED
                anti_spam_enabled = settings.ANTI_SPAM_ENABLED
                anti_mention_enabled = settings.ANTI_MENTION_ENABLED
                anti_nuke_enabled = settings.ANTI_NUKE_ENABLED
                anti_invite_enabled = settings.ANTI_INVITE_ENABLED
                anti_nuke_action = settings.ANTI_NUKE_ACTION
                anti_min_account_days = settings.ANTI_MIN_ACCOUNT_DAYS
            toggles = _Defaults()
        rows = [
            ("Anti-Raid", toggles.anti_raid_enabled),
            ("Anti-Spam", toggles.anti_spam_enabled),
            ("Anti-Mention", toggles.anti_mention_enabled),
            ("Anti-Nuke", toggles.anti_nuke_enabled),
            ("Anti-Invite", toggles.anti_invite_enabled),
            ("Nuke Response", f"{toggles.anti_nuke_action}"),
            ("Min Account Age", f"{toggles.anti_min_account_days or 0}d"),
        ]
        for name, value in rows:
            if isinstance(value, bool):
                value = "🟢 Enabled" if value else "🔴 Disabled"
            else:
                value = f"⚙️ `{value}`"
            embed.add_field(name=name, value=value, inline=True)

        if recent_logs:
            logs_text = "\n".join(
                f"`{log.created_at.strftime('%m-%d %H:%M')}` **{log.action}**"
                + (f" → <@{log.target_id}>" if log.target_id else "")
                for log in recent_logs
            )
            embed.add_field(name="Recent Security Events", value=logs_text[:1024], inline=False)

        embed.set_footer(text="Toggle protections with the buttons below (admins only) • !guardian-punish strip|kick|ban")
        await ctx.send(embed=embed, view=GuardianConfigView(ctx.guild.id), ephemeral=True)

    @commands.hybrid_command(name="whitelist", description="Manage Guardian whitelist")
    @commands.guild_only()
    async def whitelist(self, ctx: commands.Context, action: str, user: discord.User = None):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        action = action.lower()
        if action == "add" and user:
            self.whitelist_cache.add(user.id)
            persisted = await self._persist_whitelist(user.id, add=True)
            note = "" if persisted else "\n⚠️ Could not persist to DB — this entry will be lost on restart."
            await ctx.send(embed=success_embed("Whitelisted", f"{user.mention} added to the whitelist.{note}"), ephemeral=True)
        elif action == "remove" and user:
            self.whitelist_cache.discard(user.id)
            await self._persist_whitelist(user.id, add=False)
            await ctx.send(embed=success_embed("Removed", f"{user.mention} removed from the whitelist."), ephemeral=True)
        elif action == "list":
            whitelisted = [f"<@{uid}>" for uid in self.whitelist_cache]
            embed = info_embed("🛡️ Whitelist",
                               "\n".join(whitelisted) if whitelisted else "No whitelisted users.")
            await ctx.send(embed=embed, ephemeral=True)
        else:
            await ctx.send(embed=error_embed(
                "Usage", "`!whitelist add @user` • `!whitelist remove @user` • `!whitelist list`"), ephemeral=True)

    @commands.hybrid_command(name="guardian-logs", description="View Guardian security logs")
    @commands.guild_only()
    async def guardian_logs(self, ctx: commands.Context, limit: int = 20):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            logs = (await session.execute(
                select(GuardianLog)
                .where(GuardianLog.guild_id == ctx.guild.id)
                .order_by(desc(GuardianLog.created_at))
                .limit(min(limit, 50)))).scalars().all()

        if not logs:
            await ctx.send(embed=info_embed("No Logs", "No Guardian events recorded yet."), ephemeral=True)
            return

        embed = info_embed("🛡️ Guardian Logs", f"Last {len(logs)} events")
        for log in logs:
            embed.add_field(
                name=f"{log.action} • {log.created_at.strftime('%Y-%m-%d %H:%M')}",
                value=(f"Target: {f'<@{log.target_id}>' if log.target_id else 'N/A'}\n"
                       f"Punishment: {log.punishment or '—'} • Reason: {log.reason or '—'}"),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    # ── moderation: warn system ─────────────────────────────────────

    @commands.hybrid_command(name="warn", description="Warn a member (Staff)")
    @commands.guild_only()
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        if member.bot:
            await ctx.send(embed=error_embed("Not Possible", "Bots cannot be warned."), ephemeral=True)
            return
        if member.top_role >= ctx.author.top_role and not is_owner(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "You cannot warn someone with an equal/higher role."), ephemeral=True)
            return

        async with async_session_maker() as session:
            session.add(UserWarning(
                guild_id=ctx.guild.id,
                user_id=member.id,
                moderator_id=ctx.author.id,
                reason=reason[:500],
            ))
            await session.commit()
            count = (await session.execute(
                select(func.count(UserWarning.id)).where(
                    UserWarning.guild_id == ctx.guild.id,
                    UserWarning.user_id == member.id))).scalar() or 0

        # auto-escalation thresholds
        action, acted = None, False
        if count >= settings.WARN_BAN_AT:
            action = f"🚫 banned ({count}/{settings.WARN_BAN_AT} warns)"
            acted = await self.punish(member, "ban", f"{count} warnings — latest: {reason}")
        elif count >= settings.WARN_KICK_AT:
            action = f"👢 kicked ({count}/{settings.WARN_KICK_AT} warns)"
            acted = await self.punish(member, "kick", f"{count} warnings — latest: {reason}")
        elif count >= settings.WARN_TIMEOUT_AT:
            action = f"⏳ 1h timeout ({count}/{settings.WARN_TIMEOUT_AT} warns)"
            acted = await self.punish(member, "timeout", f"{count} warnings — latest: {reason}")

        # try to DM the member
        try:
            await member.send(embed=warning_embed(
                f"⚠️ You were warned in {ctx.guild.name}",
                f"**Reason:** {reason}\n**Total warnings:** {count}\n"
                f"At **{settings.WARN_TIMEOUT_AT}** warns you get a 1h timeout, "
                f"at **{settings.WARN_KICK_AT}** a kick, at **{settings.WARN_BAN_AT}** a ban."))
        except Exception:
            pass

        await self.log_action(
            ctx.guild.id, "WARN_ISSUED", target_id=member.id, target_name=str(member),
            moderator_id=ctx.author.id, moderator_name=str(ctx.author),
            punishment=f"warn #{count}", reason=reason)

        embed = success_embed(
            f"⚠️ Warned {member.display_name}",
            f"**Reason:** {reason}\n**Total warnings:** {count}\n"
            f"(auto-timeout at {settings.WARN_TIMEOUT_AT} • kick at {settings.WARN_KICK_AT} • ban at {settings.WARN_BAN_AT})")
        if action:
            embed.add_field(name="Auto-Punishment", value=(action + (" ✅" if acted else " ❌ (missing perms)")), inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="warnings", description="Show a member's warnings (Staff)")
    @commands.guild_only()
    async def warnings(self, ctx: commands.Context, user: discord.User = None):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        target = user or ctx.author

        async with async_session_maker() as session:
            warns = (await session.execute(
                select(UserWarning)
                .where(UserWarning.guild_id == ctx.guild.id, UserWarning.user_id == target.id)
                .order_by(desc(UserWarning.created_at))
                .limit(10))).scalars().all()
            total = (await session.execute(
                select(func.count(UserWarning.id)).where(
                    UserWarning.guild_id == ctx.guild.id,
                    UserWarning.user_id == target.id))).scalar() or 0

        if not warns:
            await ctx.send(embed=success_embed("Clean Record", f"{target.mention} has no warnings. 🎉"), ephemeral=True)
            return

        embed = warning_embed(f"⚠️ Warnings — {target.display_name}", f"**Total:** {total} (showing latest {len(warns)})")
        for w in warns:
            embed.add_field(
                name=f"#{w.id} • {w.created_at.strftime('%Y-%m-%d %H:%M')}",
                value=f"Reason: {w.reason}\nBy: <@{w.moderator_id}>" if w.moderator_id else f"Reason: {w.reason}",
                inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="resetwarns", description="Clear a member's warnings (Admin)")
    @commands.guild_only()
    async def resetwarns(self, ctx: commands.Context, user: discord.User):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            result = await session.execute(
                sa_delete(UserWarning).where(
                    UserWarning.guild_id == ctx.guild.id, UserWarning.user_id == user.id))
            await session.commit()
            removed = result.rowcount or 0

        await ctx.send(embed=success_embed(
            "Warnings Cleared", f"Removed **{removed}** warning(s) from {user.mention}."), ephemeral=True)

    # ── moderation: mute / unmute ───────────────────────────────────

    @commands.hybrid_command(name="mute", description="Timeout a member (Staff)")
    @commands.guild_only()
    async def mute(self, ctx: commands.Context, member: discord.Member,
                   minutes: int = 10, *, reason: str = "No reason provided"):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        if member.top_role >= ctx.author.top_role and not is_owner(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "You cannot mute someone with an equal/higher role."), ephemeral=True)
            return
        minutes = max(1, min(minutes, 40320))  # Discord caps timeouts at 28 days
        try:
            await member.timeout(timedelta(minutes=minutes), reason=f"By {ctx.author}: {reason}")
        except Exception as exc:
            await ctx.send(embed=error_embed("Failed", str(exc)), ephemeral=True)
            return
        try:
            await member.send(embed=warning_embed(
                f"🔇 Muted in {ctx.guild.name}",
                f"**Duration:** {minutes} min\n**Reason:** {reason}"))
        except Exception:
            pass
        await self.log_action(ctx.guild.id, "MUTE_ISSUED", target_id=member.id,
                              target_name=str(member), moderator_id=ctx.author.id,
                              punishment=f"timeout {minutes}m", reason=reason)
        await ctx.send(embed=success_embed(
            "🔇 Muted", f"{member.mention} is muted for **{minutes} minute(s)**.\nReason: {reason}"), ephemeral=True)

    @commands.hybrid_command(name="unmute", description="Remove a member's timeout (Staff)")
    @commands.guild_only()
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No reason provided"):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}: {reason}")
        except Exception as exc:
            await ctx.send(embed=error_embed("Failed", str(exc)), ephemeral=True)
            return
        await ctx.send(embed=success_embed("🔊 Unmuted", f"{member.mention} can speak again."), ephemeral=True)

    # ── guardian v2 configuration commands ──────────────────────────

    @commands.hybrid_command(name="guardian-punish", description="Anti-nuke response mode: strip/kick/ban (Admin)")
    @commands.guild_only()
    async def guardian_punish(self, ctx: commands.Context, action: str):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        action = action.lower().strip()
        if action not in ("strip", "kick", "ban"):
            await ctx.send(embed=error_embed(
                "Invalid Mode", "Choose `strip` (remove roles), `kick` or `ban`."), ephemeral=True)
            return
        await self.set_guild_setting(ctx.guild.id, "anti_nuke_action", action)
        emoji = {"strip": "🧨", "kick": "👢", "ban": "🚫"}[action]
        await ctx.send(embed=success_embed(
            f"{emoji} Anti-Nuke Response: {action.title()}",
            f"When a nuke is detected, the attacker will now be **{action}ed** "
            f"(strip = all roles removed)."), ephemeral=True)

    @commands.hybrid_command(name="guardian-age", description="Minimum Discord account age in days, 0 = off (Admin)")
    @commands.guild_only()
    async def guardian_age(self, ctx: commands.Context, days: int):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        days = max(0, min(days, 365))
        await self.set_guild_setting(ctx.guild.id, "anti_min_account_days", days)
        if days == 0:
            await ctx.send(embed=success_embed(
                "Account-Age Gate Disabled", "New accounts can join freely."), ephemeral=True)
        else:
            await ctx.send(embed=success_embed(
                "⛔ Account-Age Gate Active",
                f"Accounts younger than **{days} day(s)** will be kicked on join."), ephemeral=True)

    @commands.hybrid_command(name="antiinvite", description="Block Discord invite links on/off (Admin)")
    @commands.guild_only()
    async def antiinvite(self, ctx: commands.Context, enabled: bool):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        await self.set_guild_setting(ctx.guild.id, "anti_invite_enabled", enabled)
        state = "🟢 Enabled — invite links will be deleted" if enabled else "🔴 Disabled"
        await ctx.send(embed=success_embed("🔗 Anti-Invite", state), ephemeral=True)


class GuardianConfigView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="Anti-Raid", style=discord.ButtonStyle.primary, emoji="🛡️", custom_id="arqon:g_anti_raid")
    async def anti_raid(self, interaction: discord.Interaction, button: Button):
        await self.toggle_setting(interaction, "anti_raid_enabled", "Anti-Raid")

    @discord.ui.button(label="Anti-Spam", style=discord.ButtonStyle.primary, emoji="💬", custom_id="arqon:g_anti_spam")
    async def anti_spam(self, interaction: discord.Interaction, button: Button):
        await self.toggle_setting(interaction, "anti_spam_enabled", "Anti-Spam")

    @discord.ui.button(label="Anti-Mention", style=discord.ButtonStyle.primary, emoji="📣", custom_id="arqon:g_anti_mention")
    async def anti_mention(self, interaction: discord.Interaction, button: Button):
        await self.toggle_setting(interaction, "anti_mention_enabled", "Anti-Mention")

    @discord.ui.button(label="Anti-Nuke", style=discord.ButtonStyle.danger, emoji="☢️", custom_id="arqon:g_anti_nuke")
    async def anti_nuke(self, interaction: discord.Interaction, button: Button):
        await self.toggle_setting(interaction, "anti_nuke_enabled", "Anti-Nuke")

    @discord.ui.button(label="Anti-Invite", style=discord.ButtonStyle.secondary, emoji="🔗", custom_id="arqon:g_anti_invite")
    async def anti_invite(self, interaction: discord.Interaction, button: Button):
        await self.toggle_setting(interaction, "anti_invite_enabled", "Anti-Invite")

    async def toggle_setting(self, interaction: discord.Interaction, setting: str, name: str):
        if not has_permission(interaction.user, PermissionLevel.ADMIN):
            await interaction.response.send_message(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            config = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == self.guild_id))).scalar_one_or_none()
            if not config:
                config = GuildConfig(guild_id=self.guild_id)
                session.add(config)
            current = getattr(config, setting, True)
            setattr(config, setting, not current)
            await session.commit()
            status = "Enabled" if not current else "Disabled"

        embed = success_embed(f"{name} {status}", f"{name} has been **{status.lower()}**.")
        await interaction.response.edit_message(embed=embed, view=self)


async def setup(bot):
    await bot.add_cog(GuardianCog(bot))
