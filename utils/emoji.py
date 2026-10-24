"""Arqon Bot — custom emoji system.

Central emoji registry used by every cog. Each logical emoji has:

1. a **unicode fallback** (always works, even in DMs or if the custom emoji
   gets deleted),
2. an optional **Discord custom emoji** override, written as ``<:name:id>``
   or ``<a:name:id>`` (animated).

Overrides are resolved in this order (last one wins):

* environment variables ``EMOJI_<NAME>``  (set in ``.env``, e.g. ``EMOJI_SUCCESS=<:arqon_check:1234567890123456789>``)
* ``data/emojis.json``                    (editable live with /editemoji — no restart needed)

Usage in cogs::

    from utils.emoji import E
    embed.title = f"{E('vps')} VPS Panel"
    # E("success") -> "<:arqon_check:123...>" if configured, else "✅"
"""
import os
import json
import re
import asyncio
from pathlib import Path
from typing import Dict, Optional

# ──────────────────────────────────────────────────────────────────────
# Default palette:  name -> unicode fallback
# ──────────────────────────────────────────────────────────────────────
DEFAULT_EMOJIS: Dict[str, str] = {
    # brand
    "brand": "🅰️",
    "sparkles": "✨",
    "crown": "👑",
    "star": "⭐",
    "fire": "🔥",
    "gem": "💎",
    "rocket": "🚀",
    "zap": "⚡",
    # status
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "loading": "⏳",
    "gear": "⚙️",
    "lock": "🔒",
    "unlock": "🔓",
    "shield": "🛡️",
    # hosting
    "vps": "🖥️",
    "server": "🗄️",
    "node": "🌐",
    "cpu": "🧠",
    "ram": "🧮",
    "disk": "💾",
    "network": "📡",
    "cloud": "☁️",
    "terminal": "⌨️",
    "os": "💿",
    "package": "📦",
    # plans / billing
    "plans": "🛒",
    "invite": "📨",
    "money": "💰",
    "wallet": "💼",
    "invoice": "🧾",
    "gift": "🎁",
    "trophy": "🏆",
    "medal": "🏅",
    # users
    "user": "👤",
    "users": "👥",
    "admin": "🛡️",
    "bot": "🤖",
    # help / misc
    "help": "❓",
    "book": "📖",
    "tools": "🛠️",
    "chart": "📊",
    "backup": "🗜️",
    "ticket": "🎫",
    "minecraft": "⛏️",
    "ai": "🧠",
    "link": "🔗",
    "clock": "🕐",
    "ping": "📶",
    "flame_green": "🟢",
    "flame_red": "🔴",
    "flame_yellow": "🟡",
    "home": "🏠",
    "list": "📋",
    "search": "🔍",
    "settings": "🔧",
    "power": "🔌",
    "trash": "🗑️",
    "refresh": "🔄",
    "key": "🔑",
    "globe": "🌍",
    "bell": "🔔",
}

_CUSTOM_RE = re.compile(r"^<a?:(\w+):(\d{15,25})>$")

_DATA_DIR = Path(os.getenv("ARQON_DATA_DIR", "./data"))
_JSON_PATH = _DATA_DIR / "emojis.json"

# runtime state
_json_overrides: Dict[str, str] = {}
_env_overrides: Dict[str, str] = {}
_lock = asyncio.Lock()


# ──────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────
def is_custom_emoji(text: str) -> bool:
    """True if *text* looks like a Discord custom emoji ``<:name:id>``."""
    return bool(_CUSTOM_RE.match((text or "").strip()))


def _normalize(value: Optional[str]) -> Optional[str]:
    """Return a cleaned emoji string, or None if invalid/empty."""
    if not value:
        return None
    value = str(value).strip().strip('"').strip("'")
    if not value:
        return None
    if is_custom_emoji(value):
        return value
    # accept a bare unicode emoji (single grapheme-ish, len <= 8)
    if len(value) <= 8 and not value.startswith("<"):
        return value
    return None


def _load_env_overrides() -> None:
    """Pick up EMOJI_* variables from the environment / .env."""
    global _env_overrides
    try:
        # make sure .env values are visible even if we load before config.py
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except Exception:
        pass
    found: Dict[str, str] = {}
    for key, val in os.environ.items():
        if not key.upper().startswith("EMOJI_"):
            continue
        name = key[6:].lower()
        cleaned = _normalize(val)
        if name and cleaned:
            found[name] = cleaned
    _env_overrides = found


def _load_json_overrides() -> None:
    global _json_overrides
    try:
        if _JSON_PATH.exists():
            data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                found: Dict[str, str] = {}
                for k, v in data.items():
                    cleaned = _normalize(v)
                    if cleaned:
                        found[str(k).lower()] = cleaned
                _json_overrides = found
                return
    except Exception:
        pass
    _json_overrides = {}


def reload_emojis() -> Dict[str, str]:
    """Re-read env + JSON overrides. Returns the active override map."""
    _load_env_overrides()
    _load_json_overrides()
    return active_overrides()


def active_overrides() -> Dict[str, str]:
    """Merged override map (env < json)."""
    merged = dict(_env_overrides)
    merged.update(_json_overrides)
    return merged


async def set_override(name: str, emoji: str) -> bool:
    """Persist a custom override for *name* into data/emojis.json."""
    name = name.lower().strip()
    cleaned = _normalize(emoji)
    if not name or name not in DEFAULT_EMOJIS:
        return False
    async with _lock:
        # None / "reset" clears the override
        if cleaned is None or emoji.strip().lower() in {"reset", "none", "default"}:
            _json_overrides.pop(name, None)
        else:
            _json_overrides[name] = cleaned
        try:
            _DATA_DIR.mkdir(parents=True, exist_ok=True)
            _JSON_PATH.write_text(
                json.dumps(_json_overrides, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            return False
    return True


async def reset_override(name: str) -> bool:
    return await set_override(name, "reset")


def E(name: str, fallback: str = "❔") -> str:
    """Resolve a logical emoji name to its best representation.

    Priority: JSON override > EMOJI_* env var > unicode fallback.
    Never raises — unknown names return *fallback* (default "❔").
    """
    name = name.lower().strip()
    if name in _json_overrides:
        return _json_overrides[name]
    if name in _env_overrides:
        return _env_overrides[name]
    return DEFAULT_EMOJIS.get(name, fallback)


def emojilist() -> Dict[str, str]:
    """Every logical emoji with its currently active value."""
    out = {}
    for name in sorted(DEFAULT_EMOJIS):
        out[name] = E(name)
    return out


# load once at import (env is read by config.py before cogs import us)
reload_emojis()
