"""Arqon Node Agent — firewall via iptables (with in-memory rule book).

Rules are scoped to the owning VPS IP whenever it can be resolved, so an
"allow 3306 for VPS A" rule never silently opens 3306 on the whole host.
"""
import asyncio
import logging
import shutil
from typing import Dict, Any, List, Optional

logger = logging.getLogger("node-agent.firewall")


class FirewallManager:
    def __init__(self):
        self.rules: Dict[str, List[Dict]] = {}
        self._iptables = shutil.which("iptables")

    async def _exec(self, *args: str) -> bool:
        if not self._iptables:
            return False
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "iptables", *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=8)
            return proc.returncode == 0
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return False
        except Exception as exc:
            logger.warning(f"iptables exec failed: {exc}")
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return False

    async def add_rule(self, uuid: str, port: int, protocol: str,
                       action: str, source: str = "0.0.0.0/0",
                       dest_ip: Optional[str] = None):
        if uuid not in self.rules:
            self.rules[uuid] = []

        rule = {"port": port, "protocol": protocol, "action": action,
                "source": source, "dest_ip": dest_ip}
        # avoid duplicates
        self.rules[uuid] = [r for r in self.rules[uuid]
                            if not (r["port"] == port and r["protocol"] == protocol
                                    and r["action"] == action and r.get("source") == source)]
        self.rules[uuid].append(rule)

        ok = await self._apply_rule(uuid, rule, insert=False)
        if self._iptables and not ok:
            logger.warning(f"iptables rejected rule {action} {port}/{protocol} for {uuid}")
        logger.info(f"Firewall rule {action} {port}/{protocol} for {uuid}"
                    + (f" scoped to {dest_ip}" if dest_ip else ""))

    async def _apply_rule(self, uuid: str, rule: Dict, insert: bool = False) -> bool:
        """Real iptables application. When the VPS has a resolvable IP the rule
        is scoped with -d <ip> (FORWARD path for bridged containers). Falls back
        to a host-wide INPUT rule only when no IP is known. Rules are still
        tracked in memory when iptables is unavailable."""
        if not self._iptables:
            return False

        port = rule["port"]
        protocol = rule["protocol"]
        action = rule["action"]
        source = rule.get("source", "0.0.0.0/0")
        dest_ip = rule.get("dest_ip")

        target = "ACCEPT" if action == "allow" else "DROP"
        flag = "-I" if insert else "-A"
        src_flag = ["-s", source] if source and source != "0.0.0.0/0" else []
        comment = ["-m", "comment", "--comment", f"arqon:{uuid[:12]}"]

        if dest_ip:
            # Scoped: only traffic destined to THIS VPS is affected.
            ok_forward = await self._exec(
                flag, "FORWARD", "-p", protocol, "-d", dest_ip, *src_flag,
                "--dport", str(port), *comment, "-j", target,
            )
            return ok_forward

        # No IP resolvable — host-wide fallback (same behavior as before).
        ok_input = await self._exec(
            flag, "INPUT", "-p", protocol, *src_flag, "--dport", str(port),
            *comment, "-j", target,
        )
        ok_forward = await self._exec(
            flag, "FORWARD", "-p", protocol, *src_flag, "--dport", str(port),
            *comment, "-j", target,
        )
        return ok_input or ok_forward

    async def remove_rule(self, uuid: str, port: int, protocol: str,
                          action: str = "allow", source: str = "0.0.0.0/0"):
        """Remove a rule. The iptables deletion matches on port/protocol/source
        and clears BOTH allow and deny targets — the bot's `fw-remove` command
        does not know which action was originally applied, and leaving the
        other target behind silently keeps the port blocked (or open)."""
        if not self._iptables:
            self.rules[uuid] = [r for r in self.rules.get(uuid, [])
                                if not (r["port"] == port and r["protocol"] == protocol)]
            return

        # use the stored rule's source when the caller passes the wildcard
        stored = [r for r in self.rules.get(uuid, [])
                  if r["port"] == port and r["protocol"] == protocol]
        sources = {r.get("source", "0.0.0.0/0") for r in stored} or {source if source else "0.0.0.0/0"}

        for src in sources:
            src_flag = ["-s", src] if src and src != "0.0.0.0/0" else []
            for chain in ("INPUT", "FORWARD"):
                for target in ("ACCEPT", "DROP"):
                    await self._exec(
                        "-D", chain, "-p", protocol, *src_flag, "--dport", str(port),
                        "-m", "comment", "--comment", f"arqon:{uuid[:12]}",
                        "-j", target,
                    )

        self.rules[uuid] = [r for r in self.rules.get(uuid, [])
                            if not (r["port"] == port and r["protocol"] == protocol)]

    async def list_rules(self, uuid: str) -> List[Dict]:
        return self.rules.get(uuid, [])

    async def clear_rules(self, uuid: str):
        for rule in list(self.rules.get(uuid, [])):
            await self.remove_rule(uuid, rule["port"], rule["protocol"],
                                   rule.get("action", "allow"), rule.get("source", "0.0.0.0/0"))
        self.rules.pop(uuid, None)
