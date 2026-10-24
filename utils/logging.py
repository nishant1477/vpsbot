"""Compatibility shim.

Historically this module pulled in loguru (not in requirements) and conflicted
with stdlib `logging`. It now simply re-exports the project logger so legacy
imports (`from utils.logging import get_logger`) keep working.
"""
from utils.logger import (  # noqa: F401
    setup_logging,
    get_logger,
    bot_logger,
    log_command,
    log_error,
    log_info,
    log_warning,
    log_vps_action,
    log_node_event,
    log_security_event,
    log_ticket_event,
    log_audit,
    timestamp,
)
