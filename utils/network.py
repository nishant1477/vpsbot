import asyncio
import socket
import ssl
import aiohttp
import psutil
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass
from pathlib import Path

@dataclass
class NetworkStats:
    bytes_sent: int
    bytes_recv: int
    packets_sent: int
    packets_recv: int
    errin: int
    errout: int
    dropin: int
    dropout: int

@dataclass
class NetworkInterface:
    name: str
    ipv4: List[str]
    ipv6: List[str]
    mac: str
    is_up: bool
    speed: int

async def check_tcp_connection(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False

async def check_http_endpoint(url: str, timeout: float = 10.0, expected_status: int = 200) -> Tuple[bool, Optional[int]]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.get(url) as resp:
                return resp.status == expected_status, resp.status
    except Exception:
        return False, None

async def measure_latency(host: str, port: int, attempts: int = 3) -> Optional[float]:
    latencies = []
    for _ in range(attempts):
        start = asyncio.get_event_loop().time()
        success = await check_tcp_connection(host, port, timeout=2.0)
        if success:
            latencies.append((asyncio.get_event_loop().time() - start) * 1000)
    return sum(latencies) / len(latencies) if latencies else None

async def resolve_hostname(hostname: str) -> List[str]:
    try:
        loop = asyncio.get_event_loop()
        info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
        return list(set(addr[4][0] for addr in info))
    except Exception:
        return []

def get_local_ips() -> List[str]:
    ips = []
    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith('127.'):
                ips.append(addr.address)
    return ips

def get_network_interfaces() -> List[NetworkInterface]:
    interfaces = []
    stats = psutil.net_if_stats()
    addrs = psutil.net_if_addrs()
    
    for name, stat in stats.items():
        if not stat.isup:
            continue
        ipv4 = []
        ipv6 = []
        mac = ""
        for addr in addrs.get(name, []):
            if addr.family == socket.AF_INET:
                ipv4.append(addr.address)
            elif addr.family == socket.AF_INET6:
                ipv6.append(addr.address)
            elif addr.family == psutil.AF_LINK:
                mac = addr.address
        interfaces.append(NetworkInterface(
            name=name,
            ipv4=ipv4,
            ipv6=ipv6,
            mac=mac,
            is_up=stat.isup,
            speed=stat.speed
        ))
    return interfaces

async def ping_host(host: str, count: int = 4, timeout: float = 1.0) -> Optional[Dict]:
    latencies = []
    for _ in range(count):
        try:
            start = asyncio.get_event_loop().time()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 80),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            latencies.append((asyncio.get_event_loop().time() - start) * 1000)
        except Exception:
            pass
    if not latencies:
        return None
    return {
        "min": min(latencies),
        "max": max(latencies),
        "avg": sum(latencies) / len(latencies),
        "packet_loss": ((count - len(latencies)) / count) * 100
    }

async def test_node_connectivity(host: str, port: int = 8080) -> Dict:
    results = {
        "tcp": False,
        "latency_ms": None,
        "http": False,
        "http_status": None
    }
    results["tcp"] = await check_tcp_connection(host, port)
    results["latency_ms"] = await measure_latency(host, port)
    http_ok, status = await check_http_endpoint(f"http://{host}:{port}/health")
    results["http"] = http_ok
    results["http_status"] = status
    return results

def get_network_stats() -> NetworkStats:
    io = psutil.net_io_counters()
    return NetworkStats(
        bytes_sent=io.bytes_sent,
        bytes_recv=io.bytes_recv,
        packets_sent=io.packets_sent,
        packets_recv=io.packets_recv,
        errin=io.errin,
        errout=io.errout,
        dropin=io.dropin,
        dropout=io.dropout
    )

def get_network_connections() -> List[Dict]:
    connections = []
    for conn in psutil.net_connections(kind='inet'):
        connections.append({
            "fd": conn.fd,
            "family": conn.family.name,
            "type": conn.type.name,
            "local_address": f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else None,
            "remote_address": f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else None,
            "status": conn.status,
            "pid": conn.pid
        })
    return connections

async def download_file(url: str, dest: Path, chunk_size: int = 8192) -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(chunk_size):
                        f.write(chunk)
                return True
    except Exception:
        return False

async def upload_file(url: str, file_path: Path, field_name: str = "file") -> bool:
    try:
        async with aiohttp.ClientSession() as session:
            with open(file_path, 'rb') as f:
                data = aiohttp.FormData()
                data.add_field(field_name, f, filename=file_path.name)
                async with session.post(url, data=data) as resp:
                    return resp.status == 200
    except Exception:
        return False