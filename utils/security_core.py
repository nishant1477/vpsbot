"""Arqon Bot — Security Suite v3 core.

Shared primitives used by cogs/security_suite.py:

  • hash-chained tamper-evident logging        (#20)
  • incident timeline + evidence collection    (#19, #24)
  • two-person approval queue                  (#17)
  • temporary privileges                       (#18)
  • cross-server threat indicators             (#21)
  • repeat-offender strikes                    (#23)
  • honeypot registry                          (#16)
  • guild baselines (smart recovery / drift)   (#13, #14)
  • containment registry (de-dup vs Guardian)
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List

from config import settings
from utils.logger import get_logger

logger = get_logger("security_core")

GENESIS_HASH = "0" * 64

# Serialises chain appends — without this, two concurrent incidents could read
# the same tail hash and fork the chain (both entries claiming the same prev).
_chain_lock = asyncio.Lock()

# Cap for cross-server threat-pack imports (#21): a malicious pack must never
# be able to flood the indicators table with millions of rows.
MAX_THREAT_PACK_ITEMS = 500

# ───────────────────────── hash-chained logs (#20) ─────────────────────────


def _chain_hash(prev_hash: str, ts: str, kind: str, payload: str) -> str:
    blob = f"{prev_hash}|{ts}|{kind}|{payload}"
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


async def chain_append(kind: str, payload: dict, guild_id: Optional[int] = None) -> str:
    """Append an entry to the tamper-evident chain; returns the entry hash.

    Lock-protected: concurrent appends (e.g. a raid producing many incidents at
    once) queue instead of reading the same tail and forking the chain.
    """
    from database import async_session_maker, LogChainEntry
    from sqlalchemy import select

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)[:100000]
    try:
        async with _chain_lock:
            async with async_session_maker() as session:
                last = (await session.execute(
                    select(LogChainEntry).order_by(LogChainEntry.seq.desc()).limit(1)
                )).scalar_one_or_none()
                prev = last.entry_hash if last else GENESIS_HASH
                entry = LogChainEntry(
                    guild_id=guild_id, ts=ts, kind=kind, payload=payload_str,
                    prev_hash=prev, entry_hash=_chain_hash(prev, ts, kind, payload_str),
                )
                session.add(entry)
                await session.commit()
                return entry.entry_hash
    except Exception as exc:
        logger.error(f"chain_append failed: {exc}")
        return ""


async def verify_log_chain() -> Dict[str, Any]:
    """Walk the whole chain and re-compute every hash. Returns a report dict."""
    from database import async_session_maker, LogChainEntry
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(
            select(LogChainEntry).order_by(LogChainEntry.seq)
        )).scalars().all()

    prev = GENESIS_HASH
    for row in rows:
        expected = _chain_hash(prev, row.ts, row.kind, row.payload)
        if row.prev_hash != prev or row.entry_hash != expected:
            return {"ok": False, "entries": len(rows), "broken_at": row.seq}
        prev = row.entry_hash
    return {"ok": True, "entries": len(rows), "broken_at": None}


# ───────────────────── incidents + evidence (#19, #24) ─────────────────────

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
SEVERITY_COLOR = {"low": 0x3498DB, "medium": 0xF39C12, "high": 0xE67E22, "critical": 0xE74C3C}
SEVERITY_LABEL = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "critical": "CRITICAL"}


async def create_incident(
    guild_id: int,
    itype: str,
    severity: str,
    summary: str,
    actor_id: Optional[int] = None,
    actor_name: Optional[str] = None,
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    details: Optional[dict] = None,
    evidence: Optional[list] = None,
    response: Optional[str] = None,
) -> Optional[int]:
    """Record an incident (timeline + evidence + chain). Returns incident id."""
    from database import async_session_maker, SecurityIncident

    try:
        async with async_session_maker() as session:
            inc = SecurityIncident(
                guild_id=guild_id, itype=itype, severity=severity, summary=summary[:490],
                actor_id=actor_id, actor_name=actor_name, target_id=target_id,
                target_name=target_name, details=details or {},
                evidence=evidence or [], response=response,
            )
            session.add(inc)
            await session.commit()
            inc_id = inc.id
    except Exception as exc:
        logger.error(f"create_incident failed: {exc}")
        return None

    await chain_append("incident", {
        "id": inc_id, "guild": guild_id, "type": itype, "severity": severity,
        "actor": actor_id, "summary": summary, "response": response,
    }, guild_id=guild_id)
    return inc_id


async def add_incident_evidence(incident_id: int, item: dict) -> bool:
    """Append an evidence bundle to an incident (#24)."""
    from database import async_session_maker, SecurityIncident
    from sqlalchemy import select

    try:
        async with async_session_maker() as session:
            inc = (await session.execute(
                select(SecurityIncident).where(SecurityIncident.id == incident_id)
            )).scalar_one_or_none()
            if not inc:
                return False
            ev = list(inc.evidence or [])
            item = dict(item)
            item.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="seconds"))
            ev.append(item)
            inc.evidence = ev[:20]  # cap bundle size
            await session.commit()
            return True
    except Exception as exc:
        logger.error(f"add_incident_evidence failed: {exc}")
        return False


async def recent_incidents(guild_id: int, limit: int = 10) -> list:
    from database import async_session_maker, SecurityIncident
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(
            select(SecurityIncident)
            .where(SecurityIncident.guild_id == guild_id)
            .order_by(SecurityIncident.id.desc()).limit(limit)
        )).scalars().all()
    return list(rows)


# ─────────────── containment registry (de-dup vs Guardian) ───────────────

_containment: Dict[int, Dict[int, float]] = {}
_CONTAINMENT_TTL = 3600  # prune entries older than 1h so the map cannot grow forever


def note_containment(guild_id: int, actor_id: int, action: str = "") -> None:
    """Remember that an actor was just contained (by Guardian or the suite)."""
    now = time.time()
    bucket = _containment.setdefault(int(guild_id), {})
    bucket[int(actor_id)] = now
    # opportunistic prune of stale entries (keeps memory bounded under raids)
    for gid in list(_containment.keys()):
        g_bucket = _containment[gid]
        if len(g_bucket) > 512:
            for uid in [u for u, t in g_bucket.items() if now - t > _CONTAINMENT_TTL]:
                g_bucket.pop(uid, None)
        if not g_bucket and gid != int(guild_id):
            _containment.pop(gid, None)


def was_recently_contained(guild_id: int, actor_id: int, window: int = 120) -> bool:
    """True if this actor was contained within the last `window` seconds."""
    bucket = _containment.get(int(guild_id), {})
    ts = bucket.get(int(actor_id))
    if ts is None:
        return False
    if time.time() - ts > max(60, window):
        bucket.pop(int(actor_id), None)
        return False
    return True


# ─────────────────── two-person approvals (#17) ───────────────────

async def create_approval(
    guild_id: int, action: str, title: str, payload: dict, requester_id: int,
) -> Optional[int]:
    from database import async_session_maker, PendingApproval

    try:
        async with async_session_maker() as session:
            row = PendingApproval(
                guild_id=guild_id, action=action, title=title[:190],
                payload=payload or {}, requester_id=requester_id,
            )
            session.add(row)
            await session.commit()
            await chain_append("approval_created", {
                "id": row.id, "guild": guild_id, "action": action,
                "requester": requester_id, "title": title,
            }, guild_id=guild_id)
            return row.id
    except Exception as exc:
        logger.error(f"create_approval failed: {exc}")
        return None


async def get_approval(approval_id: int):
    from database import async_session_maker, PendingApproval
    from sqlalchemy import select

    async with async_session_maker() as session:
        return (await session.execute(
            select(PendingApproval).where(PendingApproval.id == approval_id)
        )).scalar_one_or_none()


async def decide_approval(approval_id: int, approver_id: int,
                          status: str, note: str = "") -> Optional[Any]:
    """Set an approval's status; returns the row so executors can act on it.

    Two-person rule is enforced HERE (not just in the UI): the approver must
    be a different user than the requester, and each approval decides once.
    """
    from database import async_session_maker, PendingApproval
    from sqlalchemy import select

    async with async_session_maker() as session:
        row = (await session.execute(
            select(PendingApproval).where(PendingApproval.id == approval_id)
        )).scalar_one_or_none()
        if not row or row.status != "pending":
            return None
        if approver_id and int(approver_id) == int(row.requester_id):
            return None  # two-person rule: self-approval is never valid
        row.status = status
        row.approver_id = approver_id
        row.decision_note = note[:290] or None
        row.decided_at = datetime.now(timezone.utc)
        await session.commit()
        await chain_append("approval_decided", {
            "id": row.id, "guild": row.guild_id, "action": row.action,
            "approver": approver_id, "status": status,
        }, guild_id=row.guild_id)
        return row


async def pending_approvals(guild_id: int, limit: int = 10) -> list:
    from database import async_session_maker, PendingApproval
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(
            select(PendingApproval)
            .where(PendingApproval.guild_id == guild_id, PendingApproval.status == "pending")
            .order_by(PendingApproval.id.desc()).limit(limit)
        )).scalars().all()
    return list(rows)


# ───────────────── temporary privileges (#18) ─────────────────

TEMP_PERMS = ("security", "moderation", "admin", "restore")


async def grant_temp_priv(guild_id: int, user_id: int, perm: str,
                          minutes: int, granted_by: int, reason: str = "") -> bool:
    from database import async_session_maker, TempPrivilege

    try:
        async with async_session_maker() as session:
            session.add(TempPrivilege(
                guild_id=guild_id, user_id=user_id, perm=perm,
                granted_by=granted_by, reason=reason[:190] or None,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
            ))
            await session.commit()
        await chain_append("temp_priv_granted", {
            "guild": guild_id, "user": user_id, "perm": perm,
            "minutes": minutes, "by": granted_by,
        }, guild_id=guild_id)
        return True
    except Exception as exc:
        logger.error(f"grant_temp_priv failed: {exc}")
        return False


async def has_temp_priv(guild_id: int, user_id: int, perm: str) -> bool:
    from database import async_session_maker, TempPrivilege
    from sqlalchemy import select

    try:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(TempPrivilege)
                .where(TempPrivilege.guild_id == guild_id,
                       TempPrivilege.user_id == user_id,
                       TempPrivilege.perm == perm,
                       TempPrivilege.expires_at > datetime.now(timezone.utc))
                .limit(1)
            )).scalar_one_or_none()
            return row is not None
    except Exception:
        return False


async def active_temp_privs(guild_id: int, user_id: Optional[int] = None) -> list:
    from database import async_session_maker, TempPrivilege
    from sqlalchemy import select

    q = select(TempPrivilege).where(
        TempPrivilege.guild_id == guild_id,
        TempPrivilege.expires_at > datetime.now(timezone.utc),
    )
    if user_id:
        q = q.where(TempPrivilege.user_id == user_id)
    async with async_session_maker() as session:
        rows = (await session.execute(
            q.order_by(TempPrivilege.expires_at))).scalars().all()
    return list(rows)


async def revoke_temp_priv(priv_id: int, guild_id: Optional[int] = None) -> bool:
    from database import async_session_maker, TempPrivilege
    from sqlalchemy import delete as sa_delete

    async with async_session_maker() as session:
        q = sa_delete(TempPrivilege).where(TempPrivilege.id == priv_id)
        if guild_id:
            q = q.where(TempPrivilege.guild_id == guild_id)
        await session.execute(q)
        await session.commit()
    return True


async def purge_expired_privs() -> int:
    """Delete expired rows; returns how many were removed."""
    from database import async_session_maker, TempPrivilege
    from sqlalchemy import delete as sa_delete

    async with async_session_maker() as session:
        result = await session.execute(
            sa_delete(TempPrivilege)
            .where(TempPrivilege.expires_at <= datetime.now(timezone.utc)))
        await session.commit()
        return result.rowcount or 0


# ─────────────── cross-server threat indicators (#21) ───────────────

def user_hash(user_id: int) -> str:
    """Privacy-preserving indicator: sha256(id)[:16] — never share raw IDs."""
    return hashlib.sha256(f"arqon:{user_id}".encode()).hexdigest()[:16]


async def add_indicator(value: str, itype: str, source: str = "local",
                        guild_id: Optional[int] = None) -> bool:
    from database import async_session_maker, ThreatIndicator
    from sqlalchemy import select

    value = (value or "").strip().lower()[:290]
    if not value or itype not in ("domain", "pattern", "user_hash"):
        return False
    try:
        async with async_session_maker() as session:
            exists = (await session.execute(
                select(ThreatIndicator)
                .where(ThreatIndicator.indicator == value, ThreatIndicator.itype == itype)
            )).scalar_one_or_none()
            if exists:
                return False
            session.add(ThreatIndicator(indicator=value, itype=itype,
                                        source=source, guild_id=guild_id))
            await session.commit()
            return True
    except Exception as exc:
        logger.error(f"add_indicator failed: {exc}")
        return False


async def check_indicator(value: str, itype: str) -> bool:
    from database import async_session_maker, ThreatIndicator
    from sqlalchemy import select

    value = (value or "").strip().lower()[:290]
    if not value:
        return False
    try:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(ThreatIndicator)
                .where(ThreatIndicator.indicator == value, ThreatIndicator.itype == itype)
            )).scalar_one_or_none()
            return row is not None
    except Exception:
        return False


async def bump_indicator(value: str, itype: str) -> None:
    from database import async_session_maker, ThreatIndicator
    from sqlalchemy import select

    value = (value or "").strip().lower()
    try:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(ThreatIndicator)
                .where(ThreatIndicator.indicator == value, ThreatIndicator.itype == itype)
            )).scalar_one_or_none()
            if row:
                row.hits = (row.hits or 0) + 1
                await session.commit()
    except Exception:
        pass


async def export_threat_pack(source_bot_id: int = 0) -> Optional[str]:
    """Build a privacy-preserving indicator pack (JSON string)."""
    from database import async_session_maker, ThreatIndicator, OffenderStrike
    from sqlalchemy import select, func

    try:
        async with async_session_maker() as session:
            inds = (await session.execute(select(ThreatIndicator))).scalars().all()
            offenders = (await session.execute(
                select(OffenderStrike.user_id, func.count(OffenderStrike.id).label("n"))
                .group_by(OffenderStrike.user_id)
                .having(func.count(OffenderStrike.id) >= 2).limit(200)
            )).all()
    except Exception as exc:
        logger.error(f"export_threat_pack failed: {exc}")
        return None

    indicators = [{"t": i.itype, "v": i.indicator} for i in inds]
    indicators += [{"t": "user_hash", "v": user_hash(r.user_id)} for r in offenders]
    pack = {
        "app": "arqon-threat-pack", "v": 1,
        "ts": int(time.time()), "src": user_hash(source_bot_id),
        "count": len(indicators), "indicators": indicators,
    }
    return json.dumps(pack, indent=2)


async def import_threat_pack(text: str, source: str = "shared",
                             guild_id: Optional[int] = None) -> Dict[str, int]:
    """Import an indicator pack; returns {added, total, bad, skipped}.

    Hardened: hard-caps the accepted item count (MAX_THREAT_PACK_ITEMS) and
    the pack size, so a hostile pack cannot flood the indicators table.
    """
    out = {"added": 0, "total": 0, "bad": 0, "skipped": 0}
    try:
        text = (text or "").strip()
        if len(text) > 400_000:  # ~400KB is far more than any legit pack needs
            out["bad"] = 1
            return out
        pack = json.loads(text)
        if pack.get("app") != "arqon-threat-pack":
            out["bad"] = 1
            return out
        items = pack.get("indicators", []) or []
        if not isinstance(items, list):
            out["bad"] = 1
            return out
        if len(items) > MAX_THREAT_PACK_ITEMS:
            out["skipped"] = len(items) - MAX_THREAT_PACK_ITEMS
            items = items[:MAX_THREAT_PACK_ITEMS]
        out["total"] = len(items)
        for item in items:
            if isinstance(item, dict) and await add_indicator(
                    str(item.get("v", "")), str(item.get("t", "domain")),
                    source=source, guild_id=guild_id):
                out["added"] += 1
        await chain_append("threat_pack_imported", {
            "guild": guild_id, "added": out["added"], "total": out["total"],
            "skipped": out["skipped"],
        }, guild_id=guild_id)
    except Exception:
        out["bad"] = 1
    return out


async def indicator_stats() -> Dict[str, int]:
    from database import async_session_maker, ThreatIndicator
    from sqlalchemy import select, func

    try:
        async with async_session_maker() as session:
            rows = (await session.execute(
                select(ThreatIndicator.itype, func.count(ThreatIndicator.id))
                .group_by(ThreatIndicator.itype))).all()
        return {r[0]: int(r[1]) for r in rows}
    except Exception:
        return {}


# ─────────────────── repeat offenders (#23) ───────────────────

async def add_strike(guild_id: int, user_id: int, user_name: str = "",
                     reason: str = "", issued_by: int = 0) -> int:
    """Record a strike; returns the member's total strike count."""
    from database import async_session_maker, OffenderStrike
    from sqlalchemy import select, func

    try:
        async with async_session_maker() as session:
            session.add(OffenderStrike(
                guild_id=guild_id, user_id=user_id, user_name=user_name[:110],
                reason=reason[:290] or None, issued_by=issued_by,
            ))
            await session.commit()
            total = (await session.execute(
                select(func.count(OffenderStrike.id))
                .where(OffenderStrike.guild_id == guild_id,
                       OffenderStrike.user_id == user_id))).scalar()
            return int(total or 0)
    except Exception as exc:
        logger.error(f"add_strike failed: {exc}")
        return 0


async def get_strikes(guild_id: int, user_id: int) -> list:
    from database import async_session_maker, OffenderStrike
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(
            select(OffenderStrike)
            .where(OffenderStrike.guild_id == guild_id, OffenderStrike.user_id == user_id)
            .order_by(OffenderStrike.id.desc()).limit(25))).scalars().all()
    return list(rows)


async def clear_strikes(guild_id: int, user_id: int) -> int:
    from database import async_session_maker, OffenderStrike
    from sqlalchemy import delete as sa_delete

    async with async_session_maker() as session:
        result = await session.execute(
            sa_delete(OffenderStrike)
            .where(OffenderStrike.guild_id == guild_id, OffenderStrike.user_id == user_id))
        await session.commit()
        return result.rowcount or 0


# ─────────────────── honeypot registry (#16) ───────────────────

async def register_honeypot(guild_id: int, channel_id: int, created_by: int) -> bool:
    from database import async_session_maker, HoneypotChannel
    from sqlalchemy import select

    try:
        async with async_session_maker() as session:
            exists = (await session.execute(
                select(HoneypotChannel)
                .where(HoneypotChannel.guild_id == guild_id,
                       HoneypotChannel.channel_id == channel_id))).scalar_one_or_none()
            if exists:
                return False
            session.add(HoneypotChannel(guild_id=guild_id, channel_id=channel_id,
                                        created_by=created_by))
            await session.commit()
            return True
    except Exception as exc:
        logger.error(f"register_honeypot failed: {exc}")
        return False


async def get_honeypot(guild_id: int, channel_id: int):
    from database import async_session_maker, HoneypotChannel
    from sqlalchemy import select

    async with async_session_maker() as session:
        return (await session.execute(
            select(HoneypotChannel)
            .where(HoneypotChannel.guild_id == guild_id,
                   HoneypotChannel.channel_id == channel_id))).scalar_one_or_none()


async def honeypot_hit(honeypot_id: int) -> None:
    from database import async_session_maker, HoneypotChannel
    from sqlalchemy import select

    try:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(HoneypotChannel).where(HoneypotChannel.id == honeypot_id)
            )).scalar_one_or_none()
            if row:
                row.hits = (row.hits or 0) + 1
                await session.commit()
    except Exception:
        pass


async def list_honeypots(guild_id: int) -> list:
    from database import async_session_maker, HoneypotChannel
    from sqlalchemy import select

    async with async_session_maker() as session:
        rows = (await session.execute(
            select(HoneypotChannel)
            .where(HoneypotChannel.guild_id == guild_id))).scalars().all()
    return list(rows)


async def remove_honeypot(guild_id: int, honeypot_id: int) -> bool:
    from database import async_session_maker, HoneypotChannel
    from sqlalchemy import delete as sa_delete

    async with async_session_maker() as session:
        result = await session.execute(
            sa_delete(HoneypotChannel)
            .where(HoneypotChannel.guild_id == guild_id, HoneypotChannel.id == honeypot_id))
        await session.commit()
        return (result.rowcount or 0) > 0


# ─────────────── guild baselines (#13 smart recovery / #14 drift) ───────────────

async def save_baseline(guild_id: int, snapshot: dict) -> bool:
    from database import async_session_maker, GuildBaseline
    from sqlalchemy import select

    try:
        async with async_session_maker() as session:
            row = (await session.execute(
                select(GuildBaseline).where(GuildBaseline.guild_id == guild_id)
            )).scalar_one_or_none()
            if not row:
                row = GuildBaseline(guild_id=guild_id)
                session.add(row)
            row.snapshot = snapshot
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
        await chain_append("baseline_saved", {
            "guild": guild_id,
            "roles": len(snapshot.get("roles", [])),
            "admins": len(snapshot.get("admin_ids", [])),
        }, guild_id=guild_id)
        return True
    except Exception as exc:
        logger.error(f"save_baseline failed: {exc}")
        return False


async def get_baseline(guild_id: int) -> Optional[dict]:
    from database import async_session_maker, GuildBaseline
    from sqlalchemy import select

    async with async_session_maker() as session:
        row = (await session.execute(
            select(GuildBaseline).where(GuildBaseline.guild_id == guild_id)
        )).scalar_one_or_none()
        return dict(row.snapshot or {}) if row else None


# ───────────────────── incident alert helper ─────────────────────

async def alert_owners(bot, guild, title: str, description: str, color: int) -> None:
    """DM every bot owner + the guild owner about a security event."""
    import discord
    from utils.embeds import base_embed

    if not settings.ANTI_NUKE_ALERT_OWNERS:
        return
    embed = base_embed(title, description, color)
    embed.set_footer(text=f"{settings.BOT_NAME} • Security Suite v3")
    targets = set(settings.effective_owner_ids)
    if guild and guild.owner_id:
        targets.add(guild.owner_id)
    for uid in targets:
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            if user:
                await user.send(embed=embed)
        except Exception:
            continue
