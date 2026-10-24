"""Arqon Bot — general-purpose helpers."""
import uuid
import secrets
import string
import hashlib
import asyncio
import re
import json
from typing import Any, Callable, TypeVar, Optional, List, Dict, Awaitable, Union
from datetime import timedelta
from functools import wraps
import aiohttp
import psutil

T = TypeVar("T")


def generate_uuid() -> str:
    return str(uuid.uuid4())


def generate_short_id(length: int = 8) -> str:
    return secrets.token_urlsafe(length)[:length]


def generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_api_key(prefix: str = "arqon") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


# ── Password hashing (salted, verifiable) ────────────────────────────

def hash_password(password: str) -> str:
    """Return 'salt$hash' so it can be verified later (PBKDF2-SHA256, 100k iters)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()
    return f"{salt.hex()}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a 'salt$hash' string produced by hash_password."""
    try:
        if not hashed or "$" not in hashed:
            return False
        salt_hex, digest_hex = hashed.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()
        return secrets.compare_digest(digest, digest_hex)
    except (ValueError, TypeError):
        return False


def sanitize_input(text: str, max_length: int = 2000) -> str:
    text = re.sub(r"[@#`]", "", text or "")
    return text[:max_length]


def parse_time_string(time_str: str) -> Optional[int]:
    time_str = (time_str or "").lower().strip()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    total = 0
    current = ""
    for char in time_str:
        if char.isdigit():
            current += char
        elif char in multipliers and current:
            total += int(current) * multipliers[char]
            current = ""
    return total if total > 0 else None


def format_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    days, hours = divmod(total, 86400)
    hours, minutes = divmod(hours, 3600)
    minutes, seconds = divmod(minutes, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds:
        parts.append(f"{seconds}s")
    return " ".join(parts) if parts else "0s"


def truncate(text: str, length: int = 100, suffix: str = "...") -> str:
    if not text or len(text) <= length:
        return text or ""
    return text[: max(0, length - len(suffix))] + suffix


def chunk_list(lst: List[T], chunk_size: int) -> List[List[T]]:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


async def run_in_executor(func: Callable[..., T], *args, **kwargs) -> T:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0,
          exceptions: tuple = (Exception,)):
    """Async retry decorator with exponential backoff."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            current_delay = delay
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts - 1:
                        raise
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            raise last_exc  # pragma: no cover
        return wrapper
    return decorator


class RateLimiter:
    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self.calls: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            self.calls = [t for t in self.calls if now - t < self.period]
            if len(self.calls) >= self.max_calls:
                sleep_time = self.period - (now - self.calls[0])
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
            self.calls.append(now)


def paginate(items: List[T], page: int, per_page: int = 10) -> List[T]:
    start = (max(1, page) - 1) * per_page
    return items[start:start + per_page]


async def fetch_json(session: aiohttp.ClientSession, url: str, **kwargs) -> Dict:
    async with session.get(url, **kwargs) as resp:
        resp.raise_for_status()
        return await resp.json()


async def post_json(session: aiohttp.ClientSession, url: str, data: Dict = None, **kwargs) -> Dict:
    async with session.post(url, json=data, **kwargs) as resp:
        resp.raise_for_status()
        return await resp.json()


def get_system_info() -> Dict[str, Any]:
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    return {
        "cpu_count": psutil.cpu_count(),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory": {
            "total": mem.total, "available": mem.available,
            "used": mem.used, "percent": mem.percent,
        },
        "disk": {
            "total": disk.total, "used": disk.used,
            "free": disk.free, "percent": disk.percent,
        },
        "network": {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv},
        "boot_time": psutil.boot_time(),
    }


def is_valid_hostname(hostname: str) -> bool:
    if not hostname or len(hostname) > 255:
        return False
    if hostname.endswith("."):
        hostname = hostname[:-1]
    allowed = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$", re.IGNORECASE)
    return all(allowed.match(x) for x in hostname.split("."))


def is_valid_ip(ip: str) -> bool:
    import ipaddress
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def parse_size(size_str: str) -> Optional[int]:
    size_str = (size_str or "").upper().strip()
    multipliers = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    if size_str and size_str[-1] in multipliers:
        try:
            return int(size_str[:-1]) * multipliers[size_str[-1]]
        except ValueError:
            return None
    try:
        return int(size_str)
    except (ValueError, TypeError):
        return None


def format_dict_for_display(data: Dict, indent: int = 2) -> str:
    return json.dumps(data, indent=indent, default=str)
