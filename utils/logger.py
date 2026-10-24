"""Arqon Bot — logging setup (stdlib based, no extra deps)."""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

from config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging() -> None:
    """Configure root logging once: colored console + rotating file."""
    global _configured
    if _configured:
        return

    level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    # Console
    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(console)

    # File (best-effort — never crash the bot because logging fails)
    try:
        log_path = settings.LOG_FILE
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.LOG_MAX_BYTES,
            backupCount=settings.LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        root.addHandler(file_handler)
    except Exception as exc:  # pragma: no cover
        print(f"[logger] Could not attach file handler: {exc}", file=sys.stderr)

    # Quiet noisy third-party loggers
    for noisy in ("discord", "discord.http", "aiosqlite", "asyncio", "urllib3", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Shared bot logger
bot_logger = get_logger("arqon.bot")


# ── Structured event helpers ──────────────────────────────────────────

def log_command(ctx) -> None:
    try:
        bot_logger.info(
            "CMD | user=%s(%s) guild=%s command=%s",
            getattr(ctx.author, "name", "?"),
            getattr(ctx.author, "id", "?"),
            getattr(getattr(ctx, "guild", None), "id", "DM"),
            getattr(getattr(ctx, "command", None), "qualified_name", "?"),
        )
    except Exception:
        pass


def log_error(message: str, exc: Exception = None) -> None:
    bot_logger.error("%s%s", message, f" | {exc}" if exc else "")


def log_info(message: str) -> None:
    bot_logger.info(message)


def log_warning(message: str) -> None:
    bot_logger.warning(message)


def log_vps_action(action: str, vps_id, user_id=None, node_id=None, extra: str = "") -> None:
    bot_logger.info("VPS | %s | vps=%s user=%s node=%s %s", action, vps_id, user_id, node_id, extra)


def log_node_event(action: str, node_id, extra: str = "") -> None:
    bot_logger.info("NODE | %s | node=%s %s", action, node_id, extra)


def log_security_event(action: str, guild_id, user_id=None, extra: str = "") -> None:
    bot_logger.warning("GUARDIAN | %s | guild=%s user=%s %s", action, guild_id, user_id, extra)


def log_ticket_event(action: str, ticket_id, user_id=None, extra: str = "") -> None:
    bot_logger.info("TICKET | %s | ticket=%s user=%s %s", action, ticket_id, user_id, extra)


def log_audit(action: str, actor_id, target=None, extra: str = "") -> None:
    bot_logger.info("AUDIT | %s | actor=%s target=%s %s", action, actor_id, target, extra)


def timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
