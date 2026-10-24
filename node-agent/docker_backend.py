"""Arqon Node Agent — Docker backend (blocking docker-py calls run in executor)."""
import asyncio
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("node-agent.docker")

IMAGE_MAP = {
    "ubuntu-22.04": "ubuntu:22.04",
    "ubuntu-24.04": "ubuntu:24.04",
    "ubuntu22.04": "ubuntu:22.04",
    "debian-11": "debian:11",
    "debian-12": "debian:12",
    "almalinux-9": "almalinux:9",
    "rockylinux-9": "rockylinux:9",
    "centos-9": "quay.io/centos/centos:stream9",
    "alpine-3.19": "alpine:3.19",
}


class DockerManager:
    def __init__(self):
        self.client = None

    async def _get_client(self):
        if self.client is None:
            def _connect():
                import docker
                return docker.from_env()
            try:
                self.client = await asyncio.get_running_loop().run_in_executor(None, _connect)
            except Exception as exc:
                logger.error(f"Docker not available: {exc}")
                raise RuntimeError("Docker not available")
        return self.client

    # ── helpers ──────────────────────────────────────────────────────

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def _get_container(self, uuid: str):
        client = await self._get_client()
        try:
            return await self._run(client.containers.get, uuid)
        except Exception:
            return None

    # ── interface methods ────────────────────────────────────────────

    async def exists(self, uuid: str) -> bool:
        container = await self._get_container(uuid)
        return container is not None

    async def create(self, config: Dict[str, Any]) -> Dict[str, Any]:
        client = await self._get_client()
        # Unknown templates pass through as image names (keeps custom images
        # from reinstall() intact instead of silently replacing them).
        image = IMAGE_MAP.get(config.get("os_template", ""), config.get("os_template") or "ubuntu:22.04")
        try:
            # Stock distro images ship neither systemd (/sbin/init) nor sshd.
            # Boot with a keep-alive command so the container stays up and the
            # agent's `execute` channel works, and apply the root password via
            # chpasswd so credentials are real.
            keepalive = ["/bin/sh", "-c", "while :; do sleep 3600; done"]

            ports = None
            ssh_port = config.get("ssh_port") or 22
            if ssh_port != 22:
                # Publishing host port 22 for every VPS would hijack the host's
                # own sshd and collide on the second create — only publish when
                # the user explicitly chose a different port.
                ports = {"22/tcp": ssh_port}

            def _create():
                return client.containers.run(
                    image,
                    detach=True,
                    name=config["uuid"],
                    hostname=config.get("hostname", "vps"),
                    mem_limit=f"{config.get('ram', 1024)}m",
                    nano_cpus=int(config.get("cpu", 1)) * 1_000_000_000,
                    ports=ports,
                    stdin_open=True,
                    tty=True,
                    privileged=False,
                    command=keepalive,
                )
            container = await self._run(_create)
            ip = ""
            try:
                await self._run(lambda: container.reload())
                nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
                ip = next(iter(nets.values()), {}).get("IPAddress", "")
            except Exception:
                pass

            # Apply the requested root password inside the container (best effort).
            root_password = config.get("root_password")
            if root_password:
                try:
                    await self._run(
                        container.exec_run,
                        ["sh", "-c", 'echo "root:$Arqon_PW" | chpasswd'],
                        environment=[f"Arqon_PW={root_password}"],
                    )
                except Exception as exc:
                    logger.warning(f"chpasswd failed on {config['uuid']}: {exc}")

            logger.info(f"Created Docker container {config['uuid']}")
            return {"status": "running", "ip_address": ip, "message": "container created"}
        except Exception as exc:
            logger.error(f"Docker create failed: {exc}")
            raise

    async def delete(self, uuid: str):
        container = await self._get_container(uuid)
        if not container:
            return
        try:
            await self._run(container.stop, timeout=10)
        except Exception:
            pass
        await self._run(container.remove, force=True)
        logger.info(f"Deleted Docker container {uuid}")

    async def start(self, uuid: str):
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")
        await self._run(container.start)

    async def stop(self, uuid: str):
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")
        await self._run(container.stop, timeout=30)

    async def restart(self, uuid: str):
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")
        await self._run(container.restart, timeout=30)

    async def status(self, uuid: str) -> Dict[str, Any]:
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")
        await self._run(container.reload)
        nets = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        ip = next(iter(nets.values()), {}).get("IPAddress", "")
        return {"status": container.status, "ip_address": ip}

    async def reinstall(self, uuid: str, os_template: Optional[str] = None) -> Dict[str, Any]:
        """Recreate the container with a fresh filesystem."""
        old = await self._get_container(uuid)
        cfg = {"uuid": uuid, "os_template": os_template or "ubuntu-22.04",
               "hostname": uuid[:12], "ram": 1024, "cpu": 1, "ssh_port": 22}
        if old:
            try:
                await self._run(lambda: old.reload())
                image = old.image.tags[0] if old.image.tags else None
                if image:
                    cfg["os_template"] = image  # keep custom images
                await self._run(old.stop, timeout=10)
                await self._run(old.remove, force=True)
            except Exception:
                pass
        return await self.create(cfg)

    async def list_all(self) -> list:
        client = await self._get_client()
        def _list():
            return [c for c in client.containers.list(all=True)]
        containers = await self._run(_list)
        out = []
        for c in containers:
            try:
                out.append({"uuid": c.name, "status": c.status,
                            "image": ", ".join(c.image.tags) if c.image.tags else ""})
            except Exception:
                continue
        return out

    async def backup(self, uuid: str) -> Dict[str, Any]:
        """Export the container filesystem to a tarball under BACKUP_PATH."""
        import os
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")
        from config import agent_settings
        backup_dir = os.path.join(agent_settings.BACKUP_PATH, uuid)
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"docker-{uuid[:12]}-{int(asyncio.get_event_loop().time())}.tar")

        def _export():
            # container.export() streams a tar of the rootfs — writes via executor.
            with open(path, "wb") as fh:
                for chunk in container.export():
                    fh.write(chunk)
            return os.path.getsize(path)
        size = await self._run(_export)
        logger.info(f"Docker backup {uuid} -> {path} ({size} bytes)")
        return {"file_path": path, "file_size": size}

    async def restore(self, uuid: str, file_path: str) -> None:
        """Import the backed-up filesystem as an image and rebuild the container
        from the last known create config."""
        import os
        client = await self._get_client()
        old = await self._get_container(uuid)
        if old:
            try:
                await self._run(old.stop, timeout=10)
                await self._run(old.remove, force=True)
            except Exception:
                pass

        restore_image = f"arqon-restore:{uuid[:12]}"
        repo, tag = restore_image.split(":", 1)

        def _import():
            # import_image_from_data is the supported streaming import API
            with open(file_path, "rb") as fh:
                client.api.import_image_from_data(fh.read(), repository=repo, tag=tag)
        await self._run(_import)

        cfg = {"uuid": uuid, "os_template": restore_image, "hostname": uuid[:12],
               "ram": 1024, "cpu": 1, "ssh_port": 22}
        await self.create(cfg)
        logger.info(f"Docker restore {uuid} from {os.path.basename(file_path)}")

    async def execute(self, uuid: str, command: str) -> str:
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")
        result = await self._run(container.exec_run, ["sh", "-c", command])
        return result.output.decode(errors="replace") if result.output else ""

    async def get_stats(self, uuid: str) -> Dict[str, Any]:
        container = await self._get_container(uuid)
        if not container:
            raise RuntimeError("container not found")

        def _stats():
            return container.stats(stream=False)

        stats = await self._run(_stats)
        try:
            cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - \
                        stats["precpu_stats"]["cpu_usage"]["total_usage"]
            system_delta = stats["cpu_stats"]["system_cpu_usage"] - \
                           stats["precpu_stats"]["system_cpu_usage"]
            percpu = stats["cpu_stats"]["cpu_usage"].get("percpu_usage") or []
            cpu_percent = (cpu_delta / system_delta) * 100 * len(percpu) if system_delta > 0 and percpu else 0.0
        except (KeyError, ZeroDivisionError, TypeError):
            cpu_percent = 0.0

        mem_usage = stats.get("memory_stats", {}).get("usage", 0)
        mem_limit = stats.get("memory_stats", {}).get("limit", 0)

        return {
            "cpu_percent": cpu_percent,
            "memory_used": mem_usage,
            "memory_limit": mem_limit,
            "running": container.status == "running",
        }
