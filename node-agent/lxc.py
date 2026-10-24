"""Arqon Node Agent — LXC backend (lxc-* CLI)."""
import asyncio
import logging
import os
import shutil
from typing import Dict, Any, Optional

logger = logging.getLogger("node-agent.lxc")

DISTRO_MAP = {
    "ubuntu-22.04": ("ubuntu", "jammy"),
    "ubuntu-24.04": ("ubuntu", "noble"),
    "debian-11": ("debian", "bullseye"),
    "debian-12": ("debian", "bookworm"),
    "almalinux-9": ("almalinux", "9"),
    "rockylinux-9": ("rockylinux", "9"),
    "alpine-3.19": ("alpine", "3.19"),
}


class LXCManager:
    def __init__(self):
        self._lxc_create = shutil.which("lxc-create")

    async def _run(self, cmd: list, timeout: int = 180) -> tuple:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        except asyncio.TimeoutError:
            # kill the orphaned operation instead of leaking it forever
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return 1, "", f"command timed out after {timeout}s: {cmd[0]}"
        except FileNotFoundError as exc:
            return 1, "", str(exc)

    async def exists(self, uuid: str) -> bool:
        code, out, _ = await self._run(["lxc-ls", "-1"])
        return uuid in out.split()

    async def create(self, config: Dict[str, Any]) -> Dict[str, Any]:
        uuid = config["uuid"]
        template = config.get("os_template", "ubuntu-22.04")
        distro, release = DISTRO_MAP.get(template, ("ubuntu", "jammy"))

        cmd = ["lxc-create", "-n", uuid, "-t", "download", "--",
               "-d", distro, "-r", release, "-a", "amd64"]
        code, out, err = await self._run(cmd)
        if code != 0:
            raise RuntimeError(f"LXC create failed: {err or out}")

        await self._set_config(uuid, config)
        code, _, err = await self._run(["lxc-start", "-n", uuid])
        if code != 0:
            raise RuntimeError(f"LXC start failed: {err}")

        logger.info(f"Created LXC container {uuid}")
        return {"status": "running", "ip_address": None, "message": "lxc container created"}

    async def _set_config(self, uuid: str, config: Dict[str, Any]):
        cfg_path = f"/var/lib/lxc/{uuid}/config"
        try:
            with open(cfg_path, "a") as fh:
                fh.write("\n# Arqon bot limits\n")
                fh.write(f"lxc.cgroup2.memory.max = {config.get('ram', 1024)}M\n")
                fh.write(f"lxc.cgroup2.cpu.max = {int(config.get('cpu', 1)) * 100000} 100000\n")
        except OSError as exc:
            logger.warning(f"Could not write LXC config: {exc}")

    async def delete(self, uuid: str):
        await self._run(["lxc-stop", "-n", uuid, "-k"])
        await self._run(["lxc-destroy", "-n", uuid])
        logger.info(f"Deleted LXC container {uuid}")

    async def start(self, uuid: str):
        code, _, err = await self._run(["lxc-start", "-n", uuid])
        if code != 0:
            raise RuntimeError(f"LXC start failed: {err}")

    async def stop(self, uuid: str):
        code, _, err = await self._run(["lxc-stop", "-n", uuid])
        if code != 0:
            raise RuntimeError(f"LXC stop failed: {err}")

    async def restart(self, uuid: str):
        await self.stop(uuid)
        await asyncio.sleep(2)
        await self.start(uuid)

    async def status(self, uuid: str) -> Dict[str, Any]:
        code, out, _ = await self._run(["lxc-info", "-n", uuid, "-s"])
        state = out.strip().split(":", 1)[-1].strip() if code == 0 else "unknown"
        code2, ip_out, _ = await self._run(["lxc-info", "-n", uuid, "-i"])
        ip = ip_out.strip().split(":", 1)[-1].strip() if code2 == 0 else None
        return {"status": state or "unknown", "ip_address": ip}

    async def reinstall(self, uuid: str, os_template: Optional[str] = None) -> Dict[str, Any]:
        await self.delete(uuid)
        cfg = {"uuid": uuid, "os_template": os_template or "ubuntu-22.04", "ram": 1024, "cpu": 1}
        return await self.create(cfg)

    async def list_all(self) -> list:
        code, out, _ = await self._run(["lxc-ls", "-1"], timeout=15)
        if code != 0:
            return []
        return [{"uuid": name, "status": "known", "image": ""} for name in out.split()]

    async def backup(self, uuid: str) -> Dict[str, Any]:
        """Archive the container rootfs (plus its LXC config) into a tar.gz."""
        if not await self.exists(uuid):
            raise RuntimeError("container not found")
        from config import agent_settings
        backup_dir = os.path.join(agent_settings.BACKUP_PATH, uuid)
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"lxc-{uuid[:12]}-{int(asyncio.get_event_loop().time())}.tar.gz")
        rootfs = f"/var/lib/lxc/{uuid}/rootfs"
        cfg_file = f"/var/lib/lxc/{uuid}/config"
        cmd = ["tar", "-czf", path, "-C", rootfs, "."]
        if os.path.exists(cfg_file):
            cmd = ["tar", "-czf", path, "-C", "/var/lib/lxc", f"{uuid}/config",
                   "-C", rootfs, "."]
        code, out, err = await self._run(cmd, timeout=900)
        if code != 0:
            raise RuntimeError(f"LXC backup failed: {err or out}")
        size = os.path.getsize(path)
        logger.info(f"LXC backup {uuid} -> {path} ({size} bytes)")
        return {"file_path": path, "file_size": size}

    async def restore(self, uuid: str, file_path: str) -> None:
        """Stop, wipe and re-extract the rootfs from the backup tarball.

        Uses argv-only subprocess calls — the old `sh -c "rm -rf '{x}'/*"`
        construction was shell-injectable through crafted file names.
        """
        if not await self.exists(uuid):
            raise RuntimeError("container not found")
        rootfs = f"/var/lib/lxc/{uuid}/rootfs"
        await self._run(["lxc-stop", "-n", uuid, "-k"])
        # verify the tarball layout before destroying anything
        code, out, _ = await self._run(["tar", "-tzf", file_path, "./"], timeout=60)
        if code != 0:
            raise RuntimeError("backup archive is not a valid LXC rootfs tarball")
        # wipe the rootfs contents (not the dir itself) without a shell
        if os.path.isdir(rootfs):
            import shutil as _shutil
            for entry in os.listdir(rootfs):
                target = os.path.join(rootfs, entry)
                if os.path.isdir(target) and not os.path.islink(target):
                    await asyncio.to_thread(_shutil.rmtree, target, True)
                else:
                    try:
                        os.remove(target)
                    except FileNotFoundError:
                        pass
        code, _, err = await self._run(
            ["tar", "-xzf", file_path, "-C", rootfs], timeout=900)
        if code != 0:
            raise RuntimeError(f"LXC restore failed: {err}")
        code, _, err = await self._run(["lxc-start", "-n", uuid])
        if code != 0:
            raise RuntimeError(f"LXC start after restore failed: {err}")
        logger.info(f"LXC restore {uuid} from {file_path}")

    async def execute(self, uuid: str, command: str) -> str:
        code, out, err = await self._run(["lxc-attach", "-n", uuid, "--", "bash", "-c", command])
        return out if code == 0 else err

    async def get_stats(self, uuid: str) -> Dict[str, Any]:
        code, out, _ = await self._run(["lxc-info", "-n", uuid, "--stats"])
        return {"running": code == 0, "raw": out[:2000]}
