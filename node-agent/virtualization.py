"""Arqon Node Agent — virtualization capability detection (no external deps)."""
import asyncio
import enum
import shutil
from typing import List


class SupportedVirt(enum.Enum):
    KVM = "kvm"
    LXD_SUPPORTED = "lxd_supported"
    LXD_UNSUPPORTED = "lxd_unsupported"
    DOCKER = "docker"


async def _run(cmd: List[str]) -> bool:
    """Return True if the command exits 0."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
        return proc.returncode == 0
    except asyncio.TimeoutError:
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return False
    except (FileNotFoundError, Exception):
        if proc and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return False


def _which(binary: str) -> bool:
    return shutil.which(binary) is not None


async def detect_virtualization_support() -> List[SupportedVirt]:
    """Detect which virtualization backends this node supports."""
    supported: List[SupportedVirt] = []

    # Docker
    if _which("docker"):
        if await _run(["docker", "info"]):
            supported.append(SupportedVirt.DOCKER)

    # LXC (basic)
    if _which("lxc-create"):
        supported.append(SupportedVirt.LXD_UNSUPPORTED)

    # LXD
    if _which("lxd") or _which("lxc"):
        if await _run(["lxc", "version"]):
            supported.append(SupportedVirt.LXD_SUPPORTED)

    # KVM
    if _which("qemu-system-x86_64") or _which("virsh"):
        try:
            with open("/proc/cpuinfo", "r") as fh:
                proc_info = fh.read().lower()
            if "vmx" in proc_info or "svm" in proc_info:
                supported.append(SupportedVirt.KVM)
        except Exception:
            pass
        if SupportedVirt.KVM not in supported and _which("virsh"):
            if await _run(["virsh", "list"]):
                supported.append(SupportedVirt.KVM)

    return supported
