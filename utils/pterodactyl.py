import aiohttp
from typing import Optional, Dict, Any, List
import config
from utils.logging import get_logger

logger = get_logger("pterodactyl")

class PterodactylClient:
    def __init__(self, url: str = None, api_key: str = None):
        self.url = (url or config.settings.PTERODACTYL_URL).rstrip('/')
        self.api_key = api_key or config.settings.PTERODACTYL_API_KEY
        self.app_api_key = config.settings.PTERODACTYL_APP_API_KEY
        self.client_api_key = config.settings.PTERODACTYL_CLIENT_API_KEY
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.url}/api/{endpoint.lstrip('/')}"
        
        try:
            async with session.request(method, url, **kwargs) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Pterodactyl API error: {e}")
            raise
    
    async def get_servers(self, node_id: int = None) -> List[Dict]:
        params = {}
        if node_id:
            params["node"] = node_id
        result = await self._request("GET", "application/servers", params=params)
        return result.get("data", []) if result else []
    
    async def get_server(self, server_id: int) -> Optional[Dict]:
        result = await self._request("GET", f"application/servers/{server_id}")
        return result.get("attributes") if result else None
    
    async def create_server(self, data: Dict) -> Optional[Dict]:
        result = await self._request("POST", "application/servers", json=data)
        return result.get("attributes") if result else None
    
    async def delete_server(self, server_id: int) -> bool:
        await self._request("DELETE", f"application/servers/{server_id}")
        return True
    
    async def send_power_action(self, server_id: int, action: str) -> bool:
        await self._request("POST", f"application/servers/{server_id}/power", json={"signal": action})
        return True
    
    async def send_command(self, server_id: int, command: str) -> bool:
        await self._request("POST", f"application/servers/{server_id}/command", json={"command": command})
        return True
    
    async def get_server_resources(self, server_id: int) -> Optional[Dict]:
        result = await self._request("GET", f"application/servers/{server_id}/resources")
        return result.get("attributes") if result else None
    
    async def get_nodes(self) -> List[Dict]:
        result = await self._request("GET", "application/nodes")
        return result.get("data", []) if result else []
    
    async def get_node(self, node_id: int) -> Optional[Dict]:
        result = await self._request("GET", f"application/nodes/{node_id}")
        return result.get("attributes") if result else None
    
    async def get_allocations(self, node_id: int = None) -> List[Dict]:
        params = {}
        if node_id:
            params["node"] = node_id
        result = await self._request("GET", "application/allocations", params=params)
        return result.get("data", []) if result else []
    
    async def create_allocation(self, node_id: int, ip: str, port: int) -> Optional[Dict]:
        result = await self._request("POST", "application/allocations", json={
            "ip": ip,
            "port": port,
            "node_id": node_id
        })
        return result.get("attributes") if result else None
    
    async def delete_allocation(self, allocation_id: int) -> bool:
        await self._request("DELETE", f"application/allocations/{allocation_id}")
        return True
    
    async def get_eggs(self) -> List[Dict]:
        result = await self._request("GET", "application/eggs")
        return result.get("data", []) if result else []
    
    async def get_egg(self, egg_id: int) -> Optional[Dict]:
        result = await self._request("GET", f"application/eggs/{egg_id}")
        return result.get("attributes") if result else None
    
    async def create_user(self, data: Dict) -> Optional[Dict]:
        result = await self._request("POST", "application/users", json=data)
        return result.get("attributes") if result else None
    
    async def get_user(self, user_id: int) -> Optional[Dict]:
        result = await self._request("GET", f"application/users/{user_id}")
        return result.get("attributes") if result else None
    
    async def update_server_build(self, server_id: int, build: Dict) -> bool:
        await self._request("PATCH", f"application/servers/{server_id}/build", json=build)
        return True
    
    async def create_backup(self, server_id: int, name: str = None) -> Optional[Dict]:
        data = {"name": name} if name else {}
        result = await self._request("POST", f"application/servers/{server_id}/backups", json=data)
        return result.get("attributes") if result else None
    
    async def get_backups(self, server_id: int) -> List[Dict]:
        result = await self._request("GET", f"application/servers/{server_id}/backups")
        return result.get("data", []) if result else []
    
    async def restore_backup(self, server_id: int, backup_id: int) -> bool:
        await self._request("POST", f"application/servers/{server_id}/backups/{backup_id}/restore")
        return True
    
    async def delete_backup(self, server_id: int, backup_id: int) -> bool:
        await self._request("DELETE", f"application/servers/{server_id}/backups/{backup_id}")
        return True

class PterodactylClientAPI:
    def __init__(self, url: str = None, api_key: str = None):
        self.url = (url or config.settings.PTERODACTYL_URL).rstrip('/')
        self.api_key = api_key or config.settings.PTERODACTYL_CLIENT_API_KEY
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            self._session = aiohttp.ClientSession(headers=headers)
        return self._session
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.url}/api/client/{endpoint.lstrip('/')}"
        
        try:
            async with session.request(method, url, **kwargs) as response:
                if response.status == 404:
                    return None
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Pterodactyl Client API error: {e}")
            raise
    
    async def get_server(self, server_id: str) -> Optional[Dict]:
        result = await self._request("GET", f"servers/{server_id}")
        return result.get("attributes") if result else None
    
    async def send_power_action(self, server_id: str, action: str) -> bool:
        await self._request("POST", f"servers/{server_id}/power", json={"signal": action})
        return True
    
    async def send_command(self, server_id: str, command: str) -> bool:
        await self._request("POST", f"servers/{server_id}/command", json={"command": command})
        return True
    
    async def get_resources(self, server_id: str) -> Optional[Dict]:
        result = await self._request("GET", f"servers/{server_id}/resources")
        return result.get("attributes") if result else None
    
    async def get_files(self, server_id: str, directory: str = "/") -> List[Dict]:
        result = await self._request("GET", f"servers/{server_id}/files/list", params={"directory": directory})
        return result.get("data", []) if result else []
    
    async def download_file(self, server_id: str, file_path: str) -> bytes:
        session = await self._get_session()
        url = f"{self.url}/api/client/servers/{server_id}/files/download?file={file_path}"
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()
    
    async def upload_file(self, server_id: str, file_path: str, content: bytes) -> bool:
        session = await self._get_session()
        url = f"{self.url}/api/client/servers/{server_id}/files/write?file={file_path}"
        async with session.post(url, data=content) as response:
            response.raise_for_status()
        return True
    
    async def delete_file(self, server_id: str, file_path: str) -> bool:
        await self._request("DELETE", f"servers/{server_id}/files/delete", json={"files": [file_path]})
        return True
    
    async def create_backup(self, server_id: str, name: str = None) -> Optional[Dict]:
        data = {"name": name} if name else {}
        result = await self._request("POST", f"servers/{server_id}/backups", json=data)
        return result.get("attributes") if result else None
    
    async def get_backups(self, server_id: str) -> List[Dict]:
        result = await self._request("GET", f"servers/{server_id}/backups")
        return result.get("data", []) if result else []
    
    async def restore_backup(self, server_id: str, backup_id: str) -> bool:
        await self._request("POST", f"servers/{server_id}/backups/{backup_id}/restore")
        return True