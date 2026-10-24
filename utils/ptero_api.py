"""Arqon Bot — Pterodactyl API client (Application + Client API).

Exposes:
    PterodactylAPIError          — raised on any API failure
    PterodactylClient            — application API wrapper
    get_pterodactyl_api()        — factory used by cogs
"""
import asyncio
from typing import Optional, Dict, Any, List

import aiohttp

import config
from config import settings


class PterodactylAPIError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


# Server identifiers are opaque Pterodactyl short-ids — never allow "/", ".."
# or query strings to reach the URL path (path-injection guard).
import re as _re
_SID_RE = _re.compile(r"^[A-Za-z0-9]{6,24}$")


def _sid(identifier: str) -> str:
    identifier = (identifier or "").strip()
    if not _SID_RE.fullmatch(identifier):
        raise PterodactylAPIError("invalid server identifier")
    return identifier


class PterodactylClient:
    """Minimal but complete Application + Client API client."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str,
                       json_body: Optional[Dict] = None,
                       params: Optional[Dict] = None) -> Any:
        if not self.configured:
            raise PterodactylAPIError("Pterodactyl is not configured (missing URL or API key).")
        url = f"{self.base_url}/api/application{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url,
                    json=json_body,
                    params=params,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status in (200, 201, 204):
                        if resp.status == 204:
                            return {}
                        try:
                            return await resp.json()
                        except Exception:
                            return {}
                    try:
                        detail = await resp.json()
                    except Exception:
                        detail = await resp.text()
                    raise PterodactylAPIError(
                        f"Pterodactyl API error {resp.status} on {path}",
                        status=resp.status,
                        detail=detail,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PterodactylAPIError(f"Cannot reach Pterodactyl panel: {exc}") from exc

    # ── Application API ──────────────────────────────────────────────

    async def list_nodes(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/nodes", params={"per_page": 100})
        return data.get("data", [])

    async def list_eggs(self, nest_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if nest_id:
            data = await self._request("GET", f"/nests/{nest_id}/eggs", params={"per_page": 100})
        else:
            data = await self._request("GET", "/eggs", params={"per_page": 250})
        return data.get("data", [])

    async def list_locations(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/locations", params={"per_page": 100})
        return data.get("data", [])

    async def list_nests(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/nests", params={"per_page": 100})
        return data.get("data", [])

    async def get_egg(self, egg_id: int) -> Dict[str, Any]:
        data = await self._request("GET", f"/eggs/{egg_id}")
        return data.get("attributes", {})

    async def create_server(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = await self._request("POST", "/servers", json_body=payload)
        return data.get("attributes", {})

    async def get_server(self, server_id: int) -> Dict[str, Any]:
        data = await self._request("GET", f"/servers/{server_id}", params={"include": ["allocations"]})
        return data.get("attributes", {})

    async def list_servers(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/servers", params={"per_page": 100})
        return data.get("data", [])

    async def delete_server(self, server_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/servers/{server_id}")

    async def suspend_server(self, server_id: int) -> Dict[str, Any]:
        return await self._request("POST", f"/servers/{server_id}/suspend")

    async def unsuspend_server(self, server_id: int) -> Dict[str, Any]:
        return await self._request("POST", f"/servers/{server_id}/unsuspend")

    async def create_allocation(self, node_id: int, port: int) -> Dict[str, Any]:
        return await self._request("POST", f"/nodes/{node_id}/allocations",
                                   json_body={"ports": [str(port)], "ip": None})

    async def find_free_port(self, node_id: int, start: int, end: int) -> Optional[int]:
        used = set()
        data = await self._request("GET", f"/nodes/{node_id}/allocations", params={"per_page": 200})
        for item in data.get("data", []):
            attrs = item.get("attributes", {})
            used.add(attrs.get("port"))
        for port in range(start, end + 1):
            if port not in used:
                return port
        return None


class ClientAPI:
    """Pterodactyl Client API (per-server power actions, backups, console)."""

    def __init__(self, base_url: str, client_api_key: str, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = client_api_key or ""
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def _request(self, method: str, path: str,
                       json_body: Optional[Dict] = None) -> Any:
        if not self.configured:
            raise PterodactylAPIError("Pterodactyl client API is not configured.")
        url = f"{self.base_url}/api/client{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url, json=json_body, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status in (200, 201, 204):
                        if resp.status == 204:
                            return {}
                        try:
                            return await resp.json()
                        except Exception:
                            return {}
                    try:
                        detail = await resp.json()
                    except Exception:
                        detail = await resp.text()
                    raise PterodactylAPIError(
                        f"Pterodactyl client API error {resp.status} on {path}",
                        status=resp.status, detail=detail,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise PterodactylAPIError(f"Cannot reach Pterodactyl panel: {exc}") from exc

    async def power(self, server_identifier: str, signal: str) -> Any:
        return await self._request("POST", f"/servers/{_sid(server_identifier)}/power",
                                   json_body={"signal": signal})

    async def send_command(self, server_identifier: str, command: str) -> Any:
        return await self._request("POST", f"/servers/{_sid(server_identifier)}/command",
                                   json_body={"command": command})

    async def list_backups(self, server_identifier: str) -> List[Dict[str, Any]]:
        data = await self._request("GET", f"/servers/{_sid(server_identifier)}/backups")
        return data.get("data", [])

    async def create_backup(self, server_identifier: str, name: str = None) -> Any:
        body = {"name": name} if name else {}
        return await self._request("POST", f"/servers/{_sid(server_identifier)}/backups",
                                   json_body=body)

    async def get_resources(self, server_identifier: str) -> Dict[str, Any]:
        data = await self._request("GET", f"/servers/{_sid(server_identifier)}/resources")
        return data.get("attributes", {})

    async def reindex(self, server_identifier: str) -> Any:
        return await self._request("POST", f"/servers/{_sid(server_identifier)}/settings/rename")


# ── Factory ──────────────────────────────────────────────────────────

_default_app_client: Optional[PterodactylClient] = None
_default_client_api: Optional[ClientAPI] = None


def get_pterodactyl_api(api_key: Optional[str] = None,
                        base_url: Optional[str] = None) -> PterodactylClient:
    """Return a PterodactylClient using explicit args or global settings."""
    global _default_app_client
    if api_key or base_url:
        return PterodactylClient(
            base_url or settings.PTERODACTYL_URL or "",
            api_key or settings.PTERODACTYL_API_KEY or settings.PTERODACTYL_APP_API_KEY or "",
        )
    if _default_app_client is None:
        _default_app_client = PterodactylClient(
            settings.PTERODACTYL_URL or "",
            settings.PTERODACTYL_API_KEY or settings.PTERODACTYL_APP_API_KEY or "",
        )
    return _default_app_client


def get_client_api(client_api_key: Optional[str] = None,
                   base_url: Optional[str] = None) -> ClientAPI:
    global _default_client_api
    if client_api_key or base_url:
        return ClientAPI(
            base_url or settings.PTERODACTYL_URL or "",
            client_api_key or settings.PTERODACTYL_CLIENT_API_KEY or "",
        )
    if _default_client_api is None:
        _default_client_api = ClientAPI(
            settings.PTERODACTYL_URL or "",
            settings.PTERODACTYL_CLIENT_API_KEY or "",
        )
    return _default_client_api
