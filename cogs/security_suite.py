"""Arqon Bot — Security Suite v3 (25 protections).

A defence-in-depth layer that complements Guardian v2:

  Guardian = enforcement (strip/kick/ban)   Suite = intelligence + response
  ──────────────────────────────────────────────────────────────────────────
   1. Smart Anti-Nuke            incidents + evidence on every mass action
   2. Admin Abuse Detector       rate-limits ALL audited admin actions
   3. Permission Escalation      auto-reverts dangerous role grants
   4. Anti-Webhook Attack        webhook burst + webhook message scanning
   5. Role Hierarchy Guard       reverts edits to admin-permission roles
   6. Adaptive Anti-Raid         burst joins -> auto lockdown (adaptive)
   7. Anti-Bot Raid              mass bot additions alert + containment
   8. Anti-Alt Detection         account risk scoring on join
   9. Anti-Impersonation         staff-name similarity shield
  10. Anti-Scam Intelligence     scam patterns + shared threat domains
  11. Emergency Lockdown         /emergency one-command server freeze
  12. Automatic Containment      auto timeouts on verified breaches
  13. Smart Recovery             snapshot + auto role recreation
  14. Anti-Permission Drift      periodic admin-list comparison
  15. Panic Command              /panic — maximum defence in one call
  16. Honeypot Detection         decoy channels catch raiders/scammers
  17. Staff Action Approval      two-person rule for sensitive actions
  18. Temporary Privileges       /granttemp — permissions that expire
  19. Incident Timeline          /incidents — chronological security feed
  20. Tamper-Evident Logs        sha256 hash chain + /verifylogs
  21. Cross-Server Threat Share  export/import/sync indicator packs
  22. Security Self-Test         /security-test coverage report
  23. Anti-Evasion               repeat offenders auto-banned
  24. Evidence Collection        message links, content & audit data saved
  25. Safe Mode                  /safe-mode — monitor without punishing
"""
import re
import json
import time
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import discord
from discord.ext import commands, tasks

from utils import (
    success_embed, error_embed, info_embed, warning_embed,
    is_admin, is_staff, get_logger, loading_embed,
)
from utils.embeds import base_embed
from utils.emoji import E
from utils import security_core as sc
from config import settings
from database import async_session_maker, GuildConfig
from sqlalchemy import select

logger = get_logger("security_suite")

NUKE_ACTIONS = ("mass_ban", "mass_kick", "mass_channel_delete", "mass_role_delete",
                "mass_webhook_delete", "mass_webhook_create", "admin_abuse",
                "bot_raid", "permission_escalation", "role_guard")

ABUSE_AUDIT_ACTIONS = {
    discord.AuditLogAction.kick, discord.AuditLogAction.ban,
    discord.AuditLogAction.member_prune, discord.AuditLogAction.webhook_delete,
    discord.AuditLogAction.channel_delete, discord.AuditLogAction.role_delete,
    discord.AuditLogAction.message_delete, discord.AuditLogAction.message_bulk_delete,
    discord.AuditLogAction.overwrite_delete,
}

DANGEROUS_ROLE_PERMS = ("administrator", "manage_guild", "manage_roles",
                        "manage_webhooks", "manage_channels", "ban_members")

SCAM_PATTERNS = [
    (r"free\s*nitro", "free-nitro scam"),
    (r"discord\.gg/\S*\s*(?:nitro|gift|giveaway)", "fake nitro invite"),
    (r"steamcommu?n?i?t?y?\s*\.\s*(?:ru|com)/gift", "steam gift phishing"),
    (r"(?:grabify|iplogger|ipgrab|yip\.su|ps3cf)\.\w+", "IP-logger link"),
    (r"(?:free|claim)\s+(?:vps|premium|robux|crypto|skins?|coins?)", "free-X bait"),
    (r"giveaway\W{0,20}(?:claim|click|join|win)", "giveaway bait"),
    (r"double\s+your\s+(?:crypto|money|btc|eth|cash)", "crypto-doubling scam"),
    (r"seed\s*phrase|recover\s+(?:your\s+)?wallet", "wallet phishing"),
    (r"(?:click|join)\s+fast|limited\s+slots?\W{0,20}free", "urgency bait"),
    (r"@everyone\s+.{0,40}(?:free|nitro|giveaway|claim)", "everyone-ping bait"),
]

BAD_DOMAINS = {
    "grabify.link", "iplogger.org", "iplogger.com", "ipgrab.org", "yip.su",
    "ps3cf.com", "steamcommunuty.com", "steamcommnity.com", "discrod.gift",
    "discord-nitro.gift", "nitro-gift.ru", "free-nitro.click",
}

URL_RE = re.compile(r"(?:https?://)?([a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+)", re.IGNORECASE)
DUR_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
DUR_UNITS = {"s": 1 / 60, "m": 1, "h": 60, "d": 1440}

# GuildConfig columns the suite is allowed to write. cfg_set() rejects
# anything else, so a crafted interaction can never smuggle an arbitrary
# attribute name into setattr().
_WRITABLE_CFG_ATTRS = {
    "sec_safe_mode", "sec_panic_mode", "sec_admin_abuse", "sec_escalation_block",
    "sec_role_guard", "sec_anti_alt", "sec_alt_min_risk", "sec_anti_impersonation",
    "sec_imp_similarity", "sec_anti_scam", "sec_scam_autodelete", "sec_raid_lockdown",
    "sec_auto_containment", "sec_perm_drift", "sec_honeypot", "sec_two_person",
    "sec_temp_priv", "sec_threat_share", "sec_nuke_threshold", "sec_abuse_threshold",
    "sec_bot_raid_threshold", "sec_lockdown_minutes", "threat_share_channel_id",
    "anti_raid_enabled", "anti_bot_enabled", "anti_webhook_enabled", "anti_nuke_enabled",
    "anti_nuke_action", "anti_min_account_days", "anti_raid_threshold",
    "anti_spam_enabled", "anti_spam_threshold", "anti_spam_window",
    "anti_mention_enabled", "anti_mention_limit", "anti_invite_enabled",
}


def _parse_minutes(text: str) -> Optional[int]:
    m = DUR_RE.match((text or "").strip())
    if not m:
        return None
    return max(1, int(int(m.group(1)) * DUR_UNITS[m.group(2).lower()]))


def _norm(name: str) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def _severity_embed(inc, guild) -> discord.Embed:
    color = sc.SEVERITY_COLOR.get(inc.severity, 0x3498DB)
    embed = base_embed(
        f"{E('shield')} {sc.SEVERITY_LABEL.get(inc.severity, 'INFO')} — {inc.itype.replace('_', ' ').title()}",
        f"{inc.summary}\n\n{E('info')} Incident `#{inc.id}` • {E('clock')} "
        f"<t:{int(inc.created_at.replace(tzinfo=timezone.utc).timestamp())}:R>"
        + (f"\n{E('gear')} Response: **{inc.response}**" if inc.response else ""),
        color,
        footer=f"{settings.BOT_NAME} • Security Suite v3",
    )
    if guild:
        embed.set_author(name=guild.name, icon_url=getattr(guild.icon, "url", None))
    if inc.actor_name:
        embed.add_field(name="Actor", value=f"{inc.actor_name}\n`{inc.actor_id or '?'}`", inline=True)
    if inc.target_name:
        embed.add_field(name="Target", value=f"{inc.target_name}\n`{inc.target_id or '?'}`", inline=True)
    return embed


class SecuritySuiteCog(commands.Cog):
    """Security Suite v3 — see module docstring for the 25 protections."""

    def __init__(self, bot):
        self.bot = bot
        # rate trackers
        self.join_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.abuse_tracker: Dict[tuple, List[datetime]] = defaultdict(list)
        self.nuke_tracker: Dict[tuple, List[datetime]] = defaultdict(list)
        self.bot_add_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.webhook_tracker: Dict[int, List[datetime]] = defaultdict(list)
        self.raid_active_until: Dict[int, float] = defaultdict(float)
        self.raid_history: Dict[int, float] = defaultdict(float)
        # emergency lockdown state: guild_id -> {"until": ts, "overwrites": {...}}
        self.lockdowns: Dict[int, dict] = {}

        self.drift_task.start()
        self.priv_task.start()
        self.lockdown_task.start()

    def cog_unload(self):
        for t in (self.drift_task, self.priv_task, self.lockdown_task):
            t.cancel()

    # ── config helpers ──────────────────────────────────────────────

    async def cfg(self, guild_id: int) -> GuildConfig:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one_or_none()
            if row is None:
                row = GuildConfig(guild_id=guild_id)
                session.add(row)
                try:
                    await session.commit()
                except Exception:
                    # another concurrent task inserted the same config row first
                    await session.rollback()
                    row = (await session.execute(
                        select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one()
            return row

    async def cfg_get(self, guild_id: int, attr: str, default=None):
        return getattr(await self.cfg(guild_id), attr, default)

    async def cfg_set(self, guild_id: int, attr: str, value) -> bool:
        if attr not in _WRITABLE_CFG_ATTRS:
            logger.error(f"cfg_set rejected non-whitelisted attribute '{attr}'")
            return False
        try:
            row = await self.cfg(guild_id)
            async with async_session_maker() as session:
                merged = await session.merge(row)
                setattr(merged, attr, value)
                await session.commit()
            return True
        except Exception as exc:
            logger.error(f"cfg_set({attr}) failed: {exc}")
            return False

    def effective(self, guild_id: int, attr: str, global_value=None):
        """Panic mode halves thresholds; safe mode disables responses."""
        return None  # thresholds go through threshold(); kept for API clarity

    async def threshold(self, guild_id: int, attr: str, global_value: int) -> int:
        val = await self.cfg_get(guild_id, attr, global_value)
        try:
            val = int(val or global_value)
        except (TypeError, ValueError):
            val = global_value
        if await self.cfg_get(guild_id, "sec_panic_mode", False):
            val = max(2, val // 2)
        return val

    async def safe_mode(self, guild_id: int) -> bool:
        return bool(await self.cfg_get(guild_id, "sec_safe_mode",
                                       settings.SEC_SAFE_MODE))

    async def panic_mode(self, guild_id: int) -> bool:
        return bool(await self.cfg_get(guild_id, "sec_panic_mode", False))

    async def is_trusted(self, member) -> bool:
        """Staff, bot admins, whitelisted users or temp-privileged members."""
        if member is None or member.bot:
            return member is not None and member.id == self.bot.user.id
        if isinstance(member, discord.Member):
            if member.guild and member.id == member.guild.owner_id:
                return True
            if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
                return True
        if is_staff(member):
            return True
        gid = member.guild.id if isinstance(member, discord.Member) else 0
        if gid:
            for perm in ("security", "admin"):
                if await sc.has_temp_priv(gid, member.id, perm):
                    return True
        return False

    async def suite_allowed(self, member, perm: str = "security") -> bool:
        """Command gate: staff OR a matching temporary privilege (#18)."""
        if not member or not member.guild:
            return False
        if is_staff(member):
            return True
        return await sc.has_temp_priv(member.guild.id, member.id, perm)

    def log_channel(self, guild: discord.Guild):
        cfg = self.bot.get_guild_config(guild.id)
        if cfg and cfg.guardian_log_channel_id:
            ch = guild.get_channel(int(cfg.guardian_log_channel_id))
            if ch:
                return ch
        return guild.system_channel

    # ── incident recording (#19 + #24) ──────────────────────────────

    async def record(self, guild, itype: str, severity: str, summary: str,
                     actor=None, target=None, details: dict = None,
                     evidence: list = None, response: str = None,
                     alert_channel: bool = True, dm_owners: bool = False) -> Optional[int]:
        actor_id = getattr(actor, "id", None)
        target_id = getattr(target, "id", None)
        inc_id = await sc.create_incident(
            guild.id if guild else 0, itype, severity, summary,
            actor_id=actor_id, actor_name=str(actor) if actor else None,
            target_id=target_id, target_name=str(target) if target else None,
            details=details or {}, evidence=evidence or [], response=response,
        )
        if inc_id and guild:
            inc_row = None
            rows = await sc.recent_incidents(guild.id, 1)
            if rows:
                inc_row = rows[0]
            if alert_channel:
                ch = self.log_channel(guild)
                if ch and inc_row:
                    try:
                        await ch.send(embed=_severity_embed(inc_row, guild))
                    except Exception:
                        pass
            if dm_owners:
                try:
                    await sc.alert_owners(
                        self.bot, guild,
                        f"{E('alert')} Security incident — {itype.replace('_', ' ')}",
                        f"**Server:** {guild.name}\n{summary}"
                        + (f"\n**Response:** {response}" if response else ""),
                        sc.SEVERITY_COLOR.get(severity, 0x3498DB))
                except Exception:
                    pass
            logger.info(f"incident #{inc_id} [{severity}] {itype} @ {guild.id if guild else 0}: {summary}")
        return inc_id

    # ── containment (#12) + anti-evasion (#23) ──────────────────────

    async def contain(self, member: discord.Member, minutes: int, reason: str,
                      itype: str) -> str:
        if member is None or member.bot or member.guild is None:
            return "skipped"
        if await self.safe_mode(member.guild.id):
            return "monitor-only (safe mode)"
        try:
            until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            await member.timeout(until, reason=f"Arqon Security: {reason}")
        except Exception as exc:
            logger.debug(f"contain timeout failed: {exc}")
            return "timeout failed (permissions)"
        sc.note_containment(member.guild.id, member.id, itype)
        strikes = await sc.add_strike(member.guild.id, member.id, str(member), reason)
        ban_at = max(2, settings.SEC_EVASION_BAN_AT)
        if strikes >= ban_at and not await self.panic_mode(member.guild.id):
            try:
                await member.ban(reason=f"Arqon anti-evasion: {strikes} strikes "
                                       f"(>= {ban_at}) — {reason}")
                await self.record(
                    member.guild, "auto_ban_evasion", "high",
                    f"Repeat offender **{member}** banned after {strikes} strikes.",
                    actor=member, response="banned (anti-evasion)")
                return "banned (anti-evasion)"
            except Exception:
                pass
        return f"timeout {minutes}m (strike {strikes})"

    # ── emergency lockdown (#11) ────────────────────────────────────

    async def emergency_lockdown(self, guild: discord.Guild, minutes: int,
                                 reason: str, actor=None) -> int:
        """Freeze all text/voice channels; returns number of channels locked.

        The lockdown slot is claimed BEFORE any permission edits: two concurrent
        lockdowns must never both snapshot overwrites, or the second snapshot
        would capture the *locked* state and a later unlock would restore
        'locked' as the 'original' permissions.
        """
        if guild.id in self.lockdowns:
            return len(self.lockdowns[guild.id]["overwrites"])
        # claim-first (no await between check and claim)
        self.lockdowns[guild.id] = {"until": time.time() + max(1, minutes) * 60,
                                    "overwrites": {}}
        saved: Dict[int, Optional[discord.PermissionOverwrite]] = {}
        channels = list(guild.text_channels) + list(guild.voice_channels)
        for ch in channels:
            try:
                saved[ch.id] = ch.overwrites_for(guild.default_role) or None
                overwrite = discord.PermissionOverwrite.from_pair(
                    *saved[ch.id].pair()) if saved[ch.id] else discord.PermissionOverwrite()
                overwrite.send_messages = False
                overwrite.add_reactions = False
                overwrite.create_public_threads = False
                overwrite.connect = False
                await ch.set_permissions(guild.default_role, overwrite=overwrite,
                                         reason=f"Arqon lockdown: {reason}")
            except Exception:
                continue
        self.lockdowns[guild.id]["overwrites"] = saved
        await sc.chain_append("lockdown", {"guild": guild.id, "minutes": minutes,
                                           "reason": reason, "channels": len(saved),
                                           "by": getattr(actor, "id", None)},
                              guild_id=guild.id)
        await self.record(guild, "lockdown", "critical",
                          f"Emergency lockdown — {len(saved)} channels frozen "
                          f"for {minutes}m. Reason: {reason}",
                          actor=actor, response=f"lockdown {minutes}m", alert_channel=False)
        return len(saved)

    async def unlock_all(self, guild: discord.Guild, actor=None,
                         state: Optional[dict] = None) -> int:
        """Lift a lockdown and restore the saved overwrites.

        `state` lets the expiry task hand over the (already popped) state so
        channels actually get restored on auto-unlock.
        """
        if state is None:
            state = self.lockdowns.pop(guild.id, None)
        else:
            self.lockdowns.pop(guild.id, None)
        if not state:
            return 0
        restored = 0
        for cid, overwrite in state["overwrites"].items():
            ch = guild.get_channel(cid)
            if not ch:
                continue
            try:
                await ch.set_permissions(guild.default_role, overwrite=overwrite,
                                         reason="Arqon lockdown lifted")
                restored += 1
            except Exception:
                continue
        await self.record(guild, "unlock", "low",
                          f"Lockdown lifted — {restored} channels restored.",
                          actor=actor, response="unlocked", alert_channel=False)
        return restored

    @tasks.loop(seconds=60)
    async def lockdown_task(self):
        now = time.time()
        for gid in list(self.lockdowns.keys()):
            state = self.lockdowns.get(gid)
            if not state or state["until"] > now:
                continue
            self.lockdowns.pop(gid, None)   # pop ONCE here…
            guild = self.bot.get_guild(gid)
            if guild:
                # …and pass the state through, otherwise unlock_all found no
                # state and every channel stayed frozen forever (auto-unlock bug)
                await self.unlock_all(guild, state=state)

    # ── alt scoring (#8) + impersonation (#9) ───────────────────────

    def alt_score(self, member: discord.Member, in_raid: bool) -> (int, List[str]):
        score, reasons = 0, []
        age = (datetime.now(timezone.utc) - member.created_at).days
        if age < 3:
            score += 40
            reasons.append(f"account only {age}d old")
        elif age < 7:
            score += 25
            reasons.append(f"account {age}d old")
        elif age < 30:
            score += 10
        if member.avatar is None and member.display_avatar is member.default_avatar:
            score += 15
            reasons.append("default avatar")
        name = member.name or ""
        digits = sum(c.isdigit() for c in name)
        if len(name) >= 5 and digits / max(1, len(name)) >= 0.5:
            score += 15
            reasons.append("numeric username")
        if in_raid:
            score += 20
            reasons.append("joined during raid window")
        return score, reasons

    def staff_reference_names(self, guild: discord.Guild) -> List[str]:
        names = []
        if guild.owner:
            names.append(guild.owner.display_name)
        for uid in settings.effective_owner_ids:
            m = guild.get_member(uid)
            if m:
                names.append(m.display_name)
        from utils.permissions import all_bot_admins
        for uid in all_bot_admins():
            m = guild.get_member(uid)
            if m:
                names.append(m.display_name)
        names += ["arqon", "arqon host", "admin", "moderator", "staff", "host", "owner"]
        return list(dict.fromkeys(names))

    def impersonation_match(self, guild: discord.Guild, member: discord.Member,
                            display_name: str):
        """Returns (ratio, matched_name) if the name mimics protected identities."""
        if member.bot:
            return None
        protected = self.staff_reference_names(guild)
        norm = _norm(display_name)
        if not norm:
            return None
        best_r, best_n = 0.0, None
        for cand in protected:
            c = _norm(cand)
            if not c or c == norm:
                continue
            ratio = SequenceMatcher(None, norm, c).ratio()
            if ratio > best_r:
                best_r, best_n = ratio, cand
        if "arqon" in norm:
            best_r = max(best_r, 0.95)
            best_n = best_n or "Arqon brand"
        return (best_r, best_n) if best_n else None

    # ─────────────────────────── listeners ───────────────────────────
    # Guardian v2 performs enforcement (strip/kick/ban). The suite records
    # incidents + evidence, escalates to lockdown, and covers everything
    # Guardian does not.

    def _prune_trackers(self):
        """Trim every sliding-window tracker so long-running raids cannot leak
        memory (entries older than 1h are dropped)."""
        now = datetime.now(timezone.utc)
        simple = (self.join_tracker, self.bot_add_tracker, self.webhook_tracker)
        for tracker in simple:
            for key in list(tracker.keys()):
                tracker[key] = [t for t in tracker[key]
                                if (now - t).total_seconds() < 3600]
                if not tracker[key]:
                    tracker.pop(key, None)
        for tracker in (self.abuse_tracker, self.nuke_tracker):
            for key in list(tracker.keys()):
                tracker[key] = [t for t in tracker[key]
                                if (now - t).total_seconds() < 3600]
                if not tracker[key]:
                    tracker.pop(key, None)

    async def _audit_actor(self, guild, action, target_id, evidence_fn=None):
        """Resolve the audit-log actor for an event + collect evidence.

        Also feeds the #2 admin-abuse detector: every audited destructive
        action counts against its actor, staff or not.
        """
        actor, evidence = None, []
        try:
            async for entry in guild.audit_logs(action=action, limit=6):
                if entry.target and getattr(entry.target, "id", None) == target_id:
                    actor = entry.user
                    if evidence_fn:
                        evidence.append(evidence_fn(entry))
                    evidence.append({"audit_id": entry.id, "actor": str(entry.user)})
                    break
        except Exception:
            pass
        if actor is not None and action in ABUSE_AUDIT_ACTIONS and not actor.bot:
            self.abuse_tracker[(guild.id, actor.id)].append(datetime.now(timezone.utc))
        return actor, evidence

    async def _nuke_escalation(self, guild, actor, action: str, count: int,
                               evidence: list, target=None):
        """#1/#11 — record, alert and (optionally) lock down."""
        response = None
        if await self.cfg_get(guild.id, "sec_raid_lockdown", True) and \
                not await self.safe_mode(guild.id):
            minutes = await self.cfg_get(guild.id, "sec_lockdown_minutes", 15)
            locked = await self.emergency_lockdown(guild, minutes,
                                                   f"anti-nuke: {action}", actor)
            response = f"lockdown {minutes}m ({locked} channels)"
        await self.record(
            guild, action, "critical",
            f"{count}x {action.replace('mass_', '').replace('_', ' ')} actions by "
            f"{actor or 'unknown actor'} within 10s.",
            actor=actor, target=target, evidence=evidence, response=response,
            dm_owners=True)
        if actor and not await self.is_trusted(actor):
            await self.contain(actor, 60, f"anti-nuke: {action}", action)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if not settings.SEC_ENABLED:
            return
        await asyncio.sleep(1)
        actor, evidence = await self._audit_actor(
            guild, discord.AuditLogAction.ban, user.id,
            lambda e: {"action": "ban", "reason": e.reason})
        if actor and await self.is_trusted(actor):
            return
        now = datetime.now(timezone.utc)
        self.nuke_tracker[(guild.id, "ban")].append(now)
        recent = len([t for t in self.nuke_tracker[(guild.id, "ban")]
                      if (now - t).total_seconds() <= 10])
        if recent >= await self.threshold(guild.id, "sec_nuke_threshold",
                                          settings.ANTI_NUKE_THRESHOLD):
            self.nuke_tracker[(guild.id, "ban")].clear()
            await self._nuke_escalation(guild, actor, "mass_ban", recent,
                                        evidence, target=user)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not settings.SEC_ENABLED:
            return
        await asyncio.sleep(1)
        actor, evidence = await self._audit_actor(
            member.guild, discord.AuditLogAction.kick, member.id,
            lambda e: {"action": "kick", "reason": e.reason})
        if actor is None:
            return  # voluntary leave — no actor in audit log
        if await self.is_trusted(actor):
            return
        now = datetime.now(timezone.utc)
        self.nuke_tracker[(member.guild.id, "kick")].append(now)
        recent = len([t for t in self.nuke_tracker[(member.guild.id, "kick")]
                      if (now - t).total_seconds() <= 10])
        if recent >= await self.threshold(member.guild.id, "sec_nuke_threshold",
                                          settings.ANTI_NUKE_THRESHOLD):
            self.nuke_tracker[(member.guild.id, "kick")].clear()
            await self._nuke_escalation(member.guild, actor, "mass_kick", recent,
                                        evidence, target=member)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not settings.SEC_ENABLED:
            return
        guild = channel.guild
        await asyncio.sleep(1)
        actor, evidence = await self._audit_actor(
            guild, discord.AuditLogAction.channel_delete, channel.id,
            lambda e: {"action": "channel_delete", "channel": channel.name})
        now = datetime.now(timezone.utc)
        self.nuke_tracker[(guild.id, "channel_delete")].append(now)
        recent = len([t for t in self.nuke_tracker[(guild.id, "channel_delete")]
                      if (now - t).total_seconds() <= 10])
        if recent >= await self.threshold(guild.id, "sec_nuke_threshold",
                                          settings.ANTI_NUKE_THRESHOLD):
            self.nuke_tracker[(guild.id, "channel_delete")].clear()
            await self._nuke_escalation(guild, actor, "mass_channel_delete",
                                        recent, evidence, target=channel)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        """#1 nuke intel + #5 hierarchy guard + #13 smart recovery."""
        if not settings.SEC_ENABLED:
            return
        guild = role.guild
        if role.is_default() or role.managed:
            return
        await asyncio.sleep(1)
        actor, evidence = await self._audit_actor(
            guild, discord.AuditLogAction.role_delete, role.id,
            lambda e: {"action": "role_delete", "role": role.name})
        now = datetime.now(timezone.utc)
        self.nuke_tracker[(guild.id, "role_delete")].append(now)
        recent = len([t for t in self.nuke_tracker[(guild.id, "role_delete")]
                      if (now - t).total_seconds() <= 10])
        if recent >= await self.threshold(guild.id, "sec_nuke_threshold",
                                          settings.ANTI_NUKE_THRESHOLD):
            self.nuke_tracker[(guild.id, "role_delete")].clear()
            await self._nuke_escalation(guild, actor, "mass_role_delete",
                                        recent, evidence, target=role)
            return
        # single guarded-role delete by an unauthorised actor -> smart recovery
        guarded = role.permissions.administrator or role.permissions.manage_guild
        if not guarded or not await self.cfg_get(guild.id, "sec_role_guard", True):
            return
        if actor and await self.is_trusted(actor):
            return
        recreated = await self._recreate_role(guild, role)
        response = ("role recreated (smart recovery)" if recreated
                    else "recreate failed (missing manage_roles)")
        await self.record(
            guild, "role_guard_delete", "high",
            f"Guarded role **{role.name}** was deleted by "
            f"{actor or 'an unknown actor'} — auto-recreated.",
            actor=actor, target=role, evidence=evidence, response=response,
            dm_owners=True)

    async def _recreate_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        if await self.safe_mode(guild.id):
            return False
        try:
            await guild.create_role(
                name=role.name, permissions=role.permissions, colour=role.colour,
                hoist=role.hoist, mentionable=role.mentionable,
                reason="Arqon smart recovery: recreate deleted role")
            return True
        except Exception as exc:
            logger.debug(f"role recreate failed: {exc}")
            return False

    # ── join pipeline: #23 evasion, #7 bot raid, #6 raid, #8 alt, #9 name ──

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not settings.SEC_ENABLED or not member.guild:
            return
        guild = member.guild

        # ── #7 anti-bot raid: bots added by unauthorised users ──────────
        if member.bot:
            await asyncio.sleep(2)
            actor, evidence = await self._audit_actor(
                guild, discord.AuditLogAction.bot_add, member.id,
                lambda e: {"action": "bot_add", "bot": str(member)})
            now = datetime.now(timezone.utc)
            self.bot_add_tracker[guild.id].append(now)
            recent = len([t for t in self.bot_add_tracker[guild.id]
                          if (now - t).total_seconds() <= 60])
            if recent >= await self.threshold(guild.id, "sec_bot_raid_threshold", 3):
                self.bot_add_tracker[guild.id].clear()
                await self.record(
                    guild, "bot_raid", "critical",
                    f"{recent} bots were added within 60s — possible bot raid.",
                    actor=actor, target=member, evidence=evidence,
                    response="alert", dm_owners=True)
            elif actor and not await self.is_trusted(actor):
                await self.record(
                    guild, "unauthorised_bot", "medium",
                    f"Bot **{member}** added by non-staff user {actor}.",
                    actor=actor, target=member, evidence=evidence,
                    response="Guardian kicks the bot")
            return

        now = datetime.now(timezone.utc)

        # ── #23 anti-evasion: repeat offenders are banned on arrival ────
        strikes = await sc.get_strikes(guild.id, member.id)
        ban_at = max(2, settings.SEC_EVASION_BAN_AT)
        if len(strikes) >= ban_at and not await self.safe_mode(guild.id):
            try:
                await member.ban(reason=f"Arqon anti-evasion: "
                                        f"{len(strikes)} prior strikes")
                await self.record(
                    guild, "evasion_blocked", "high",
                    f"**{member}** re-joined with {len(strikes)} strikes — auto-banned.",
                    actor=member, response="banned (anti-evasion)")
            except Exception:
                pass
            return

        # ── #6 adaptive anti-raid ───────────────────────────────────────
        self.join_tracker[guild.id].append(now)
        in_raid = self.raid_active_until[guild.id] > time.time()
        base = settings.ANTI_RAID_THRESHOLD
        try:
            base = int(await self.cfg_get(guild.id, "anti_raid_threshold", base) or base)
        except (TypeError, ValueError):
            pass
        if time.time() - self.raid_history[guild.id] < 86400:
            base = max(3, int(base * 0.6))          # adaptive: 40% stricter for 24h
        joins = len([t for t in self.join_tracker[guild.id]
                     if (now - t).total_seconds() <= settings.ANTI_RAID_WINDOW])
        if not in_raid and joins >= base and \
                await self.cfg_get(guild.id, "anti_raid_enabled", True):
            self.raid_active_until[guild.id] = time.time() + 600
            self.raid_history[guild.id] = time.time()
            response = "raid mode: new joins screened"
            if await self.cfg_get(guild.id, "sec_raid_lockdown", True) and \
                    not await self.safe_mode(guild.id):
                minutes = await self.cfg_get(guild.id, "sec_lockdown_minutes", 15)
                locked = await self.emergency_lockdown(guild, minutes,
                                                       "raid detected")
                response = f"raid mode + lockdown {minutes}m ({locked} channels)"
            await self.record(
                guild, "raid_detected", "critical",
                f"{joins} members joined within {settings.ANTI_RAID_WINDOW}s "
                f"(threshold {base}).",
                response=response, dm_owners=True)

        # ── #8 anti-alt detection ───────────────────────────────────────
        if await self.cfg_get(guild.id, "sec_anti_alt", True):
            score, reasons = self.alt_score(member, in_raid)
            if await sc.check_indicator(sc.user_hash(member.id), "user_hash"):
                score += 100
                reasons.append("matches shared threat hash")
            if score >= int(await self.cfg_get(guild.id, "sec_alt_min_risk", 60) or 60):
                response = await self.contain(
                    member, 30, "suspected alt: " + ", ".join(reasons[:3]),
                    "alt_suspect")
                await self.record(
                    guild, "alt_suspect", "medium",
                    f"**{member}** flagged as suspected alt (risk {score}/100): "
                    + "; ".join(reasons),
                    actor=member, response=response)

        # ── #9 anti-impersonation (on join) ────────────────────────────
        if await self.cfg_get(guild.id, "sec_anti_impersonation", True):
            match = self.impersonation_match(guild, member, member.display_name)
            if match and match[0] * 100 >= int(
                    await self.cfg_get(guild.id, "sec_imp_similarity", 85) or 85):
                response = await self.contain(
                    member, 30, f"impersonates '{match[1]}'", "impersonation")
                await self.record(
                    guild, "impersonation", "high",
                    f"**{member}** joined with a name mimicking '{match[1]}' "
                    f"({match[0]:.0%} match).",
                    actor=member, response=response)

    # ── message pipeline: #21 sync, #16 honeypot, #4 webhooks, #10 scam ──

    def _extract_domains(self, text: str) -> List[str]:
        out = []
        for host in URL_RE.findall(text or ""):
            host = host.lower().strip(".")
            if host not in out:
                out.append(host)
        return out

    async def scan_scam(self, message: discord.Message) -> Optional[str]:
        """Return a scam label if the message matches known intelligence."""
        content = message.content or ""
        low = content.lower()
        for pattern, label in SCAM_PATTERNS:
            if re.search(pattern, low):
                return label
        for host in self._extract_domains(content):
            if host in BAD_DOMAINS or await sc.check_indicator(host, "domain"):
                await sc.bump_indicator(host, "domain")
                return f"known scam domain: {host}"
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not settings.SEC_ENABLED or message.guild is None:
            return
        guild = message.guild
        content = message.content or ""

        # ── #21 cross-server threat-share sync channel ──────────────────
        share_id = await self.cfg_get(guild.id, "threat_share_channel_id", None)
        if share_id and message.channel.id == int(share_id) and \
                content.startswith("ARQON-THREAT-PACK"):
            result = await sc.import_threat_pack(
                content.split("\n", 1)[-1], source="sync", guild_id=guild.id)
            try:
                await message.add_reaction("✅" if result["added"] or result["total"]
                                           else "⚠️")
            except Exception:
                pass
            return

        # ── #4 anti-webhook attack: webhook messages pinging / scamming ─
        if message.webhook_id:
            label = await self.scan_scam(message)
            if label or message.mention_everyone:
                try:
                    await message.delete()
                except Exception:
                    pass
                await self.record(
                    guild, "webhook_attack", "high",
                    f"Webhook message attack blocked ({label or 'mass ping'}).",
                    target=f"webhook:{message.webhook_id}",
                    evidence=[{"channel": str(message.channel),
                               "content": content[:500],
                               "link": message.jump_url}],
                    response="deleted")
            return

        author = message.author
        if author.bot or not isinstance(author, discord.Member):
            return

        # ── #16 honeypot: posting in a decoy = instant ban ─────────────
        hp = await sc.get_honeypot(guild.id, message.channel.id) \
            if await self.cfg_get(guild.id, "sec_honeypot", True) else None
        if hp:
            await sc.honeypot_hit(hp.id)
            evidence = [{"channel": str(message.channel),
                         "content": content[:500], "link": message.jump_url}]
            response = "monitor-only (safe mode)"
            if not await self.safe_mode(guild.id):
                try:
                    await message.delete()
                    await author.ban(reason="Arqon honeypot: posted in decoy channel")
                    sc.note_containment(guild.id, author.id, "honeypot")
                    response = "banned + message deleted"
                except Exception:
                    response = "ban failed (permissions)"
            await self.record(
                guild, "honeypot", "high",
                f"**{author}** posted in honeypot channel {message.channel.mention}.",
                actor=author, evidence=evidence, response=response,
                dm_owners=True)
            return

        if await self.is_trusted(author):
            return

        # ── #10 anti-scam intelligence ──────────────────────────────────
        if await self.cfg_get(guild.id, "sec_anti_scam", True):
            label = await self.scan_scam(message)
            if label:
                links = self._extract_domains(content)
                for host in links[:3]:
                    await sc.add_indicator(host, "domain", source="local",
                                           guild_id=guild.id)
                evidence = [{"link": message.jump_url,
                             "content": content[:500],
                             "domains": links[:10]}]
                response = "monitor-only (safe mode)"
                if await self.cfg_get(guild.id, "sec_scam_autodelete", True) and \
                        not await self.safe_mode(guild.id):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    response = await self.contain(author, 60, f"scam: {label}",
                                                  "scam")
                    response = f"deleted + {response}"
                await self.record(
                    guild, "scam", "high",
                    f"**{author}** posted scam content ({label}).",
                    actor=author, evidence=evidence, response=response)

    # ── member update: #3 escalation blocker + #9 nick shield ───────

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not settings.SEC_ENABLED or after.bot or not after.guild:
            return
        guild = after.guild

        if before.display_name != after.display_name and \
                await self.cfg_get(guild.id, "sec_anti_impersonation", True):
            match = self.impersonation_match(guild, after, after.display_name)
            if match and match[0] * 100 >= int(
                    await self.cfg_get(guild.id, "sec_imp_similarity", 85) or 85):
                response = await self.contain(
                    after, 30, f"nickname impersonates '{match[1]}'", "impersonation")
                if after.nick and not await self.safe_mode(guild.id):
                    try:
                        await after.edit(nick=None,
                                         reason="Arqon: impersonation shield")
                        response += " + nickname reset"
                    except Exception:
                        pass
                await self.record(
                    guild, "impersonation", "medium",
                    f"**{after}** changed their name to mimic '{match[1]}' "
                    f"({match[0]:.0%} match).",
                    actor=after, response=response)

        added = [r for r in after.roles if r not in before.roles]
        if not added:
            return
        danger = [r for r in added
                  if any(getattr(r.permissions, p, False)
                         for p in DANGEROUS_ROLE_PERMS)]
        if not danger or not await self.cfg_get(guild.id, "sec_escalation_block", True):
            return
        actor, evidence = await self._audit_actor(
            guild, discord.AuditLogAction.member_role_update, after.id,
            lambda e: {"action": "role_grant", "roles": [r.name for r in danger]})
        if actor and await self.is_trusted(actor):
            return
        response = "monitor-only (safe mode)"
        if not await self.safe_mode(guild.id):
            try:
                await after.remove_roles(*danger,
                                         reason="Arqon: permission escalation blocked")
                response = f"reverted {len(danger)} dangerous role grant(s)"
            except Exception as exc:
                response = f"revert failed: {exc}"
            if actor and not await self.is_trusted(actor):
                response += "; " + await self.contain(
                    actor, 60, "permission escalation", "permission_escalation")
        await self.record(
            guild, "permission_escalation", "critical",
            f"{actor or 'Unknown actor'} granted dangerous permissions to "
            f"**{after}** ({', '.join(r.name for r in danger)}).",
            actor=actor, target=after, evidence=evidence, response=response,
            dm_owners=True)

    # ── role update/create: #5 hierarchy guard ─────────────────────

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if not settings.SEC_ENABLED or after.is_default() or after.managed:
            return
        if before.permissions == after.permissions:
            return
        guild = after.guild
        if not await self.cfg_get(guild.id, "sec_role_guard", True):
            return
        gained_danger = (
            (not before.permissions.administrator) and after.permissions.administrator) or (
            (not before.permissions.manage_guild) and after.permissions.manage_guild)
        guarded = before.permissions.administrator or before.permissions.manage_guild \
            or gained_danger
        if not guarded:
            return
        actor, evidence = await self._audit_actor(
            guild, discord.AuditLogAction.role_update, after.id,
            lambda e: {"action": "role_edit", "role": after.name,
                       "changes": str(e.changes)})
        if actor and await self.is_trusted(actor):
            return
        response = "monitor-only (safe mode)"
        if not await self.safe_mode(guild.id):
            try:
                await after.edit(permissions=before.permissions,
                                 reason="Arqon role hierarchy guard: revert")
                response = "permission change reverted"
            except Exception as exc:
                response = f"revert failed: {exc}"
            if actor and not await self.is_trusted(actor):
                response += "; " + await self.contain(
                    actor, 60, "guarded role edited", "role_guard")
        await self.record(
            guild, "role_guard_edit", "high",
            f"Permissions of guarded role **{after.name}** were modified by "
            f"{actor or 'an unknown actor'}.",
            actor=actor, target=after, evidence=evidence, response=response,
            dm_owners=gained_danger)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not settings.SEC_ENABLED or not role.permissions.administrator:
            return
        if not await self.cfg_get(role.guild.id, "sec_escalation_block", True):
            return
        await asyncio.sleep(1)
        actor, evidence = await self._audit_actor(
            role.guild, discord.AuditLogAction.role_create, role.id,
            lambda e: {"action": "role_create", "role": role.name})
        if actor and await self.is_trusted(actor):
            return
        response = "monitor-only (safe mode)"
        if not await self.safe_mode(role.guild.id):
            try:
                await role.delete(reason="Arqon: unauthorised admin role removed")
                response = "unauthorised admin role deleted"
            except Exception as exc:
                response = f"delete failed: {exc}"
            if actor and not await self.is_trusted(actor):
                response += "; " + await self.contain(
                    actor, 60, "created an admin role", "permission_escalation")
        await self.record(
            role.guild, "admin_role_created", "high",
            f"A role with **administrator** permissions was created by "
            f"{actor or 'an unknown actor'}.",
            actor=actor, target=role, evidence=evidence, response=response,
            dm_owners=True)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        """#4 anti-webhook attack — creation bursts by unauthorised users."""
        if not settings.SEC_ENABLED:
            return
        await asyncio.sleep(1.5)
        guild = channel.guild
        actor, evidence = None, []
        try:
            async for entry in guild.audit_logs(limit=5):
                if entry.action not in (discord.AuditLogAction.webhook_create,
                                        discord.AuditLogAction.webhook_delete):
                    continue
                if entry.action == discord.AuditLogAction.webhook_create:
                    actor, evidence = entry.user, [
                        {"action": "webhook_create", "channel": str(channel),
                         "webhook": str(entry.target)}]
                break
        except Exception:
            pass
        if actor is None or actor.bot:
            return
        now = datetime.now(timezone.utc)
        self.webhook_tracker[guild.id].append(now)
        recent = len([t for t in self.webhook_tracker[guild.id]
                      if (now - t).total_seconds() <= 30])
        if recent < await self.threshold(guild.id, "sec_bot_raid_threshold", 3):
            return
        self.webhook_tracker[guild.id].clear()
        if await self.is_trusted(actor):
            return
        response = "monitor-only (safe mode)"
        if not await self.safe_mode(guild.id):
            response = await self.contain(actor, 60, "webhook creation burst",
                                          "webhook_attack")
        await self.record(
            guild, "webhook_attack", "high",
            f"{recent} webhooks created within 30s by {actor} — possible "
            f"webhook spam attack.",
            actor=actor, evidence=evidence, response=response, dm_owners=True)

    # ── background tasks: #14 drift, #2 abuse, temp-priv expiry ────

    @tasks.loop(minutes=5)
    async def drift_task(self):
        if not settings.SEC_ENABLED:
            return
        for guild in list(self.bot.guilds):
            try:
                if not await self.cfg_get(guild.id, "sec_perm_drift", True):
                    continue
                baseline = await sc.get_baseline(guild.id)
                if not baseline:
                    continue
                current = {m.id for m in guild.members
                           if m.guild_permissions.administrator
                           or m.guild_permissions.manage_guild}
                current -= {guild.owner_id, self.bot.user.id}
                known = set(baseline.get("admin_ids", [])) | {guild.owner_id}
                extras = current - known
                if extras:
                    names = ", ".join(str(guild.get_member(i) or f"`{i}`")
                                      for i in list(extras)[:6])
                    await self.record(
                        guild, "permission_drift", "medium",
                        f"{len(extras)} member(s) hold admin powers outside the "
                        f"approved baseline: {names}. Compare with `/sec-snapshot show` "
                        f"and restore via `/sec-restore`.",
                        response="alert")
            except Exception as exc:
                logger.debug(f"drift check failed for {guild.id}: {exc}")

    @tasks.loop(seconds=120)
    async def priv_task(self):
        """Purge expired temp privileges (#18) + admin abuse scan (#2)."""
        try:
            await sc.purge_expired_privs()
        except Exception:
            pass
        self._prune_trackers()
        now = datetime.now(timezone.utc)
        for key in list(self.abuse_tracker.keys()):
            guild_id, actor_id = key
            recent = [t for t in self.abuse_tracker[key]
                      if (now - t).total_seconds() <= settings.SEC_ADMIN_ABUSE_WINDOW]
            if not recent:
                self.abuse_tracker.pop(key, None)
                continue
            self.abuse_tracker[key] = recent
            limit = await self.threshold(guild_id, "sec_abuse_threshold",
                                         settings.SEC_ADMIN_ABUSE_THRESHOLD)
            if len(recent) < limit:
                continue
            self.abuse_tracker.pop(key, None)
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue
            actor = guild.get_member(actor_id)
            response = None
            if await self.panic_mode(guild_id) and not await self.safe_mode(guild_id) \
                    and actor:
                response = await self.contain(actor, 30, "admin abuse (panic mode)",
                                              "admin_abuse")
            severity = "critical" if await self.panic_mode(guild_id) else "high"
            await self.record(
                guild, "admin_abuse", severity,
                f"{actor or f'`{actor_id}`'} performed {len(recent)} destructive "
                f"actions within {settings.SEC_ADMIN_ABUSE_WINDOW}s.",
                actor=actor, response=response, dm_owners=True)

    # ═══════════════════════════ commands ═══════════════════════════

    async def _deny(self, ctx) -> None:
        await ctx.send(embed=error_embed(
            "Permission Denied", "You need staff permissions for this command."),
            ephemeral=True)

    DASH_GROUPS = [
        ("Anti-Nuke & Abuse", [
            ("Smart Anti-Nuke", "anti_nuke_enabled", True),
            ("Admin Abuse Detector", "sec_admin_abuse", True),
            ("Escalation Blocker", "sec_escalation_block", True),
            ("Role Hierarchy Guard", "sec_role_guard", True),
            ("Anti-Webhook", "anti_webhook_enabled", True),
        ]),
        ("Raid & Members", [
            ("Adaptive Anti-Raid", "anti_raid_enabled", True),
            ("Anti-Bot Raid", "anti_bot_enabled", True),
            ("Alt Detection", "sec_anti_alt", True),
            ("Anti-Impersonation", "sec_anti_impersonation", True),
            ("Honeypots", "sec_honeypot", True),
        ]),
        ("Response & Intel", [
            ("Scam Intelligence", "sec_anti_scam", True),
            ("Auto-Containment", "sec_auto_containment", True),
            ("Auto-Lockdown", "sec_raid_lockdown", True),
            ("Permission Drift", "sec_perm_drift", True),
            ("Two-Person Rule", "sec_two_person", True),
        ]),
    ]

    async def _dashboard_embed(self, guild: discord.Guild) -> discord.Embed:
        cfg = await self.cfg(guild.id)
        rows = await sc.recent_incidents(guild.id, 5)
        chain = await sc.verify_log_chain()
        stats = await sc.indicator_stats()

        desc = []
        for group, items in self.DASH_GROUPS:
            line = "  ".join(
                f"{'🟢' if getattr(cfg, attr, True) else '🔴'} {name}"
                for name, attr, _ in items)
            desc.append(f"**{group}**\n{line}")
        desc.append(
            f"**System**\n"
            f"{'🟢' if chain['ok'] else '🔴'} Hash-chain ({chain['entries']} entries)  "
            f"{'🟣' if await self.panic_mode(guild.id) else '⚪'} Panic mode  "
            f"{'🟠' if await self.safe_mode(guild.id) else '⚪'} Safe mode\n"
            f"{'🟢' if guild.id in self.lockdowns else '⚪'} Lockdown  "
            f"{E('chart')} Incidents (last 5): **{len(rows)}**  "
            f"{E('shield')} Indicators: **{sum(stats.values())}**")
        embed = base_embed(
            f"{E('shield')} Security Suite v3 — Control Center",
            "\n\n".join(desc), 0x8E44AD,
            footer=f"{settings.BOT_NAME} • 25 protections • /help security")
        if rows:
            top = "\n".join(
                f"`#{r.id}` **{r.itype}** — {r.severity}" for r in rows[:5])
            embed.add_field(name="Latest incidents",
                            value=top[:1000] + f"\n→ `/incidents`", inline=False)
        return embed

    @commands.hybrid_command(name="security",
                             description="Security Suite control center (staff)")
    @commands.guild_only()
    async def security(self, ctx: commands.Context):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        view = SecurityDashboardView(self, ctx.author)
        await ctx.send(embed=await self._dashboard_embed(ctx.guild),
                       view=view, ephemeral=True)

    @commands.hybrid_command(name="incidents",
                             description="Recent security incidents (staff)")
    @commands.guild_only()
    async def incidents(self, ctx: commands.Context, limit: int = 8):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        rows = await sc.recent_incidents(ctx.guild.id, max(1, min(limit, 15)))
        if not rows:
            return await ctx.send(embed=info_embed(
                f"{E('check')} Clean Record",
                "No security incidents recorded for this server."), ephemeral=True)
        embed = base_embed(f"{E('chart')} Incident Timeline — {ctx.guild.name}",
                           None, 0x8E44AD,
                           footer=f"{settings.BOT_NAME} • /evidence <id> for details")
        for r in rows:
            stamp = int(r.created_at.replace(tzinfo=timezone.utc).timestamp())
            embed.add_field(
                name=f"`#{r.id}` {sc.SEVERITY_LABEL.get(r.severity, r.severity)} "
                     f"• {r.itype.replace('_', ' ')}",
                value=f"{r.summary[:150]}\n<t:{stamp}:R>"
                      + (f" → {r.response}" if r.response else ""),
                inline=False)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="evidence",
                             description="Show collected evidence for an incident")
    @commands.guild_only()
    async def evidence(self, ctx: commands.Context, incident_id: int):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        rows = await sc.recent_incidents(ctx.guild.id, 100)
        inc = next((r for r in rows if r.id == incident_id), None)
        if not inc:
            return await ctx.send(embed=error_embed(
                "Not Found", f"No incident `#{incident_id}` in the last 100 records."),
                ephemeral=True)
        ev = inc.evidence or []
        desc = "\n".join(
            f"**{i + 1}.** `{json.dumps(item, default=str)[:220]}`"
            for i, item in enumerate(ev[:8])) or "No evidence bundles attached."
        embed = base_embed(f"{E('search')} Evidence — Incident #{inc.id}",
                           f"{inc.summary}\n\n{desc[:3800]}", 0x3498DB)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="verifylogs",
                             description="Verify the tamper-evident log chain (staff)")
    async def verifylogs(self, ctx: commands.Context):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        report = await sc.verify_log_chain()
        if report["ok"]:
            embed = success_embed(
                f"{E('check')} Log Chain Verified",
                f"All **{report['entries']}** entries are intact.\n"
                f"Any tampering with past records would break the sha256 chain — "
                f"none detected.")
        else:
            embed = error_embed(
                f"{E('alert')} CHAIN COMPROMISED",
                f"Integrity failure at entry **#{report['broken_at']}** of "
                f"{report['entries']}.\nRecords may have been altered — investigate immediately.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="security-test",
                             description="Run a full security self-test (staff)")
    @commands.guild_only()
    async def security_test(self, ctx: commands.Context):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        msg = await ctx.send(embed=loading_embed("Running security self-test…"))
        checks, warnings = [], []
        me = ctx.guild.me
        for perm in ("view_audit_log", "ban_members", "kick_members",
                     "moderate_members", "manage_roles", "manage_channels",
                     "manage_webhooks", "manage_messages"):
            ok = getattr(me.guild_permissions, perm, False)
            (checks if ok else warnings if perm == "moderate_members" else checks
             ).append((f"bot permission `{perm}`", ok))
        chain = await sc.verify_log_chain()
        checks.append(("hash-chain integrity", chain["ok"]))
        iid = await sc.create_incident(ctx.guild.id, "self_test", "low",
                                       "Security self-test probe",
                                       response="auto-resolved (test)")
        checks.append(("database writable", iid is not None))
        baseline = await sc.get_baseline(ctx.guild.id)
        warnings.append(("baseline snapshot saved (/sec-snapshot)", baseline is not None))
        hps = await sc.list_honeypots(ctx.guild.id)
        warnings.append(("honeypot channels deployed (/honeypot)", bool(hps)))
        stats = await sc.indicator_stats()
        warnings.append(("threat indicators loaded (/threatshare)",
                         sum(stats.values()) > 0))
        share_id = await self.cfg_get(ctx.guild.id, "threat_share_channel_id", None)
        warnings.append(("threat sync channel linked", share_id is not None))
        cfg = await self.cfg(ctx.guild.id)
        enabled = sum(1 for _g, items in self.DASH_GROUPS
                      for _n, attr, _d in items if getattr(cfg, attr, True))
        total_toggles = sum(len(items) for _g, items in self.DASH_GROUPS)
        checks.append((f"protections enabled ({enabled}/{total_toggles})",
                       enabled >= total_toggles - 3))
        log_ch = self.log_channel(ctx.guild)
        checks.append(("security log channel reachable",
                       log_ch is not None and
                       log_ch.permissions_for(me).send_messages))
        pending = len(await sc.pending_approvals(ctx.guild.id))
        warnings.append(("no pending approvals waiting", pending == 0))

        hard_pass = sum(1 for _n, ok in checks if ok)
        score = int(hard_pass / max(1, len(checks)) * 100)
        desc = "\n".join(
            f"{'✅' if ok else '❌'} {name}" for name, ok in checks)
        if warnings:
            desc += "\n" + "\n".join(
                f"{'✅' if ok else '⚠️'} {name}" for name, ok in warnings)
        color = 0x2ECC71 if score >= 90 else 0xF39C12 if score >= 60 else 0xE74C3C
        embed = base_embed(f"{E('gear')} Security Self-Test — {score}/100",
                           desc[:3900], color)
        embed.add_field(name="Verdict",
                        value=("Hardened — excellent coverage." if score >= 90 else
                               "Needs attention — fix the ❌ items." if score >= 60 else
                               "Critical gaps — the bot may be unable to defend this server."),
                        inline=False)
        await msg.edit(embed=embed)

    @commands.hybrid_command(name="panic",
                             description="Emergency maximum-defence mode (admin)")
    @commands.guild_only()
    async def panic(self, ctx: commands.Context, mode: str):
        if not await self.suite_allowed(ctx.author, "security") or \
                not is_admin(ctx.author):
            return await self._deny(ctx)
        mode = mode.lower().strip()
        if mode in ("on", "enable", "true", "1"):
            await self.cfg_set(ctx.guild.id, "sec_panic_mode", True)
            minutes = await self.cfg_get(ctx.guild.id, "sec_lockdown_minutes", 15)
            locked = await self.emergency_lockdown(ctx.guild, minutes,
                                                   "PANIC MODE", ctx.author)
            await self.record(
                ctx.guild, "panic_on", "critical",
                f"**PANIC MODE** activated by {ctx.author} — thresholds halved, "
                f"{locked} channels locked, abuse containment armed.",
                actor=ctx.author, response=f"lockdown {minutes}m", dm_owners=True)
            await ctx.send(embed=warning_embed(
                f"{E('alert')} PANIC MODE ACTIVE",
                f"Thresholds **halved**, {locked} channels locked, admin-abuse "
                f"containment armed.\nRestore with `/panic off`."), ephemeral=True)
        elif mode in ("off", "disable", "false", "0"):
            await self.cfg_set(ctx.guild.id, "sec_panic_mode", False)
            restored = await self.unlock_all(ctx.guild, ctx.author)
            await self.record(ctx.guild, "panic_off", "low",
                              f"Panic mode deactivated by {ctx.author}; "
                              f"{restored} channels unlocked.",
                              actor=ctx.author, response="unlocked")
            await ctx.send(embed=success_embed(
                "Panic Mode Off", f"{restored} channel(s) restored to normal."),
                ephemeral=True)
        else:
            await ctx.send(embed=info_embed("Usage", "`/panic on` or `/panic off`"),
                           ephemeral=True)

    @commands.hybrid_command(name="safe-mode",
                             description="Monitor-only mode: detect but never punish (admin)")
    @commands.guild_only()
    async def safe_mode_cmd(self, ctx: commands.Context, mode: str):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        enabled = mode.lower().strip() in ("on", "enable", "true", "1")
        await self.cfg_set(ctx.guild.id, "sec_safe_mode", enabled)
        await sc.chain_append("safe_mode", {"guild": ctx.guild.id,
                                            "enabled": enabled,
                                            "by": ctx.author.id},
                              guild_id=ctx.guild.id)
        await ctx.send(embed=(
            warning_embed("🟠 Safe Mode ON",
                          "All protections keep monitoring and recording, but no "
                          "automated punishments will be applied.")
            if enabled else
            success_embed("🟢 Safe Mode OFF",
                          "Automatic containment and enforcement are active again.")),
            ephemeral=True)

    @commands.hybrid_command(name="emergency",
                             description="Freeze every channel immediately (admin)")
    @commands.guild_only()
    async def emergency(self, ctx: commands.Context, minutes: int = 0):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        if minutes <= 0:
            minutes = int(await self.cfg_get(ctx.guild.id, "sec_lockdown_minutes", 15)
                          or 15)
        locked = await self.emergency_lockdown(ctx.guild, minutes,
                                               f"manual by {ctx.author}", ctx.author)
        await ctx.send(embed=warning_embed(
            f"{E('lock')} EMERGENCY LOCKDOWN",
            f"**{locked}** channels frozen for **{minutes} minutes**.\n"
            f"Lift early with `/unlockall`."), ephemeral=True)

    @commands.hybrid_command(name="unlockall",
                             description="Lift the emergency lockdown (admin)")
    @commands.guild_only()
    async def unlockall(self, ctx: commands.Context):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        restored = await self.unlock_all(ctx.guild, ctx.author)
        await ctx.send(embed=success_embed(
            f"{E('unlock')} Lockdown Lifted",
            f"{restored} channel(s) restored." if restored
            else "No active lockdown found."), ephemeral=True)

    @commands.hybrid_command(name="sec-snapshot",
                             description="Save a recovery baseline of roles & admins (admin)")
    @commands.guild_only()
    async def sec_snapshot(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        snapshot = await self.build_snapshot(ctx.guild)
        await sc.save_baseline(ctx.guild.id, snapshot)
        embed = success_embed(
            f"{E('vault')} Recovery Baseline Saved",
            f"**{len(snapshot['roles'])}** roles and **{len(snapshot['admin_ids'])}** "
            f"admin members snapshotted.\n"
            f"The drift watcher (#14) now alerts when admin powers change, and "
            f"`/sec-restore` can rebuild guarded roles.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="sec-restore",
                             description="Restore the saved baseline (two-person rule)")
    @commands.guild_only()
    async def sec_restore(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        baseline = await sc.get_baseline(ctx.guild.id)
        if not baseline:
            return await ctx.send(embed=error_embed(
                "No Baseline", "Run `/sec-snapshot` first."), ephemeral=True)
        if not await self.cfg_get(ctx.guild.id, "sec_two_person", True) or \
                is_owner_member(ctx.author):
            result = await self.execute_restore(ctx.guild, baseline)
            await ctx.send(embed=success_embed("Baseline Restored", result),
                           ephemeral=True)
            return
        approval_id = await sc.create_approval(
            ctx.guild.id, "restore_baseline",
            f"Restore security baseline (requested by {ctx.author})",
            {}, ctx.author.id)
        embed = warning_embed(
            f"{E('vault')} Restore Approval #{approval_id}",
            f"{ctx.author.mention} requested a baseline restore.\n"
            f"A **second** staff member must approve this action.")
        await ctx.send(embed=embed, view=ApprovalView(self, approval_id,
                                                      ctx.author.id))

    async def build_snapshot(self, guild: discord.Guild) -> dict:
        roles = []
        for r in guild.roles:
            if r.is_default() or r.managed:
                continue
            roles.append({
                "id": r.id, "name": r.name, "color": r.colour.value,
                "hoist": r.hoist, "mentionable": r.mentionable,
                "perms": [p for p, v in r.permissions if v],
            })
        admin_ids = [m.id for m in guild.members
                     if (m.guild_permissions.administrator or
                         m.guild_permissions.manage_guild)
                     and m.id != guild.owner_id and m.id != self.bot.user.id]
        return {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "roles": roles, "admin_ids": admin_ids}

    async def execute_restore(self, guild: discord.Guild, snapshot: dict) -> str:
        if await self.safe_mode(guild.id):
            return "Skipped — safe mode is ON (monitor-only)."
        baseline_ids = set(snapshot.get("admin_ids", []))
        baseline_role_ids = {r["id"] for r in snapshot.get("roles", [])
                             if "administrator" in r["perms"]
                             or "manage_guild" in r["perms"]}
        demoted = recreated = 0
        for m in guild.members:
            if m.bot or m.id == guild.owner_id:
                continue
            if (m.guild_permissions.administrator or m.guild_permissions.manage_guild) \
                    and m.id not in baseline_ids:
                drop = [r for r in m.roles
                        if (r.permissions.administrator or r.permissions.manage_guild)
                        and not r.managed and r.id not in baseline_role_ids]
                if drop:
                    try:
                        await m.remove_roles(*drop, reason="Arqon baseline restore")
                        demoted += 1
                    except Exception:
                        pass
        for r in snapshot.get("roles", []):
            if "administrator" not in r["perms"] and "manage_guild" not in r["perms"]:
                continue
            if guild.get_role(r["id"]):
                continue
            perms = discord.Permissions.none()
            for p in r["perms"]:
                try:
                    setattr(perms, p, True)
                except Exception:
                    continue
            try:
                await guild.create_role(name=r["name"], permissions=perms,
                                        reason="Arqon baseline restore")
                recreated += 1
            except Exception:
                pass
        result = (f"{demoted} member(s) demoted to baseline, "
                  f"{recreated} guarded role(s) recreated.")
        await sc.chain_append("baseline_restored",
                              {"guild": guild.id, "result": result},
                              guild_id=guild.id)
        return result

    @commands.hybrid_command(name="honeypot",
                             description="Turn a channel into a honeypot decoy (admin)")
    @commands.guild_only()
    async def honeypot(self, ctx: commands.Context,
                       channel: discord.TextChannel = None):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        target = channel or ctx.channel
        if await sc.register_honeypot(ctx.guild.id, target.id, ctx.author.id):
            await ctx.send(embed=warning_embed(
                f"{E('ghost')} Honeypot Armed — {target.mention}",
                "Anyone who posts in this channel is instantly flagged and banned.\n"
                "Keep it hidden — never post in it yourself."), ephemeral=True)
        else:
            await ctx.send(embed=info_embed("Already Armed",
                                            f"{target.mention} is already a honeypot."),
                           ephemeral=True)

    @commands.hybrid_command(name="honeypots",
                             description="List honeypot channels (staff)")
    @commands.guild_only()
    async def honeypots(self, ctx: commands.Context):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        rows = await sc.list_honeypots(ctx.guild.id)
        if not rows:
            return await ctx.send(embed=info_embed(
                "No Honeypots", f"Arm one with `/honeypot #{ctx.channel.name}`."),
                ephemeral=True)
        lines = [f"<#{r.channel_id}> — {r.hits} hit(s)" for r in rows]
        await ctx.send(embed=info_embed(f"{E('ghost')} Honeypots "
                                        f"({len(rows)})", "\n".join(lines)),
                       ephemeral=True)

    @commands.hybrid_command(name="threatshare",
                             description="Cross-server threat intelligence (admin)")
    @commands.guild_only()
    async def threatshare(self, ctx: commands.Context, action: str,
                          channel: discord.TextChannel = None):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        action = action.lower().strip()

        if action == "export":
            import io
            pack = await sc.export_threat_pack(self.bot.user.id or 0)
            if not pack:
                return await ctx.send(embed=error_embed(
                    "Export Failed", "Could not build the indicator pack."),
                    ephemeral=True)
            fname = f"arqon-threat-pack-{int(time.time())}.json"
            await ctx.send(
                embed=info_embed(f"{E('globe')} Threat Pack Exported",
                                 f"{json.loads(pack)['count']} privacy-preserving "
                                 f"indicators (user IDs are hashed)."),
                file=discord.File(io.BytesIO(pack.encode()), filename=fname),
                ephemeral=True)
            share_id = await self.cfg_get(ctx.guild.id, "threat_share_channel_id", None)
            if share_id:
                ch = ctx.guild.get_channel(int(share_id))
                if ch:
                    try:
                        await ch.send(f"ARQON-THREAT-PACK\n{pack}")
                    except Exception:
                        pass
        elif action == "import":
            att = next((a for a in ctx.message.attachments
                        if a.filename.endswith(".json")), None)
            if not att:
                return await ctx.send(embed=info_embed(
                    "Import", "Attach an `arqon-threat-pack-*.json` file to this "
                    "command."), ephemeral=True)
            pack = (await att.read()).decode("utf-8", "replace")
            if await self.cfg_get(ctx.guild.id, "sec_two_person", True) and \
                    not is_owner_member(ctx.author):
                approval_id = await sc.create_approval(
                    ctx.guild.id, "threat_import",
                    f"Import threat pack from {ctx.author}",
                    {"pack": pack[:100000]}, ctx.author.id)
                await ctx.send(embed=warning_embed(
                    f"{E('globe')} Import Approval #{approval_id}",
                    "A second staff member must approve this import."),
                    view=ApprovalView(self, approval_id, ctx.author.id),
                    ephemeral=True)
            else:
                result = await sc.import_threat_pack(pack, source="shared",
                                                     guild_id=ctx.guild.id)
                await ctx.send(embed=success_embed(
                    "Pack Imported",
                    f"Added **{result['added']}** of {result['total']} indicators."),
                    ephemeral=True)
        elif action == "channel":
            if not channel:
                return await ctx.send(embed=info_embed(
                    "Usage", "`/threatshare channel <#channel>` — packs posted "
                    "there auto-sync into this server."), ephemeral=True)
            await self.cfg_set(ctx.guild.id, "threat_share_channel_id", channel.id)
            await ctx.send(embed=success_embed(
                f"{E('link')} Sync Channel Linked",
                f"{channel.mention} will now auto-import `ARQON-THREAT-PACK` "
                f"messages."), ephemeral=True)
        else:
            stats = await sc.indicator_stats()
            pretty = ", ".join(f"{k}: **{v}**" for k, v in sorted(stats.items())) \
                or "none yet"
            share_id = await self.cfg_get(ctx.guild.id, "threat_share_channel_id", None)
            await ctx.send(embed=info_embed(
                f"{E('globe')} Threat Sharing Status",
                f"Indicators — {pretty}\n"
                f"Sync channel: {('<#' + str(share_id) + '>') if share_id else 'not set'}\n"
                f"Commands: `/threatshare export` • `import` • `channel` • `status`"),
                ephemeral=True)

    @commands.hybrid_command(name="strikes",
                             description="Show a member's abuse strikes (staff)")
    @commands.guild_only()
    async def strikes(self, ctx: commands.Context, member: discord.Member):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        rows = await sc.get_strikes(ctx.guild.id, member.id)
        if not rows:
            return await ctx.send(embed=success_embed(
                f"{E('check')} Clean", f"**{member}** has no strikes."),
                ephemeral=True)
        lines = [f"`{r.created_at:%Y-%m-%d}` {r.reason or 'no reason'}"
                 for r in rows[:10]]
        ban_at = max(2, settings.SEC_EVASION_BAN_AT)
        await ctx.send(embed=warning_embed(
            f"{E('warn')} Strikes — {member} ({len(rows)})",
            "\n".join(lines) + f"\n\nAuto-ban at **{ban_at}** strikes (anti-evasion)."),
            ephemeral=True)

    @commands.hybrid_command(name="strikes-clear",
                             description="Clear a member's strikes (admin)")
    @commands.guild_only()
    async def strikes_clear(self, ctx: commands.Context, member: discord.Member):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        removed = await sc.clear_strikes(ctx.guild.id, member.id)
        await ctx.send(embed=success_embed(
            "Strikes Cleared", f"Removed **{removed}** strike(s) from {member}."),
            ephemeral=True)

    @commands.hybrid_command(name="granttemp",
                             description="Grant a permission that expires (admin)")
    @commands.guild_only()
    async def granttemp(self, ctx: commands.Context, member: discord.Member,
                        perm: str, duration: str, *, reason: str = ""):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        perm = perm.lower().strip()
        if perm not in sc.TEMP_PERMS:
            return await ctx.send(embed=error_embed(
                "Unknown Permission",
                f"Valid: {', '.join(f'`{p}`' for p in sc.TEMP_PERMS)}"), ephemeral=True)
        minutes = _parse_minutes(duration)
        if not minutes:
            return await ctx.send(embed=error_embed(
                "Invalid Duration", "Use `30m`, `2h`, `3d` … (max 30 days)."),
                ephemeral=True)
        minutes = min(minutes, 43200)
        await sc.grant_temp_priv(ctx.guild.id, member.id, perm, minutes,
                                 ctx.author.id, reason)
        expires = int((datetime.now(timezone.utc) +
                       timedelta(minutes=minutes)).timestamp())
        await self.record(
            ctx.guild, "temp_priv", "low",
            f"{ctx.author} granted temporary `{perm}` to {member} for {minutes}m.",
            actor=ctx.author, target=member, response="granted")
        await ctx.send(embed=success_embed(
            f"{E('clock')} Temporary Privilege Granted",
            f"{member.mention} → `{perm}` until <t:{expires}:f>\n"
            f"They can now use `{perm}`-gated security commands. It expires "
            f"automatically — no cleanup needed."), ephemeral=True)

    @commands.hybrid_command(name="temp-privs",
                             description="List active temporary privileges (staff)")
    @commands.guild_only()
    async def temp_privs(self, ctx: commands.Context, member: discord.Member = None):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        rows = await sc.active_temp_privs(ctx.guild.id,
                                          member.id if member else None)
        if not rows:
            return await ctx.send(embed=info_embed(
                "No Active Grants", "Nothing to expire."), ephemeral=True)
        lines = []
        for r in rows[:15]:
            exp = int(r.expires_at.replace(tzinfo=timezone.utc).timestamp())
            lines.append(f"<@{r.user_id}> — `{r.perm}` → <t:{exp}:R>")
        await ctx.send(embed=info_embed(f"{E('clock')} Temporary Privileges "
                                        f"({len(rows)})", "\n".join(lines)),
                       ephemeral=True)

    @commands.hybrid_command(name="revoketemp",
                             description="Revoke a temporary privilege (admin)")
    @commands.guild_only()
    async def revoketemp(self, ctx: commands.Context, priv_id: int):
        if not is_admin(ctx.author):
            return await self._deny(ctx)
        ok = await sc.revoke_temp_priv(priv_id, ctx.guild.id)
        await ctx.send(embed=(success_embed("Revoked", f"Privilege `#{priv_id}` removed.")
                              if ok else error_embed("Not Found",
                                                     f"No privilege `#{priv_id}` here.")),
                       ephemeral=True)

    @commands.hybrid_command(name="approvals",
                             description="Pending two-person approvals (staff)")
    @commands.guild_only()
    async def approvals(self, ctx: commands.Context):
        if not await self.suite_allowed(ctx.author, "security"):
            return await self._deny(ctx)
        rows = await sc.pending_approvals(ctx.guild.id)
        if not rows:
            return await ctx.send(embed=info_embed(
                f"{E('check')} Nothing Pending",
                "No approvals are waiting for a second pair of eyes."),
                ephemeral=True)
        for row in rows[:5]:
            embed = warning_embed(
                f"{E('vault')} Approval #{row.id} — {row.action.replace('_', ' ')}",
                f"{row.title}\nRequested by <@{row.requester_id}> — needs a "
                f"**different** staff member to approve.")
            await ctx.send(embed=embed,
                           view=ApprovalView(self, row.id, row.requester_id),
                           ephemeral=True)

    # ── approval executors ─────────────────────────────────────────

    async def run_approval(self, row) -> str:
        if row.action == "restore_baseline":
            baseline = await sc.get_baseline(row.guild_id)
            if not baseline:
                return "No baseline found — run /sec-snapshot first."
            guild = self.bot.get_guild(row.guild_id)
            return await self.execute_restore(guild, baseline) if guild else \
                "Guild not reachable."
        if row.action == "threat_import":
            result = await sc.import_threat_pack(row.payload.get("pack", ""),
                                                 source="shared",
                                                 guild_id=row.guild_id)
            return f"Imported {result['added']} of {result['total']} indicators."
        if row.action == "unlock_all":
            guild = self.bot.get_guild(row.guild_id)
            restored = await self.unlock_all(guild) if guild else 0
            return f"{restored} channel(s) unlocked."
        return f"Unknown action `{row.action}`."


class SecurityDashboardView(discord.ui.View):
    """Toggle board for the suite protections (admin-only)."""

    def __init__(self, cog: "SecuritySuiteCog", requester: discord.Member):
        super().__init__(timeout=240)
        self.cog = cog
        self.requester = requester
        row, per_row = 0, 0
        for group, items in cog.DASH_GROUPS:
            for name, attr, _default in items:
                btn = discord.ui.Button(
                    label=name, style=discord.ButtonStyle.secondary, row=row)
                btn.callback = self._make_toggle(attr, name)
                self.add_item(btn)
                per_row += 1
                if per_row == 5:
                    row += 1
                    per_row = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or \
                not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Admin Only",
                                  "Toggling protections requires admin."),
                ephemeral=True)
            return False
        return True

    def _make_toggle(self, attr: str, name: str):
        async def cb(interaction: discord.Interaction):
            current = await self.cog.cfg_get(interaction.guild_id, attr, True)
            await self.cog.cfg_set(interaction.guild_id, attr, not current)
            await interaction.response.edit_message(
                embed=await self.cog._dashboard_embed(interaction.guild),
                view=self)
        return cb


class ApprovalView(discord.ui.View):
    """Two-person approval buttons (#17)."""

    def __init__(self, cog: "SecuritySuiteCog", approval_id: int, requester_id: int):
        super().__init__(timeout=3600)
        self.cog = cog
        self.approval_id = approval_id
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or \
                not is_staff(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Staff Only",
                                  "Only staff can decide approvals."), ephemeral=True)
            return False
        if interaction.user.id == self.requester_id:
            await interaction.response.send_message(
                embed=warning_embed("Two-Person Rule",
                                    "You requested this action — a **different** "
                                    "staff member must approve it."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction,
                      button: discord.ui.Button):
        row = await sc.decide_approval(self.approval_id, interaction.user.id,
                                       "approved")
        if not row:
            return await interaction.response.edit_message(
                embed=info_embed("Already Decided",
                                 "This approval was handled by someone else."),
                view=None)
        try:
            result = await self.cog.run_approval(row)
        except Exception as exc:
            result = f"Executor error: {exc}"
        await interaction.response.edit_message(
            embed=success_embed(f"✅ Approved by {interaction.user}",
                                f"{row.title}\n\n{result}"), view=None)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="✖")
    async def deny(self, interaction: discord.Interaction,
                   button: discord.ui.Button):
        row = await sc.decide_approval(self.approval_id, interaction.user.id,
                                       "denied")
        if not row:
            return await interaction.response.edit_message(
                embed=info_embed("Already Decided",
                                 "This approval was handled by someone else."),
                view=None)
        await interaction.response.edit_message(
            embed=error_embed(f"✖ Denied by {interaction.user}", row.title), view=None)


def is_owner_member(member: discord.Member) -> bool:
    from utils.permissions import is_owner
    return is_owner(member)


async def setup(bot):
    await bot.add_cog(SecuritySuiteCog(bot))
