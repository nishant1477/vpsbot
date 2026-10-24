"""Arqon Bot — Node communication client (multi-node layer).

Provides a uniform async HTTP client that talks to every registered node
agent. Each Node row carries its own host/port/api_key, so the bot can manage
an unlimited fleet of nodes with per-node authentication.

Usage:
    from utils.node_client import NodeClient, NodeConnectionError

    async with NodeClient(node) as client:
        stats = await client.get_stats()
        await client.create_vps(uuid=..., ram=..., ...)
"""
import asyncio
import time
from typing import Optional, Dict, Any, List

import aiohttp


class NodeConnectionError(Exception):
    """Raised when a node agent cannot be reached or returns an error."""

    def __init__(self, message: str, status: Optional[int] = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class NodeClient:
    """Async HTTP client bound to a single node (any object with
    host / port / api_key attributes, e.g. database.Node)."""

    def __init__(self, node, timeout: int = 15):
        self.node = node
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return f"http://{self.node.host}:{self.node.port}"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.node.api_key}",
            "Content-Type": "application/json",
            "X-Node-Name": getattr(self.node, "name", "") or "",
        }

    async def _request(self, method: str, path: str,
                       json_body: Optional[Dict] = None,
                       params: Optional[Dict] = None,
                       timeout: Optional[int] = None,
                       retries: int = 2) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(max(1, retries + 1)):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.request(
                        method, url,
                        json=json_body,
                        params=params,
                        headers=self._headers(),
                        timeout=aiohttp.ClientTimeout(total=timeout or self.timeout),
                    ) as resp:
                        if resp.status == 401:
                            raise NodeConnectionError(
                                f"Node '{self.node.name}' rejected the API key (401).",
                                status=401,
                            )
                        if resp.status == 404:
                            raise NodeConnectionError(
                                f"Endpoint {path} not found on node '{self.node.name}'.",
                                status=404,
                            )
                        if resp.status >= 400:
                            try:
                                detail = await resp.json()
                            except Exception:
                                detail = await resp.text()
                            raise NodeConnectionError(
                                f"Node '{self.node.name}' returned {resp.status}.",
                                status=resp.status,
                                detail=detail,
                            )
                        if resp.content_type == "application/json":
                            return await resp.json()
                        return {"raw": await resp.text()}
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError,
                    aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                raise NodeConnectionError(
                    f"Cannot reach node '{self.node.name}' at {self.base_url}: {exc}"
                ) from exc

        raise NodeConnectionError(f"Request failed: {last_exc}")  # pragma: no cover

    # ── Health / stats ───────────────────────────────────────────────

    async def ping(self) -> Dict[str, Any]:
        """Authenticated health check with latency measurement (ms)."""
        start = time.monotonic()
        data = await self._request("GET", "/api/v1/health", retries=1, timeout=8)
        latency_ms = int((time.monotonic() - start) * 1000)
        data["latency_ms"] = latency_ms
        return data

    async def get_stats(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/stats")

    async def detect_virtualization(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/virtualization")

    # ── VPS lifecycle ────────────────────────────────────────────────

    async def create_vps(self, **payload) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/vps/create", json_body=payload, timeout=120)

    async def start_vps(self, uuid: str) -> Dict[str, Any]:
        return await self._request("POST", f"/api/v1/vps/{uuid}/start", timeout=60)

    async def stop_vps(self, uuid: str) -> Dict[str, Any]:
        return await self._request("POST", f"/api/v1/vps/{uuid}/stop", timeout=60)

    async def restart_vps(self, uuid: str) -> Dict[str, Any]:
        return await self._request("POST", f"/api/v1/vps/{uuid}/restart", timeout=90)

    async def delete_vps(self, uuid: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/api/v1/vps/{uuid}", timeout=90)

    async def reinstall_vps(self, uuid: str, os_template: str = None) -> Dict[str, Any]:
        body = {"os_template": os_template} if os_template else {}
        return await self._request("POST", f"/api/v1/vps/{uuid}/reinstall", json_body=body, timeout=180)

    async def get_vps(self, uuid: str) -> Dict[str, Any]:
        return await self._request("GET", f"/api/v1/vps/{uuid}")

    async def list_vps(self) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/vps")

    async def backup_vps(self, uuid: str, timeout: int = 900) -> Dict[str, Any]:
        """Create a real filesystem backup on the node (agent-side)."""
        return await self._request("POST", f"/api/v1/vps/{uuid}/backup", timeout=timeout)

    async def restore_vps(self, uuid: str, file_path: str, timeout: int = 900) -> Dict[str, Any]:
        """Restore a node-side backup file (must live under the agent's BACKUP_PATH)."""
        return await self._request("POST", f"/api/v1/vps/{uuid}/restore",
                                   json_body={"file_path": file_path}, timeout=timeout)

    async def execute(self, uuid: str, command: str, timeout: int = 30) -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/vps/execute",
                                   json_body={"uuid": uuid, "command": command},
                                   timeout=timeout)

    # ── Firewall ─────────────────────────────────────────────────────

    async def firewall_add(self, uuid: str, action: str, port: int,
                           protocol: str = "tcp", source: str = "0.0.0.0/0") -> Dict[str, Any]:
        return await self._request("POST", "/api/v1/firewall/rules", json_body={
            "uuid": uuid,
            "action": action,      # allow | deny
            "port": port,
            "protocol": protocol,
            "source": source,      # must be a CIDR — "any" is rejected by the agent
        })

    async def firewall_remove(self, uuid: str, port: int,
                              protocol: str = "tcp", action: str = "allow") -> Dict[str, Any]:
        return await self._request("DELETE", "/api/v1/firewall/rules", json_body={
            "uuid": uuid,
            "action": action,
            "port": port,
            "protocol": protocol,
        })

    async def firewall_list(self, uuid: str) -> Dict[str, Any]:
        return await self._request("GET", "/api/v1/firewall/rules",
                                   params={"uuid": uuid})


# ── Fleet-level helpers (multi-node orchestration) ───────────────────

async def check_node_health(node) -> Dict[str, Any]:
    """Ping one node and return {'online': bool, 'latency': int, 'stats': dict|None}."""
    client = NodeClient(node)
    try:
        result = await client.ping()
        return {"online": True, "latency": result.get("latency_ms", 0), "result": result}
    except NodeConnectionError:
        return {"online": False, "latency": 0, "result": None}


async def check_all_nodes(nodes: List) -> Dict[int, Dict[str, Any]]:
    """Health-check many nodes concurrently.

    Returns {node_id: {'online': bool, 'latency': int, 'result': dict|None}}
    """
    async def _one(node):
        return node.id, await check_node_health(node)

    results = await asyncio.gather(*[_one(n) for n in nodes], return_exceptions=True)
    out: Dict[int, Dict[str, Any]] = {}
    for item in results:
        if isinstance(item, Exception):
            continue
        node_id, health = item
        out[node_id] = health
    return out


async def pick_best_node(candidates: List, required: Dict[str, int] = None,
                         exclude_ids: Optional[set] = None) -> Optional[Any]:
    """Choose the least-loaded online node that fits the resource request.

    Scoring: available RAM after allocation (higher is better), then latency.
    Returns the node object or None when nothing fits.
    """
    required = required or {}
    need_ram = required.get("ram", 0)
    need_disk = required.get("disk", 0)
    exclude_ids = exclude_ids or set()

    health_map = await check_all_nodes([n for n in candidates if n.id not in exclude_ids])

    best = None
    best_score = float("-inf")
    for node in candidates:
        if node.id in exclude_ids:
            continue
        health = health_map.get(node.id)
        if not health or not health["online"]:
            continue
        if getattr(node, "maintenance_mode", False):
            continue

        total_ram = node.total_ram or node.max_ram or 0
        used_ram = node.used_ram or 0
        avail_ram = total_ram - used_ram

        total_disk = node.total_disk or node.max_disk or 0
        used_disk = node.used_disk or 0
        avail_disk = total_disk - used_disk

        if total_ram and avail_ram < need_ram:
            continue
        if total_disk and avail_disk < need_disk:
            continue
        if node.max_vps and node.vps_count >= node.max_vps:
            continue

        # score: most available RAM wins; latency breaks ties
        score = avail_ram - health["latency"]
        if score > best_score:
            best_score = score
            best = node

    return best
