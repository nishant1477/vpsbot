"""Arqon Node Agent — LXD backend (pylxd, blocking calls in executor)."""
import asyncio
import logging
import os
from typing import Dict, Any, Optional

logger = logging.getLogger("node-agent.lxd")

IMAGE_MAP = {
    "ubuntu-22.04": "ubuntu:22.04",
    "ubuntu-24.04": "ubuntu:24.04",
    "ubuntu22.04": "ubuntu:22.04",
    "debian-11": "debian:11",
    "debian-12": "debian:12",
    "almalinux-9": "almalinux:9",
    "rockylinux-9": "rockylinux:9",
    "alpine-3.19": "alpine:3.19",
}


class LXDManager:
    def __init__(self):
        self.client = None

    async def _get_client(self):
        if self.client is None:
            def _connect():
                import pylxd
                return pylxd.Client()
            try:
                self.client = await asyncio.get_running_loop().run_in_executor(None, _connect)
            except Exception as exc:
                logger.error(f"LXD not available: {exc}")
                raise RuntimeError("LXD not available")
        return self.client

    async def _run(self, cmd: list, timeout: int = 180) -> tuple:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return 1, "", f"command timed out after {timeout}s"
        except FileNotFoundError as exc:
            return 1, "", str(exc)

    async def _run_py(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def _get_instance(self, uuid: str):
        client = await self._get_client()
        try:
            return await self._run_py(client.instances.get, uuid)
        except Exception:
            return None

    async def exists(self, uuid: str) -> bool:
        inst = await self._get_instance(uuid)
        return inst is not None

    async def create(self, config: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._get_client()
        uuid = config["uuid"]
        image = IMAGE_MAP.get(config.get("os_template", ""), "ubuntu:22.04")

        def _create():
            inst = client.instances.create({
                "name": uuid,
                "type": "container",
                "source": {"type": "image", "alias": image},
                "config": {
                    "limits.memory": f"{config.get('ram', 1024)}MiB",
                    "limits.cpu": str(config.get("cpu", 1)),
                },
                "profiles": ["default"],
            }, wait=True)
            inst.start(wait=True)
            return inst

        try:
            inst = await self._run_py(_create)
            ip = None
            try:
                state = await self._run_py(inst.state)
                addrs = (state.network or {}).get("eth0", {}).get("addresses", [])
                ip = next((a["address"] for a in addrs if a.get("family") == "inet"), None)
            except Exception:
                pass
            logger.info(f"Created LXD container {uuid}")
            return {"status": "running", "ip_address": ip, "message": "lxd container created"}
        except Exception as exc:
            logger.error(f"LXD create failed: {exc}")
            raise

    async def delete(self, uuid: str):
        inst = await self._get_instance(uuid)
        if not inst:
            return
        try:
            await self._run_py(inst.stop, wait=True, force=True, timeout=30)
        except Exception:
            pass
        await self._run_py(inst.delete, wait=True)
        logger.info(f"Deleted LXD instance {uuid}")

    async def start(self, uuid: str):
        inst = await self._get_instance(uuid)
        if not inst:
            raise RuntimeError("instance not found")
        await self._run_py(inst.start, wait=True, timeout=60)

    async def stop(self, uuid: str):
        inst = await self._get_instance(uuid)
        if not inst:
            raise RuntimeError("instance not found")
        await self._run_py(inst.stop, wait=True, timeout=60)

    async def restart(self, uuid: str):
        inst = await self._get_instance(uuid)
        if not inst:
            raise RuntimeError("instance not found")
        await self._run_py(inst.restart, wait=True, timeout=60)

    async def status(self, uuid: str) -> Dict[str, Any]:
        inst = await self._get_instance(uuid)
        if not inst:
            raise RuntimeError("instance not found")
        state = await self._run_py(inst.state)
        addrs = (state.network or {}).get("eth0", {}).get("addresses", [])
        ip = next((a["address"] for a in addrs if a.get("family") == "inet"), None)
        return {"status": state.status.lower(), "ip_address": ip}

    async def reinstall(self, uuid: str, os_template: Optional[str] = None) -> Dict[str, Any]:
        await self.delete(uuid)
        cfg = {"uuid": uuid, "os_template": os_template or "ubuntu-22.04", "ram": 1024, "cpu": 1}
        return await self.create(cfg)

    async def execute(self, uuid: str, command: str) -> str:
        inst = await self._get_instance(uuid)
        if not inst:
            raise RuntimeError("instance not found")
        result = await self._run_py(inst.execute, ["bash", "-c", command])
        return result.stdout if getattr(result, "exit_code", 0) == 0 else result.stderr

    async def list_all(self) -> list:
        client = await self._get_client()
        try:
            instances = await self._run_py(lambda: client.instances.all())
        except Exception:
            return []
        out = []
        for inst in instances:
            try:
                out.append({"uuid": inst.name, "status": inst.status.lower(), "image": ""})
            except Exception:
                continue
        return out

    async def backup(self, uuid: str) -> Dict[str, Any]:
        """`lxc export` produces a restorable tarball (works for containers & VMs)."""
        if not await self.exists(uuid):
            raise RuntimeError("instance not found")
        from config import agent_settings
        backup_dir = os.path.join(agent_settings.BACKUP_PATH, uuid)
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"lxd-{uuid[:12]}-{int(asyncio.get_event_loop().time())}.tar.gz")
        code, out, err = await self._run(["lxc", "export", uuid, path, "--instance-only"], timeout=1800)
        if code != 0:
            raise RuntimeError(f"LXD export failed: {err or out}")
        size = os.path.getsize(path)
        logger.info(f"LXD backup {uuid} -> {path} ({size} bytes)")
        return {"file_path": path, "file_size": size}

    async def restore(self, uuid: str, file_path: str) -> None:
        """LXD import requires the instance to not exist — delete, import, start."""
        await self.delete(uuid)
        code, out, err = await self._run(["lxc", "import", file_path, uuid], timeout=1800)
        if code != 0:
            raise RuntimeError(f"LXD import failed: {err or out}")
        code, _, err = await self._run(["lxc", "start", uuid], timeout=120)
        if code != 0:
            raise RuntimeError(f"LXD start after restore failed: {err}")
        logger.info(f"LXD restore {uuid} from {file_path}")

    async def get_stats(self, uuid: str) -> Dict[str, Any]:
        inst = await self._get_instance(uuid)
        if not inst:
            raise RuntimeError("instance not found")
        state = await self._run_py(inst.state)
        # pylxd exposes cpu/memory/network as plain dicts — attribute access crashes.
        return {
            "running": state.status == "Running",
            "cpu_usage_ns": (state.cpu or {}).get("usage", 0),
            "memory_used": (state.memory or {}).get("usage", 0),
        }
