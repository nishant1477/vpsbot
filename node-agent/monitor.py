"""Arqon Node Agent — system monitoring (non-blocking).

A single shared SystemMonitor instance is created at import time and used by
both agent.py (background sampling loop) and api.py (/stats endpoint), so
CPU sampling windows and network deltas are consistent everywhere.
"""
import asyncio
import psutil
import logging
import time
from typing import Dict, List, Any

from config import agent_settings
from virtualization import detect_virtualization_support

logger = logging.getLogger("node-agent.monitor")


class SystemMonitor:
    def __init__(self):
        self.running = False
        self.task = None
        self._cpu_percent: float = 0.0
        self._last_net = psutil.net_io_counters()
        self._stats_lock = asyncio.Lock()
        self._vps_count_cache: int = 0
        self._vps_count_at: float = 0.0

    async def start(self):
        self.running = True
        # prime the cpu sampling cache (interval=None returns since-last-call)
        psutil.cpu_percent(interval=None)
        self.task = asyncio.create_task(self.monitor_loop())
        logger.info("System monitor started")

    async def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("System monitor stopped")

    async def monitor_loop(self):
        while self.running:
            try:
                self._cpu_percent = psutil.cpu_percent(interval=None)
            except Exception as exc:
                logger.error(f"Monitor error: {exc}")
            await asyncio.sleep(agent_settings.MONITOR_INTERVAL)

    async def _run_cmd(self, *cmd: str, timeout: int = 8) -> str:
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return stdout.decode(errors="replace").strip()
        except asyncio.TimeoutError:
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return ""
        except (FileNotFoundError, Exception):
            if proc and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return ""

    async def get_vps_count(self) -> int:
        # Cached: this spawns up to 4 subprocesses; do not run it on every poll.
        now = time.monotonic()
        if now - self._vps_count_at < 60:
            return self._vps_count_cache
        count = 0
        out = await self._run_cmd("docker", "ps", "-q")
        count += len([l for l in out.splitlines() if l.strip()])
        out = await self._run_cmd("lxc-ls", "--active")
        count += len([l for l in out.splitlines() if l.strip()])
        out = await self._run_cmd("lxc", "list", "--format=csv", "-c=n", "status=running")
        count += len([l for l in out.splitlines() if l.strip()])
        out = await self._run_cmd("virsh", "list", "--state-running", "--name")
        count += len([l for l in out.splitlines() if l.strip()])
        self._vps_count_cache = count
        self._vps_count_at = now
        return count

    async def get_stats(self) -> Dict[str, Any]:
        """Stats payload in the exact shape the bot expects:
        {node, stats: {cpu:{percent,cores}, ram:{total,used,percent},
                       disk:{total,used,percent}, network:{rx,tx},
                       vps_count, loadavg, uptime, version}}"""
        # Lock: concurrent /stats calls must not interleave the network
        # delta bookkeeping (rx/tx are "bytes since last get_stats call").
        async with self._stats_lock:
            cpu_percent = self._cpu_percent or psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()

            rx_delta = max(0, net.bytes_recv - self._last_net.bytes_recv)
            tx_delta = max(0, net.bytes_sent - self._last_net.bytes_sent)
            self._last_net = net

            vps_count = await self.get_vps_count()
            uptime = int(time.time() - psutil.boot_time())

            from config import AGENT_VERSION
            return {
                "node": agent_settings.NODE_NAME,
                "stats": {
                    "cpu": {
                        "percent": cpu_percent,
                        "cores": psutil.cpu_count() or 1,
                    },
                    "ram": {
                        "total": mem.total,
                        "used": mem.used,
                        "percent": mem.percent,
                    },
                    "disk": {
                        "total": disk.total,
                        "used": disk.used,
                        "percent": disk.percent,
                    },
                    "network": {
                        "rx": rx_delta,
                        "tx": tx_delta,
                    },
                    "loadavg": list(psutil.getloadavg()) if hasattr(psutil, "getloadavg") else [0, 0, 0],
                    "uptime": uptime,
                    "vps_count": vps_count,
                    "version": AGENT_VERSION,  # bot heartbeat reads version inside stats
                },
            }

    async def get_virtualization_support(self) -> List:
        return await detect_virtualization_support()


# Shared singleton — imported by both agent.py and api.py so there is exactly
# one sampling loop and one set of counters for the whole process.
system_monitor = SystemMonitor()
