"""Arqon Bot — input validation helpers.

Every validator raises `ValidationError` with a human-friendly message that
cogs can surface directly to users via error embeds.
"""
import re
from typing import Optional

from config import settings, OS_TEMPLATES, VIRTUALIZATION_TYPES


class ValidationError(ValueError):
    """Raised when user input fails validation."""


_HOSTNAME_RE = re.compile(
    r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$", re.IGNORECASE
)


def validate_node_name(name: str) -> str:
    if not name or not name.strip():
        raise ValidationError("Node name cannot be empty.")
    name = name.strip()
    if len(name) < 2 or len(name) > 100:
        raise ValidationError("Node name must be 2-100 characters.")
    if not re.match(r"^[\w \-\.]+$", name):
        raise ValidationError("Node name may only contain letters, numbers, spaces, - _ .")
    return name


def validate_node_host(host: str) -> str:
    if not host or not host.strip():
        raise ValidationError("Node host cannot be empty.")
    host = host.strip()
    # hostname / ipv4 / ipv6
    ipv4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
    if ipv4.match(host):
        if any(int(o) > 255 for o in host.split(".")):
            raise ValidationError("Invalid IPv4 address.")
        return host
    if host.count(":") > 0:  # loose IPv6 check
        if not re.match(r"^[0-9a-fA-F:\.]+$", host):
            raise ValidationError("Invalid IPv6 address.")
        return host
    if not _HOSTNAME_RE.match(host) and not all(_HOSTNAME_RE.match(p) for p in host.split(".")):
        raise ValidationError("Host must be a valid hostname or IP address.")
    return host


def validate_node_port(port) -> int:
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValidationError("Port must be a number.")
    if not (1 <= port <= 65535):
        raise ValidationError("Port must be between 1 and 65535.")
    return port


def validate_vps_name(name: str) -> str:
    if not name or not name.strip():
        raise ValidationError("VPS name cannot be empty.")
    name = name.strip()
    if len(name) < 2 or len(name) > 64:
        raise ValidationError("VPS name must be 2-64 characters.")
    if not re.match(r"^[\w \-\.]+$", name):
        raise ValidationError("VPS name may only contain letters, numbers, spaces, - _ .")
    return name


def validate_vps_hostname(hostname: str) -> str:
    if not hostname or not hostname.strip():
        raise ValidationError("Hostname cannot be empty.")
    hostname = hostname.strip().lower()
    if len(hostname) > 253:
        raise ValidationError("Hostname is too long.")
    if not all(_HOSTNAME_RE.match(p) for p in hostname.split(".")):
        raise ValidationError("Hostname contains invalid characters.")
    return hostname


def validate_ram(ram) -> int:
    try:
        ram = int(ram)
    except (TypeError, ValueError):
        raise ValidationError("RAM must be a number (MB).")
    if ram < 128:
        raise ValidationError("RAM must be at least 128 MB.")
    if ram > 262144:
        raise ValidationError("RAM cannot exceed 256 GB (262144 MB).")
    return ram


def validate_cpu(cpu) -> int:
    try:
        cpu = int(cpu)
    except (TypeError, ValueError):
        raise ValidationError("CPU cores must be a number.")
    if cpu < 1:
        raise ValidationError("CPU must be at least 1 core.")
    if cpu > 64:
        raise ValidationError("CPU cannot exceed 64 cores.")
    return cpu


def validate_disk(disk) -> int:
    try:
        disk = int(disk)
    except (TypeError, ValueError):
        raise ValidationError("Disk must be a number (GB).")
    if disk < 5:
        raise ValidationError("Disk must be at least 5 GB.")
    if disk > 4096:
        raise ValidationError("Disk cannot exceed 4 TB (4096 GB).")
    return disk


def validate_virtualization(virt: str) -> str:
    virt = (virt or "").strip().lower()
    # accept common aliases
    aliases = {
        "lxc_lxd_supported": "lxd_supported",
        "lxc_lxd_unsupported": "lxd_unsupported",
        "lxc": "lxd_unsupported",
        "lxd": "lxd_supported",
    }
    virt = aliases.get(virt, virt)
    if virt not in VIRTUALIZATION_TYPES:
        raise ValidationError(
            f"Invalid virtualization type. Choose one of: {', '.join(VIRTUALIZATION_TYPES)}."
        )
    return virt


def validate_os_template(os_template: str) -> str:
    os_template = (os_template or "").strip().lower()
    if not os_template:
        raise ValidationError("OS template cannot be empty.")
    # Allow both the canonical ids and free-form template names used by agents
    if os_template in OS_TEMPLATES:
        return os_template
    if not re.match(r"^[\w\-\.\+/]+$", os_template):
        raise ValidationError("OS template contains invalid characters.")
    return os_template


def validate_ticket_subject(subject: str) -> str:
    if not subject or not subject.strip():
        raise ValidationError("Subject cannot be empty.")
    subject = subject.strip()
    if len(subject) < 4:
        raise ValidationError("Subject must be at least 4 characters.")
    if len(subject) > 200:
        raise ValidationError("Subject must be at most 200 characters.")
    return subject


def validate_ticket_description(description: str) -> str:
    if not description or not description.strip():
        raise ValidationError("Description cannot be empty.")
    description = description.strip()
    if len(description) < 10:
        raise ValidationError("Please describe your issue in at least 10 characters.")
    if len(description) > 2000:
        raise ValidationError("Description must be at most 2000 characters.")
    return description


def validate_discord_id(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValidationError("ID must be a number.")
    if not (17 <= len(str(value)) <= 20):
        raise ValidationError("That does not look like a valid Discord ID.")
    return value


def validate_port(port) -> int:
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValidationError("Port must be a number.")
    if not (1 <= port <= 65535):
        raise ValidationError("Port must be between 1 and 65535.")
    if port in (22, 80, 443):
        raise ValidationError(f"Port {port} is reserved for system use.")
    return port


def validate_username(username: str) -> str:
    if not username or not username.strip():
        raise ValidationError("Username cannot be empty.")
    username = username.strip()
    if not re.match(r"^[a-zA-Z0-9_\-\.]{3,32}$", username):
        raise ValidationError("Username must be 3-32 chars: letters, numbers, _ - . only.")
    return username


def validate_ssh_public_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        raise ValidationError("SSH key cannot be empty.")
    if not re.match(r"^(ssh-rsa|ssh-ed25519|ecdsa-sha2-\S+|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) ", key):
        raise ValidationError("That does not look like a valid SSH public key.")
    if len(key) > 4000:
        raise ValidationError("SSH key is too long.")
    return key
