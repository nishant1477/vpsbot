"""Arqon Node Agent — API routes.

All routes are mounted under /api/v1 by agent.py and speak the exact shapes
that utils/node_client.py (bot side) expects.
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict, Any
import asyncio
import ipaddress
import logging
import os
import time

from docker_backend import DockerManager  # noqa: F401  (named docker_backend to avoid shadowing pip 'docker')
from lxc import LXCManager
from lxd import LXDManager
from kvm import KVMManager
from firewall import FirewallManager
from monitor import system_monitor
from virtualization import detect_virtualization_support, SupportedVirt
from config import agent_settings, AGENT_VERSION

logger = logging.getLogger("node-agent.api")

router = APIRouter()

docker_mgr = DockerManager()
lxc_mgr = LXCManager()
lxd_mgr = LXDManager()
kvm_mgr = KVMManager()
firewall_mgr = FirewallManager()
monitor = system_monitor  # shared singleton (started by agent.py lifespan)

BOOT_TIME = time.time()
_virt_cache: Any = {"values": None, "at": 0.0}

# in-memory provisioning results: {uuid: {"status": ..., "ip_address": ..., "message": ...}}
_provisioning: Dict[str, Dict[str, Any]] = {}
# last known create config per uuid (used by docker restore to rebuild the container)
_last_config: Dict[str, Dict[str, Any]] = {}


class VPSCreateRequest(BaseModel):
    uuid: str
    name: str = "vps"
    hostname: str = "vps.local"
    virtualization: str = "lxd_unsupported"
    os_template: str = "ubuntu-22.04"
    ram: int = 1024
    cpu: int = 1
    disk: int = 10
    ssh_port: int = 22
    root_password: str = ""


class VPSActionRequest(BaseModel):
    uuid: str


class CommandRequest(BaseModel):
    uuid: str
    command: str


class FirewallRuleRequest(BaseModel):
    uuid: str
    port: int
    protocol: str = "tcp"
    action: str          # allow | deny
    source: str = "0.0.0.0/0"


class FirewallDeleteRequest(BaseModel):
    uuid: str
    port: int
    protocol: str = "tcp"
    action: str = "allow"


class RestoreRequest(BaseModel):
    file_path: str


def _manager_for(virtualization: str):
    mapping = {
        "docker": docker_mgr,
        "lxd_supported": lxd_mgr,
        "lxd_unsupported": lxc_mgr,
        "kvm": kvm_mgr,
    }
    return mapping.get((virtualization or "").lower())


async def _resolve_manager(uuid: str):
    """Try every manager until one recognizes the instance.

    exists() is async — calling it without await returned a truthy coroutine,
    which made EVERY uuid resolve to the first manager (docker) and broke all
    VPS actions whenever Docker was unavailable.
    """
    for mgr in (docker_mgr, lxd_mgr, lxc_mgr, kvm_mgr):
        try:
            if await mgr.exists(uuid):
                return mgr
        except Exception:
            continue
    return None


def _safe_uuid(uuid: str) -> str:
    """Reject anything that could escape a path, a command argument or a
    libvirt domain XML. Only [A-Za-z0-9_-] is allowed (no quotes, no spaces,
    no slashes, no shell metacharacters) and it may not start with a dash
    (which some CLIs would parse as a flag)."""
    import re as _re
    if not uuid or len(uuid) > 64 or not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", uuid):
        raise HTTPException(400, "invalid uuid")
    return uuid


async def _vps_ip(uuid: str) -> Optional[str]:
    """Best-effort current IP of a VPS (used to scope firewall rules)."""
    mgr = await _resolve_manager(uuid)
    if not mgr:
        return None
    try:
        state = await mgr.status(uuid)
        return state.get("ip_address") or None
    except Exception:
        return None


# ── health & stats ───────────────────────────────────────────────────

@router.get("/health")
async def health():
    """Health + live stats in one call — the bot heartbeat pings this every
    30s and feeds `stats` straight into the nodes table (RAM/disk/CPU/VPS
    count/agent version)."""
    try:
        stats = await monitor.get_stats()
    except Exception as exc:
        logger.warning(f"stats unavailable in /health: {exc}")
        stats = {"stats": {"version": AGENT_VERSION}}
    return {
        "status": "ok",
        "node": agent_settings.NODE_NAME,
        "version": AGENT_VERSION,
        "uptime": int(time.time() - BOOT_TIME),
        "stats": stats.get("stats", {}),
    }


@router.get("/stats")
async def get_stats():
    stats = await monitor.get_stats()
    # Cache virtualization detection: it spawns several subprocesses and the
    # result rarely changes. Re-detect at most every 5 minutes.
    now = time.monotonic()
    if _virt_cache["values"] is None or now - _virt_cache["at"] > 300:
        try:
            virt = await detect_virtualization_support()
            _virt_cache["values"] = [v.value for v in virt]
        except Exception:
            _virt_cache["values"] = []
        _virt_cache["at"] = now
    return {
        **stats,
        "virtualization": _virt_cache["values"],
        "version": AGENT_VERSION,
        "uptime": int(time.time() - BOOT_TIME),
    }


@router.get("/virtualization")
async def virtualization():
    try:
        virt = await detect_virtualization_support()
        return {"supported": [v.value for v in virt]}
    except Exception as exc:
        return {"supported": [], "error": str(exc)}


# ── VPS lifecycle ────────────────────────────────────────────────────

async def _do_create(request: VPSCreateRequest):
    _provisioning[request.uuid] = {"status": "creating", "ip_address": None, "message": "queued"}
    # Store the config WITHOUT the plaintext root password — keeping it in
    # memory indefinitely was an unnecessary credential exposure; restore
    # flows regenerate credentials instead.
    _last_config[request.uuid] = {
        k: v for k, v in request.dict().items() if k != "root_password"
    }
    mgr = _manager_for(request.virtualization)
    if mgr is None:
        _provisioning[request.uuid] = {"status": "error", "ip_address": None,
                                       "message": f"unsupported virtualization '{request.virtualization}'"}
        return
    try:
        result = await mgr.create(request.dict())
        _provisioning[request.uuid] = {
            "status": result.get("status", "running"),
            "ip_address": result.get("ip_address"),
            "message": result.get("message", "created"),
        }
    except Exception as exc:
        logger.error(f"create {request.uuid} failed: {exc}")
        _provisioning[request.uuid] = {"status": "error", "ip_address": None, "message": str(exc)}


@router.post("/vps/create")
async def create_vps(request: VPSCreateRequest, background_tasks: BackgroundTasks):
    # CRITICAL: uuid is interpolated into libvirt XML and file paths by the
    # backends — it MUST pass the strict validator (an unvalidated uuid once
    # allowed XML injection that could bind-mount the host root into a VM).
    _safe_uuid(request.uuid)
    request.hostname = request.hostname[:200]
    background_tasks.add_task(_do_create, request)
    return {"status": "creating", "uuid": request.uuid, "message": "queued"}


@router.get("/vps")
async def list_vps():
    """List instances across every backend (matches NodeClient.list_vps)."""
    instances = []
    for mgr in (docker_mgr, lxd_mgr, lxc_mgr, kvm_mgr):
        try:
            listing = await mgr.list_all()
            for item in listing:
                item.setdefault("backend", mgr.__class__.__name__)
                instances.append(item)
        except Exception as exc:
            logger.debug(f"list_all on {mgr.__class__.__name__} failed: {exc}")
    return {"instances": instances}


@router.get("/vps/{uuid}")
async def vps_status(uuid: str):
    if uuid in _provisioning:
        entry = _provisioning[uuid]
        if entry.get("status") in ("creating", "queued"):
            return entry
        # Terminal state — hand the entry out once, then serve live status so
        # later stop/start crashes and IP changes are visible via the API.
        _provisioning.pop(uuid, None)
        if entry.get("status") == "error":
            return entry
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        return await mgr.status(uuid)
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.delete("/vps/{uuid}")
async def delete_vps(uuid: str):
    mgr = await _resolve_manager(uuid)
    # Always drop this VPS's firewall rules — stale rules would keep ports
    # open on the host forever after deletion.
    try:
        await firewall_mgr.clear_rules(uuid)
    except Exception as exc:
        logger.warning(f"clear_rules({uuid}) failed: {exc}")
    if not mgr:
        # nothing found — still report success so the DB can be cleaned
        _provisioning.pop(uuid, None)
        _last_config.pop(uuid, None)
        return {"status": "deleted", "uuid": uuid, "message": "not found on node"}
    try:
        await mgr.delete(uuid)
        _provisioning.pop(uuid, None)
        _last_config.pop(uuid, None)
        return {"status": "deleted", "uuid": uuid, "message": "deleted"}
    except Exception as exc:
        logger.error(f"delete {uuid} failed: {exc}")
        raise HTTPException(500, str(exc))


@router.post("/vps/{uuid}/start")
async def start_vps(uuid: str):
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        await mgr.start(uuid)
        return {"status": "running", "uuid": uuid, "message": "started"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/vps/{uuid}/stop")
async def stop_vps(uuid: str):
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        await mgr.stop(uuid)
        return {"status": "stopped", "uuid": uuid, "message": "stopped"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/vps/{uuid}/restart")
async def restart_vps(uuid: str):
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        await mgr.restart(uuid)
        return {"status": "running", "uuid": uuid, "message": "restarted"}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/vps/{uuid}/reinstall")
async def reinstall_vps(uuid: str, body: Optional[dict] = None):
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        # reinstall wipes the filesystem — old port rules are meaningless after it
        await firewall_mgr.clear_rules(uuid)
        os_template = (body or {}).get("os_template")
        result = await mgr.reinstall(uuid, os_template)
        return {"status": "reinstalling", "uuid": uuid, "message": result.get("message", "reinstalling")}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/vps/execute")
async def execute_command(request: CommandRequest):
    mgr = await _resolve_manager(request.uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        output = await mgr.execute(request.uuid, request.command)
        return {"output": output}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.get("/vps/{uuid}/stats")
async def get_vps_stats(uuid: str):
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        return await mgr.get_stats(uuid)
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Backup / restore ─────────────────────────────────────────────

@router.post("/vps/{uuid}/backup")
async def backup_vps(uuid: str):
    """Create a real backup of the VPS filesystem on this node.

    Supported backends: LXC (rootfs tar.gz), Docker (container export),
    KVM (qcow2 convert), LXD (`lxc export`). Synchronous — the bot calls it
    with a long timeout.
    """
    _safe_uuid(uuid)
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    try:
        result = await mgr.backup(uuid)
        return {"status": "completed", **result}
    except NotImplementedError:
        raise HTTPException(501, f"{mgr.__class__.__name__} does not support backups")
    except Exception as exc:
        logger.error(f"backup {uuid} failed: {exc}")
        raise HTTPException(500, str(exc))


@router.post("/vps/{uuid}/restore")
async def restore_vps(uuid: str, body: RestoreRequest):
    """Restore a previously created backup (file must live under BACKUP_PATH)."""
    _safe_uuid(uuid)
    mgr = await _resolve_manager(uuid)
    if not mgr:
        raise HTTPException(404, "VPS not found")
    path = os.path.realpath(body.file_path)
    backup_root = os.path.realpath(agent_settings.BACKUP_PATH)
    if not path.startswith(backup_root + os.sep) or not os.path.isfile(path):
        raise HTTPException(400, "file_path must be an existing file under the node backup directory")
    try:
        await mgr.restore(uuid, path)
        return {"status": "restored", "uuid": uuid, "message": f"restored from {os.path.basename(path)}"}
    except NotImplementedError:
        raise HTTPException(501, f"{mgr.__class__.__name__} does not support restore")
    except Exception as exc:
        logger.error(f"restore {uuid} failed: {exc}")
        raise HTTPException(500, str(exc))


# ── Firewall ─────────────────────────────────────────────────────────

@router.get("/firewall/rules")
async def firewall_list(uuid: str):
    try:
        rules = await firewall_mgr.list_rules(uuid)
        return {"rules": rules}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.post("/firewall/rules")
async def firewall_add(request: FirewallRuleRequest):
    # Validate before touching iptables — a bad source like "any" would make
    # iptables fail while the rule was still recorded as active.
    if request.protocol not in ("tcp", "udp"):
        raise HTTPException(400, "protocol must be tcp or udp")
    if not (1 <= request.port <= 65535):
        raise HTTPException(400, "port must be 1-65535")
    if request.action not in ("allow", "deny"):
        raise HTTPException(400, "action must be allow or deny")
    try:
        ipaddress.ip_network(request.source, strict=False)
    except ValueError:
        raise HTTPException(400, f"invalid source CIDR: {request.source}")
    try:
        # Scope the rule to the VPS IP when resolvable so a rule for one tenant
        # never opens a port on the whole host.
        dest_ip = await _vps_ip(request.uuid)
        await firewall_mgr.add_rule(request.uuid, request.port, request.protocol,
                                    request.action, request.source, dest_ip=dest_ip)
        return {"status": request.action, "port": request.port,
                "protocol": request.protocol, "source": request.source,
                "scoped_to": dest_ip}
    except Exception as exc:
        raise HTTPException(500, str(exc))


@router.delete("/firewall/rules")
async def firewall_remove(request: FirewallDeleteRequest):
    # mirror the add-route validation — values reach iptables argv
    if request.protocol not in ("tcp", "udp"):
        raise HTTPException(400, "protocol must be tcp or udp")
    if not (1 <= request.port <= 65535):
        raise HTTPException(400, "port must be 1-65535")
    if request.action not in ("allow", "deny"):
        raise HTTPException(400, "action must be allow or deny")
    _safe_uuid(request.uuid)
    try:
        await firewall_mgr.remove_rule(request.uuid, request.port, request.protocol, request.action)
        return {"status": "removed", "port": request.port, "protocol": request.protocol}
    except Exception as exc:
        raise HTTPException(500, str(exc))
