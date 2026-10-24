"""Arqon Node Agent — KVM backend (qemu-img + virsh, with cloud-init support)."""
import asyncio
import logging
import os
import re
import shutil
from typing import Dict, Any, Optional
from xml.sax.saxutils import escape as _xml_escape, quoteattr

logger = logging.getLogger("node-agent.kvm")

# uuid guard used by every KVM path — mirrors the API-level _safe_uuid so the
# backend stays safe even if a future route forgets to validate.
_UUID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class KVMManager:
    def __init__(self):
        self._bridge = os.getenv("NETWORK_BRIDGE", "arqonbr0")
        self._images_dir = os.getenv("KVM_IMAGES_DIR", "/var/lib/libvirt/images")
        self._cloud_init = os.getenv("KVM_CLOUD_IMAGE", "/var/lib/libvirt/images/cloud-init.img")
        # Base image used as qcow2 backing file. Without it a fresh disk has no
        # OS and the VM can never boot — treat a missing template as an error.
        self._template_dir = os.getenv("KVM_TEMPLATE_DIR", "/var/lib/arqon/templates")
        self._templates = {
            "ubuntu-22.04": "ubuntu-22.04.qcow2",
            "ubuntu-24.04": "ubuntu-24.04.qcow2",
            "debian-11": "debian-11.qcow2",
            "debian-12": "debian-12.qcow2",
            "almalinux-9": "almalinux-9.qcow2",
            "rockylinux-9": "rockylinux-9.qcow2",
            "alpine-3.19": "alpine-3.19.qcow2",
        }

    async def _run(self, cmd: list, timeout: int = 300) -> tuple:
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

    async def exists(self, uuid: str) -> bool:
        code, out, _ = await self._run(["virsh", "dominfo", uuid])
        return code == 0

    async def create(self, config: Dict[str, Any]) -> Dict[str, Any]:
        uuid = config["uuid"]
        if not _UUID_RE.fullmatch(uuid):
            raise RuntimeError("invalid uuid (must match [A-Za-z0-9][A-Za-z0-9_-]{0,63})")
        os.makedirs(self._images_dir, exist_ok=True)
        disk_path = os.path.join(self._images_dir, f"{uuid}.qcow2")

        template = config.get("os_template", "ubuntu-22.04")
        base_image = os.path.join(self._template_dir, self._templates.get(template, ""))
        if not os.path.isfile(base_image):
            raise RuntimeError(
                f"no KVM template image for '{template}' — install a cloud image at "
                f"{base_image} (or set KVM_TEMPLATE_DIR) so VMs can actually boot")

        # COW backed by the template: boots a real OS, keeps template pristine.
        code, out, err = await self._run(
            ["qemu-img", "create", "-f", "qcow2", "-b", base_image, "-F", "qcow2",
             disk_path, f"{config.get('disk', 10)}G"])
        if code != 0:
            raise RuntimeError(f"Disk create failed: {err}")

        xml = self._generate_xml(config, disk_path)
        xml_path = f"/tmp/{uuid}.xml"
        with open(xml_path, "w") as fh:
            fh.write(xml)

        code, out, err = await self._run(["virsh", "define", xml_path])
        if code != 0:
            raise RuntimeError(f"VM define failed: {err}")

        code, out, err = await self._run(["virsh", "start", uuid])
        if code != 0:
            raise RuntimeError(f"VM start failed: {err}")

        logger.info(f"Created KVM VM {uuid}")
        return {"status": "running", "ip_address": None, "message": "vm created"}

    def _generate_xml(self, config: Dict[str, Any], disk_path: str) -> str:
        uuid = config["uuid"]
        # every externally-influenced value is XML-escaped / attribute-quoted —
        # an unescaped name once allowed injecting extra <device> elements.
        name = _xml_escape(uuid)
        seed_disk = ""
        if os.path.exists(self._cloud_init):
            seed_disk = f"""
    <disk type='file' device='disk'>
      <driver name='qemu' type='raw'/>
      <source file={quoteattr(self._cloud_init)}/>
      <target dev='vdb' bus='virtio'/>
    </disk>"""
        ram = int(config.get("ram", 1024))
        cpu = int(config.get("cpu", 1))
        return f"""<domain type='kvm'>
  <name>{name}</name>
  <memory unit='MiB'>{ram}</memory>
  <vcpu>{cpu}</vcpu>
  <os>
    <type arch='x86_64' machine='pc-q35-6.2'>hvm</type>
    <boot dev='hd'/>
  </os>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file={quoteattr(disk_path)}/>
      <target dev='vda' bus='virtio'/>
    </disk>{seed_disk}
    <interface type='bridge'>
      <source bridge={quoteattr(self._bridge)}/>
      <model type='virtio'/>
    </interface>
    <console type='pty'/>
  </devices>
</domain>"""

    async def delete(self, uuid: str):
        await self._run(["virsh", "destroy", uuid])
        await self._run(["virsh", "undefine", uuid, "--remove-all-storage"])
        logger.info(f"Deleted KVM VM {uuid}")

    async def start(self, uuid: str):
        code, _, err = await self._run(["virsh", "start", uuid])
        if code != 0:
            raise RuntimeError(f"VM start failed: {err}")

    async def stop(self, uuid: str):
        await self._run(["virsh", "shutdown", uuid])
        await asyncio.sleep(5)
        await self._run(["virsh", "destroy", uuid])

    async def restart(self, uuid: str):
        code, _, _ = await self._run(["virsh", "reboot", uuid])
        if code != 0:
            await self.stop(uuid)
            await asyncio.sleep(2)
            await self.start(uuid)

    async def status(self, uuid: str) -> Dict[str, Any]:
        code, out, _ = await self._run(["virsh", "domstate", uuid])
        state = out.strip() if code == 0 else "unknown"
        return {"status": state.lower(), "ip_address": None}

    async def reinstall(self, uuid: str, os_template: Optional[str] = None) -> Dict[str, Any]:
        await self.delete(uuid)
        cfg = {"uuid": uuid, "os_template": os_template or "ubuntu-22.04",
               "disk": 10, "ram": 1024, "cpu": 1}
        return await self.create(cfg)

    async def list_all(self) -> list:
        code, out, _ = await self._run(["virsh", "list", "--all", "--name"], timeout=30)
        if code != 0:
            return []
        return [{"uuid": name, "status": "known", "image": "kvm"}
                for name in out.splitlines() if name.strip()]

    async def backup(self, uuid: str) -> Dict[str, Any]:
        """qemu-img convert the VM disk into a compact backup image."""
        if not await self.exists(uuid):
            raise RuntimeError("VM not found")
        from config import agent_settings
        backup_dir = os.path.join(agent_settings.BACKUP_PATH, uuid)
        os.makedirs(backup_dir, exist_ok=True)
        path = os.path.join(backup_dir, f"kvm-{uuid[:12]}-{int(asyncio.get_event_loop().time())}.qcow2")
        disk_path = os.path.join(self._images_dir, f"{uuid}.qcow2")
        if not os.path.isfile(disk_path):
            raise RuntimeError(f"disk image not found: {disk_path}")
        code, out, err = await self._run(
            ["qemu-img", "convert", "-O", "qcow2", disk_path, path], timeout=1800)
        if code != 0:
            raise RuntimeError(f"KVM backup failed: {err or out}")
        size = os.path.getsize(path)
        logger.info(f"KVM backup {uuid} -> {path} ({size} bytes)")
        return {"file_path": path, "file_size": size}

    async def restore(self, uuid: str, file_path: str) -> None:
        """Destroy the VM, replace its disk with the backup, start again."""
        disk_path = os.path.join(self._images_dir, f"{uuid}.qcow2")
        if not os.path.isfile(disk_path):
            raise RuntimeError(f"disk image not found: {disk_path}")
        await self._run(["virsh", "destroy", uuid])
        code, out, err = await self._run(
            ["qemu-img", "convert", "-O", "qcow2", file_path, disk_path], timeout=1800)
        if code != 0:
            raise RuntimeError(f"KVM restore failed: {err or out}")
        code, _, err = await self._run(["virsh", "start", uuid])
        if code != 0:
            raise RuntimeError(f"KVM start after restore failed: {err}")
        logger.info(f"KVM restore {uuid} from {file_path}")

    async def execute(self, uuid: str, command: str) -> str:
        return "KVM guest agent required for command execution"

    async def get_stats(self, uuid: str) -> Dict[str, Any]:
        code, out, _ = await self._run(["virsh", "domstats", uuid, "--cpu", "--memory"])
        return {"running": code == 0, "raw": out[:2000]}
