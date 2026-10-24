import discord
from discord import Color, Embed
from typing import Optional, List, Dict, Any
from datetime import datetime
import math

from utils.emoji import E

Arqon_PURPLE = Color(0x9b59b6)
Arqon_DARK_PURPLE = Color(0x8e44ad)
Arqon_LIGHT_PURPLE = Color(0xb794d6)
Arqon_SUCCESS = Color(0x2ecc71)
Arqon_ERROR = Color(0xe74c3c)
Arqon_WARNING = Color(0xf39c12)
Arqon_INFO = Color(0x3498db)
Arqon_GRAY = Color(0x95a5a6)

def base_embed(
    title: str = "",
    description: str = "",
    color: Color = Arqon_PURPLE,
    footer: str = "Arqon Host VPS Bot • Premium Hosting",
    timestamp: bool = True,
    thumbnail: Optional[str] = None,
    image: Optional[str] = None,
    author: Optional[Dict[str, str]] = None,
    fields: Optional[List[Dict[str, Any]]] = None
) -> Embed:
    embed = Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.utcnow() if timestamp else None
    )
    embed.set_footer(text=footer)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    if author:
        embed.set_author(name=author.get("name", ""), icon_url=author.get("icon_url"), url=author.get("url"))
    if fields:
        for field in fields:
            embed.add_field(
                name=field.get("name", ""),
                value=field.get("value", ""),
                inline=field.get("inline", True)
            )
    return embed

def success_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Arqon_SUCCESS, **kwargs)

def error_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Arqon_ERROR, **kwargs)

def warning_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Arqon_WARNING, **kwargs)

def info_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Arqon_INFO, **kwargs)

def purple_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Arqon_PURPLE, **kwargs)

def ticket_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Arqon_PURPLE, **kwargs)

def vps_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Color(0x8e44ad), **kwargs)

def node_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Color(0x2c3e50), **kwargs)

def minecraft_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Color(0x27ae60), **kwargs)

def backup_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Color(0x9b59b6), **kwargs)

def monitoring_embed(title: str, description: str, **kwargs) -> Embed:
    return base_embed(title, description, Color(0xe67e22), **kwargs)

def format_bytes(bytes_value: int) -> str:
    if bytes_value == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = int(math.floor(math.log(bytes_value, 1024)))
    size = round(bytes_value / (1024 ** i), 2)
    return f"{size} {units[i]}"

def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"

def create_progress_bar(current: int, maximum: int, length: int = 10, fill: str = "▰", empty: str = "▱") -> str:
    if maximum == 0:
        return empty * length
    filled = int((current / maximum) * length)
    return fill * filled + empty * (length - filled)

def status_indicator(online: bool) -> str:
    return "🟢" if online else "🔴"

def format_percentage(current: int, maximum: int) -> str:
    if maximum == 0:
        return "0%"
    return f"{(current / maximum) * 100:.1f}%"


def loading_embed(text: str = "Working on it…", title: str = None, color=None) -> Embed:
    """Consistent animated-feel loading card (pair it with message edits)."""
    return Embed(
        title=title or f"{E('loading')} Please wait…",
        description=f"{E('gear')} {text}",
        color=color or Arqon_PURPLE,
        timestamp=datetime.utcnow(),
    )