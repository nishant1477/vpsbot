#!/usr/bin/env python3
"""Arqon Security Suite v3 — validation suite.

Covers:
  1. compile of every touched file
  2. fresh-DB schema (8 new security tables)
  3. OLD-database migration (sec_* columns added additively)
  4. hash chain: append → verify → tamper → detect broken seq
  5. incident + evidence collection
  6. threat pack export/import round-trip (privacy-preserving)
  7. two-person approval lifecycle
  8. temporary privileges: grant / active / expired purge
  9. offender strikes + honeypot + baseline
 10. plan template editor path (update via setattr like the modal)
 11. cog imports + bot registration of the new extensions
"""
import asyncio
import os
import py_compile
import sys

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./database/test_sec_suite.db"
os.environ["SEC_ENABLED"] = "true"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

PASSED, FAILED = [], []


def check(name, fn):
    try:
        result = fn() if callable(fn) else fn
        ok = result if isinstance(result, bool) else True
        (PASSED if ok else FAILED).append(name)
        print(f"  {'✅' if ok else '❌'} {name}")
    except Exception as exc:
        FAILED.append(f"{name} ({exc})")
        print(f"  ❌ {name} — {exc}")


async def amain():
    print("━" * 60)
    print("Arqon Security Suite v3 — validation")
    print("━" * 60)

    # ── 1. compile ────────────────────────────────────────────────
    print("\n[1] compile checks")
    files = [
        "bot.py", "config.py", "database.py",
        "cogs/security_suite.py", "cogs/settings_ui.py",
        "cogs/plans.py", "cogs/guardian.py", "cogs/help.py",
        "utils/security_core.py",
    ]
    for f in files:
        check(f"compile {f}", lambda f=f: py_compile.compile(f, doraise=True) or True)

    # ── imports ───────────────────────────────────────────────────
    print("\n[2] imports")
    check("import database", lambda: __import__("database"))
    from sqlalchemy import select, text as sa_text
    import database as dbmod
    check("security models exported", lambda: all(
        hasattr(dbmod, n) for n in (
            "SecurityIncident", "LogChainEntry", "PendingApproval",
            "TempPrivilege", "ThreatIndicator", "HoneypotChannel",
            "OffenderStrike", "GuildBaseline")))

    # fresh DB
    if os.path.exists("database/test_sec_suite.db"):
        os.remove("database/test_sec_suite.db")
    await dbmod.init_db()
    check("fresh init_db", lambda: True)

    # ── 3. old-db migration ───────────────────────────────────────
    print("\n[3] old-database migration")
    OLD_DB = "database/test_sec_old.db"
    if os.path.exists(OLD_DB):
        os.remove(OLD_DB)
    import aiosqlite
    async with aiosqlite.connect(OLD_DB) as conn:
        await conn.execute(
            "CREATE TABLE guild_configs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " guild_id BIGINT UNIQUE NOT NULL, prefix VARCHAR(10) DEFAULT '!',"
            " guardian_enabled BOOLEAN DEFAULT 1)")
        await conn.commit()
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./database/test_sec_old.db"
    import importlib
    import config as config_mod
    importlib.reload(config_mod)
    importlib.reload(dbmod)
    await dbmod.init_db()
    async with aiosqlite.connect(OLD_DB) as conn:
        cursor = await conn.execute("PRAGMA table_info(guild_configs)")
        rows = await cursor.fetchall()
        cols = {r[1] for r in rows}
    expected = {"sec_safe_mode", "sec_panic_mode", "sec_admin_abuse",
                "sec_escalation_block", "sec_role_guard", "sec_anti_alt",
                "sec_alt_min_risk", "sec_anti_impersonation", "sec_imp_similarity",
                "sec_anti_scam", "sec_scam_autodelete", "sec_raid_lockdown",
                "sec_auto_containment", "sec_perm_drift", "sec_honeypot",
                "sec_two_person", "sec_temp_priv", "sec_threat_share",
                "sec_nuke_threshold", "sec_abuse_threshold",
                "sec_bot_raid_threshold", "sec_lockdown_minutes"}
    missing = expected - cols
    check(f"migration adds {len(expected)} sec_* columns", lambda: not missing)

    # back to the fresh test DB for the rest
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./database/test_sec_suite.db"
    importlib.reload(config_mod)
    importlib.reload(dbmod)
    await dbmod.init_db()

    from utils import security_core as sc
    GID = 123456789012345678

    # ── 4. hash chain ─────────────────────────────────────────────
    print("\n[4] tamper-evident hash chain (#20)")
    for i in range(3):
        await sc.chain_append("test", {"n": i}, guild_id=GID)
    report = await sc.verify_log_chain()
    check(f"chain verifies ({report['entries']} entries)", report["ok"])
    async with dbmod.async_session_maker() as session:
        row = (await session.execute(
            select(dbmod.LogChainEntry).order_by(dbmod.LogChainEntry.seq))).scalars().all()
        victim = row[1]
        victim.payload = '{"tampered": true}'
        await session.commit()
    report = await sc.verify_log_chain()
    check(f"tamper detected at seq {report['broken_at']}",
          (not report["ok"]) and report["broken_at"] is not None)
    # clean the tamper so later tests see a valid chain
    async with dbmod.async_session_maker() as session:
        await session.execute(sa_text("DELETE FROM log_chain"))
        await session.commit()
    await sc.chain_append("genesis_after_cleanup", {"reset": True})

    # ── 5. incidents + evidence ───────────────────────────────────
    print("\n[5] incident timeline + evidence (#19 #24)")
    iid = await sc.create_incident(
        GID, "nuke_test", "critical", "mass bans detected",
        actor_id=999, actor_name="BadActor#0001",
        details={"count": 5}, evidence=[{"audit": "entry-1"}],
        response="lockdown")
    check("incident created", iid is not None)
    await sc.add_incident_evidence(iid, {"message_link": "https://discord/…"})
    rows = await sc.recent_incidents(GID, 5)
    check("incident + 2 evidence bundles",
          rows and rows[0].id == iid and len(rows[0].evidence) == 2)

    # ── 6. threat pack ────────────────────────────────────────────
    print("\n[6] cross-server threat sharing (#21)")
    await sc.add_indicator("grabify.link", "domain", source="local", guild_id=GID)
    await sc.add_indicator("free-nitro", "pattern", source="local", guild_id=GID)
    await sc.add_strike(GID, 424242, "Repeater#1", "spam")
    await sc.add_strike(GID, 424242, "Repeater#1", "scam")
    pack = await sc.export_threat_pack(source_bot_id=1)
    check("pack built", pack and '"arqon-threat-pack"' in pack)
    check("privacy: raw offender id NOT in pack", "424242" not in pack)
    result = await sc.import_threat_pack(pack, source="shared", guild_id=GID)
    check(f"first import adds only the new user_hash (added {result['added']})",
          result["added"] <= 1 and result["total"] > 0)
    again2 = await sc.import_threat_pack(pack, source="shared", guild_id=GID)
    check("second import fully dedupes", again2["added"] == 0)
    check("user_hash indicator matches",
          await sc.check_indicator(sc.user_hash(424242), "user_hash"))

    # ── 7. approvals ──────────────────────────────────────────────
    print("\n[7] two-person approvals (#17)")
    aid = await sc.create_approval(GID, "restore_baseline", "restore test", {},
                                   requester_id=111)
    pend = await sc.pending_approvals(GID)
    check("approval pending", pend and pend[0].id == aid)
    row = await sc.decide_approval(aid, 111, "approved")
    check("same-user decision blocked (two-person rule)", row is None)
    row = await sc.decide_approval(aid, 222, "approved")
    check("second staff member approves", row is not None
          and row.status == "approved")
    row = await sc.decide_approval(aid, 333, "denied")
    check("double-decision blocked", row is None)

    # ── 8. temp privileges ────────────────────────────────────────
    print("\n[8] temporary privileges (#18)")
    await sc.grant_temp_priv(GID, 555, "security", 60, granted_by=111)
    check("active temp priv visible",
          await sc.has_temp_priv(GID, 555, "security"))
    check("wrong perm rejected",
          not await sc.has_temp_priv(GID, 555, "admin"))
    await sc.grant_temp_priv(GID, 666, "admin", 0, granted_by=111)  # expired
    purged = await sc.purge_expired_privs()
    check(f"expired priv purged ({purged})", purged >= 1)
    check("still-active priv survives purge",
          await sc.has_temp_priv(GID, 555, "security"))
    active = await sc.active_temp_privs(GID)
    check("active list has 1 row", len(active) == 1)
    await sc.revoke_temp_priv(active[0].id, GID)
    check("revoke works", not await sc.has_temp_priv(GID, 555, "security"))

    # ── 9. strikes / honeypot / baseline ──────────────────────────
    print("\n[9] strikes (#23), honeypot (#16), baseline (#13)")
    await sc.add_strike(GID, 777, "Evader#2", "nuke")
    total = len(await sc.get_strikes(GID, 777))
    check("strike rows readable", total >= 1)
    cleared = await sc.clear_strikes(GID, 777)
    check(f"strikes cleared ({cleared})", cleared >= 1)
    ok = await sc.register_honeypot(GID, 999888, created_by=111)
    check("honeypot registered", ok)
    again = await sc.register_honeypot(GID, 999888, created_by=111)
    check("duplicate honeypot rejected", again is False)
    await sc.honeypot_hit((await sc.get_honeypot(GID, 999888)).id)
    hps = await sc.list_honeypots(GID)
    check("honeypot hit counted", hps and hps[0].hits == 1)
    snap = {"admin_ids": [111], "roles": [
        {"id": 1, "name": "Staff", "perms": ["administrator"], "color": 0,
         "hoist": True, "mentionable": False}], "ts": "now"}
    check("baseline saved", await sc.save_baseline(GID, snap))
    back = await sc.get_baseline(GID)
    check("baseline round-trip", back and back["admin_ids"] == [111])

    # ── 10. plan template editor path ─────────────────────────────
    print("\n[10] plan template editing")
    async with dbmod.async_session_maker() as session:
        session.add(dbmod.Plan(name="EditTest", required_invites=3, ram=3072,
                               cpu=1, disk=12, price=0))
        await session.commit()
        plan = (await session.execute(
            select(dbmod.Plan).where(dbmod.Plan.name == "EditTest"))).scalar_one()
        plan.ram = 4096          # what PlanFieldModal does
        plan.required_invites = 5
        plan.is_active = False
        await session.commit()
        pid = plan.id
    async with dbmod.async_session_maker() as session:
        plan = (await session.execute(
            select(dbmod.Plan).where(dbmod.Plan.id == pid))).scalar_one()
        check("plan fields updated", plan.ram == 4096 and
              plan.required_invites == 5 and plan.is_active is False)
        await session.delete(plan)
        await session.commit()

    # ── 11. guild config defaults + cog imports ───────────────────
    print("\n[11] guild config defaults + cog registration")
    async with dbmod.async_session_maker() as session:
        cfg = dbmod.GuildConfig(guild_id=GID)
        session.add(cfg)
        await session.commit()
        cfg2 = (await session.execute(
            select(dbmod.GuildConfig).where(
                dbmod.GuildConfig.guild_id == GID))).scalar_one()
        defaults_ok = (cfg2.sec_admin_abuse is True and
                       cfg2.sec_escalation_block is True and
                       cfg2.sec_two_person is True and
                       cfg2.sec_safe_mode is False and
                       cfg2.sec_panic_mode is False and
                       cfg2.sec_nuke_threshold == 3 and
                       cfg2.sec_lockdown_minutes == 15 and
                       cfg2.threat_share_channel_id is None)
        await session.delete(cfg2)
        await session.commit()
    check("GuildConfig sec_* defaults correct", defaults_ok)

    import cogs.security_suite  # noqa: F401
    import cogs.settings_ui     # noqa: F401
    check("cogs import cleanly", True)
    src = open("bot.py").read()
    check("bot.py registers security_suite",
          '"cogs.security_suite"' in src)
    check("bot.py registers settings_ui", '"cogs.settings_ui"' in src)

    await dbmod.close_db()

    print("\n" + "━" * 60)
    print(f"RESULT: {len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for f in FAILED:
            print("  ❌", f)
        sys.exit(1)
    print("ALL GREEN ✅")


def main():
    asyncio.run(amain())


if __name__ == "__main__":
    main()
