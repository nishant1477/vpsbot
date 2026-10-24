"""
Arqon Bot — Unified database models.

Single source of truth for the schema. All cogs, the dashboard API and the
bot core import models from this module. `VPSServer` is provided as a
backwards-compatible alias of `VPSInstance`.
"""
import os
import enum
import uuid as uuid_lib
from datetime import datetime
from typing import Optional, AsyncGenerator

from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey,
    Enum, BigInteger, JSON, Index, UniqueConstraint, select, func,
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON

from config import settings

Base = declarative_base()


def _json_default():
    return {}


def _json_list_default():
    return []


# ─────────────────────────────── Enums ───────────────────────────────

class PermissionLevel(enum.IntEnum):
    USER = 0
    CUSTOMER = 1
    VPS_USER = 2
    PREMIUM = 3
    STAFF = 4
    ADMIN = 5
    OWNER = 6


class VirtualizationType(enum.Enum):
    KVM = "kvm"
    LXD_SUPPORTED = "lxd_supported"
    LXD_UNSUPPORTED = "lxd_unsupported"
    DOCKER = "docker"


class NodeStatus(enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"
    ERROR = "error"


class VPSStatus(enum.Enum):
    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    RESTARTING = "restarting"
    REINSTALLING = "reinstalling"
    ERROR = "error"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class ServerStatus(enum.Enum):
    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    RESTARTING = "restarting"
    REINSTALLING = "reinstalling"
    BACKING_UP = "backing_up"
    SUSPENDED = "suspended"
    ERROR = "error"


class TicketStatus(enum.Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    CLOSED = "closed"
    DELETED = "deleted"


class BackupType(enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class BackupStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GuardianAction(enum.Enum):
    WARN = "warn"
    MUTE = "mute"
    KICK = "kick"
    BAN = "ban"
    TIMEOUT = "timeout"
    ANTI_RAID_TRIGGERED = "anti_raid_triggered"
    ANTI_SPAM_TRIGGERED = "anti_spam_triggered"
    ANTI_NUKE_TRIGGERED = "anti_nuke_triggered"
    NOTIFICATION = "notification"


# Billing enums
class TransactionType(enum.Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    PURCHASE = "purchase"
    REFUND = "refund"


class TransactionStatus(enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvoiceStatus(enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


# ─────────────────────────────── Models ───────────────────────────────

class User(Base):
    __tablename__ = "users"

    # Integer (not BigInteger): SQLite only auto-generates rowids for columns
    # declared exactly INTEGER PRIMARY KEY — a BIGINT PK makes every INSERT
    # fail with NOT NULL constraint failed: users.id. 64-bit Discord IDs live
    # in discord_id below, which stays BigInteger.
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(100), nullable=False)
    discriminator = Column(String(10), nullable=True)
    avatar = Column(String(200), nullable=True)

    permission_level = Column(Integer, default=PermissionLevel.USER.value)
    is_whitelisted = Column(Boolean, default=False)
    is_banned = Column(Boolean, default=False)
    ban_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vps_instances = relationship("VPSInstance", back_populates="owner")
    minecraft_servers = relationship("MinecraftServer", back_populates="owner")
    tickets = relationship("Ticket", back_populates="creator", foreign_keys="Ticket.creator_id")
    tickets_claimed = relationship("Ticket", back_populates="claimer", foreign_keys="Ticket.claimer_id")
    guardian_logs = relationship("GuardianLog", back_populates="user")
    api_keys = relationship("APIKey", back_populates="user")

    # Convenience computed properties (read-only; safe everywhere)
    @property
    def total_vps(self) -> int:
        return len([v for v in (self.vps_instances or []) if v.deleted_at is None])

    @property
    def total_tickets(self) -> int:
        return len(self.tickets or [])

    @property
    def is_admin(self) -> bool:
        return self.permission_level >= PermissionLevel.ADMIN.value

    @property
    def is_staff(self) -> bool:
        return self.permission_level >= PermissionLevel.STAFF.value

    @property
    def is_premium(self) -> bool:
        return self.permission_level >= PermissionLevel.PREMIUM.value

    @property
    def mention(self) -> str:
        return f"<@{self.discord_id}>"


class BotAdmin(Base):
    """Global bot-level admins granted via /addadmin (no Discord role needed).

    Works across every guild the bot is in — perfect for giving multiple
    trusted users full admin access to the bot without touching server roles.
    """
    __tablename__ = "bot_admins"

    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_id = Column(BigInteger, unique=True, nullable=False, index=True)
    level = Column(String(20), default="admin")   # 'admin' | 'staff'
    added_by = Column(BigInteger, nullable=True)  # Discord ID of the granter
    reason = Column(String(200), nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow)

    @property
    def is_admin_level(self) -> bool:
        return self.level == "admin"


class UserWarning(Base):
    """Moderation warnings issued with /warn — auto-punishment thresholds apply."""
    __tablename__ = "user_warnings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    moderator_id = Column(BigInteger, nullable=True)
    reason = Column(String(500), default="No reason provided")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class GuildConfig(Base):
    __tablename__ = "guild_configs"

    # Integer PK — see the note on User.id (SQLite rowid aliasing).
    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, unique=True, nullable=False, index=True)

    # General
    prefix = Column(String(10), default="!")
    language = Column(String(10), default="en")
    timezone = Column(String(50), default="UTC")

    # Channels
    vps_log_channel_id = Column(BigInteger, nullable=True)
    guardian_log_channel_id = Column(BigInteger, nullable=True)
    ticket_category_id = Column(BigInteger, nullable=True)
    ticket_log_channel_id = Column(BigInteger, nullable=True)
    ticket_transcript_channel_id = Column(BigInteger, nullable=True)

    # Roles
    admin_role_ids = Column(SQLiteJSON, default=_json_list_default)
    staff_role_ids = Column(SQLiteJSON, default=_json_list_default)
    customer_role_ids = Column(SQLiteJSON, default=_json_list_default)
    vps_user_role_ids = Column(SQLiteJSON, default=_json_list_default)
    premium_user_role_ids = Column(SQLiteJSON, default=_json_list_default)
    whitelist_role_ids = Column(SQLiteJSON, default=_json_list_default)
    ticket_staff_role_ids = Column(SQLiteJSON, default=_json_list_default)

    # Guardian
    guardian_enabled = Column(Boolean, default=True)
    anti_raid_enabled = Column(Boolean, default=True)
    anti_spam_enabled = Column(Boolean, default=True)
    anti_bot_enabled = Column(Boolean, default=True)
    anti_webhook_enabled = Column(Boolean, default=True)
    anti_nuke_enabled = Column(Boolean, default=True)
    anti_kick_enabled = Column(Boolean, default=True)
    anti_ban_enabled = Column(Boolean, default=True)
    anti_role_delete_enabled = Column(Boolean, default=True)
    anti_channel_delete_enabled = Column(Boolean, default=True)
    anti_mention_enabled = Column(Boolean, default=True)
    anti_spam_threshold = Column(Integer, default=5)
    anti_spam_window = Column(Integer, default=10)
    anti_raid_threshold = Column(Integer, default=10)
    anti_raid_window = Column(Integer, default=10)
    anti_mention_limit = Column(Integer, default=5)

    # Guardian v2 (multi-admin / anti-nuke action / account-age / invite filter)
    anti_nuke_action = Column(String(10), default="strip")   # strip | kick | ban
    anti_min_account_days = Column(Integer, default=0)        # 0 = disabled
    anti_invite_enabled = Column(Boolean, default=False)      # block Discord invite links
    whitelist_user_ids = Column(SQLiteJSON, default=_json_list_default)  # Guardian whitelist (persisted)

    # ── Security Suite v3 (25 protections) ─────────────────────────────
    sec_safe_mode = Column(Boolean, default=False)            # monitor-only, no auto punishment
    sec_panic_mode = Column(Boolean, default=False)           # /panic emergency state
    sec_admin_abuse = Column(Boolean, default=True)           # #2 admin abuse detector
    sec_escalation_block = Column(Boolean, default=True)      # #3 permission escalation blocker
    sec_role_guard = Column(Boolean, default=True)            # #5 role hierarchy guard
    sec_anti_alt = Column(Boolean, default=True)              # #8 alt detection
    sec_alt_min_risk = Column(Integer, default=60)            # #8 risk score threshold (0-100)
    sec_anti_impersonation = Column(Boolean, default=True)    # #9 impersonation shield
    sec_imp_similarity = Column(Integer, default=85)          # #9 similarity percent (50-100)
    sec_anti_scam = Column(Boolean, default=True)             # #10 scam intelligence
    sec_scam_autodelete = Column(Boolean, default=True)       # #10 auto-delete scam messages
    sec_raid_lockdown = Column(Boolean, default=True)         # #11/#6 auto lockdown on raid
    sec_auto_containment = Column(Boolean, default=True)      # #12 auto timeouts on breaches
    sec_perm_drift = Column(Boolean, default=True)            # #14 permission drift watch
    sec_honeypot = Column(Boolean, default=True)              # #16 honeypot detection
    sec_two_person = Column(Boolean, default=True)            # #17 two-person approvals
    sec_temp_priv = Column(Boolean, default=True)             # #18 temporary privileges
    sec_threat_share = Column(Boolean, default=True)          # #21 cross-server threat sharing
    sec_nuke_threshold = Column(Integer, default=3)           # #1 mass actions before nuke incident
    sec_abuse_threshold = Column(Integer, default=8)          # #2 admin actions/minute before abuse flag
    sec_bot_raid_threshold = Column(Integer, default=3)       # #7 bots added before bot-raid alert
    sec_lockdown_minutes = Column(Integer, default=15)        # default emergency lockdown duration
    threat_share_channel_id = Column(BigInteger, nullable=True)  # #21 sync channel for indicator packs

    # Tickets
    max_tickets_per_user = Column(Integer, default=3)
    ticket_close_delay = Column(Integer, default=5)

    # VPS defaults
    vps_default_ram = Column(Integer, default=1024)
    vps_default_cpu = Column(Integer, default=1)
    vps_default_disk = Column(Integer, default=10)
    vps_default_os = Column(String(50), default="ubuntu-22.04")
    vps_default_virtualization = Column(String(30), default="lxd_unsupported")

    # Pterodactyl
    pterodactyl_url = Column(String(200), nullable=True)
    pterodactyl_api_key = Column(String(200), nullable=True)
    pterodactyl_app_api_key = Column(String(200), nullable=True)
    pterodactyl_client_api_key = Column(String(200), nullable=True)

    # Minecraft
    mc_default_egg = Column(String(50), default="paper")
    mc_default_version = Column(String(50), default="latest")
    mc_allocation_start = Column(Integer, default=25565)
    mc_allocation_end = Column(Integer, default=25665)

    # Monitoring
    monitoring_interval = Column(Integer, default=60)
    monitoring_alert_cpu = Column(Integer, default=90)
    monitoring_alert_ram = Column(Integer, default=90)
    monitoring_alert_disk = Column(Integer, default=90)

    # Backups
    backup_enabled = Column(Boolean, default=True)
    backup_interval = Column(Integer, default=86400)
    backup_retention_days = Column(Integer, default=7)
    backup_path = Column(String(200), default="./backups")
    backup_compression = Column(Boolean, default=True)

    # Rate limits / cooldowns
    rate_limit_commands = Column(Integer, default=10)
    rate_limit_window = Column(Integer, default=60)
    rate_limit_global = Column(Integer, default=100)
    cooldown_create_vps = Column(Integer, default=300)
    cooldown_create_mc = Column(Integer, default=60)
    cooldown_ticket = Column(Integer, default=10)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Node(Base):
    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    host = Column(String(200), nullable=False)
    port = Column(Integer, default=8080)
    api_key = Column(String(200), nullable=False)
    jwt_secret = Column(String(200), nullable=True)

    location = Column(String(100), nullable=True)          # region / datacenter label
    is_local = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    maintenance_mode = Column(Boolean, default=False)

    supported_virtualization = Column(SQLiteJSON, default=_json_list_default)

    # Capacity (configured / detected maximums)
    max_ram = Column(Integer, default=0)       # MB
    max_cpu = Column(Integer, default=0)       # cores (%)
    max_disk = Column(Integer, default=0)      # GB
    max_vps = Column(Integer, default=0)       # 0 = unlimited

    # Live usage (refreshed from agent stats)
    total_ram = Column(Integer, default=0)
    total_cpu = Column(Integer, default=0)
    total_disk = Column(Integer, default=0)
    used_ram = Column(Integer, default=0)
    used_cpu = Column(Integer, default=0)
    used_disk = Column(Integer, default=0)

    status = Column(Enum(NodeStatus), default=NodeStatus.OFFLINE)
    last_heartbeat = Column(DateTime, nullable=True)
    latency = Column(Integer, default=0)       # ms
    vps_count = Column(Integer, default=0)
    agent_version = Column(String(50), nullable=True)
    network_info = Column(SQLiteJSON, default=_json_default)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    vps_instances = relationship("VPSInstance", back_populates="node")
    minecraft_servers = relationship("MinecraftServer", back_populates="node")
    port_allocations = relationship("PortAllocation", back_populates="node")

    # ── helpers ──
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ram_percent(self) -> float:
        if not self.total_ram:
            return 0.0
        return round((self.used_ram / self.total_ram) * 100, 1)

    @property
    def disk_percent(self) -> float:
        if not self.total_disk:
            return 0.0
        return round((self.used_disk / self.total_disk) * 100, 1)

    @property
    def cpu_percent(self) -> float:
        if not self.total_cpu:
            return 0.0
        return round((self.used_cpu / self.total_cpu) * 100, 1)

    def get_virtualization_types(self):
        return self.supported_virtualization or []

    def set_virtualization_types(self, types):
        self.supported_virtualization = list(types)

    @property
    def status_value(self) -> str:
        return self.status.value if isinstance(self.status, NodeStatus) else str(self.status)

    @property
    def is_online(self) -> bool:
        return self.status == NodeStatus.ONLINE


class VPSInstance(Base):
    __tablename__ = "vps_instances"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(64), nullable=False, unique=True, default=lambda: str(uuid_lib.uuid4()))
    name = Column(String(100), nullable=False)
    hostname = Column(String(100), nullable=False, unique=True)

    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=False, index=True)

    ram = Column(Integer, nullable=False)   # MB
    cpu = Column(Integer, nullable=False)   # cores
    disk = Column(Integer, nullable=False)  # GB

    os_template = Column(String(100), nullable=False)
    virtualization = Column(Enum(VirtualizationType), default=VirtualizationType.LXD_UNSUPPORTED)

    ip_address = Column(String(45), nullable=True)
    ipv6_address = Column(String(45), nullable=True)
    ssh_port = Column(Integer, default=22)
    root_password = Column(String(200), nullable=True)  # stored encrypted

    status = Column(Enum(VPSStatus), default=VPSStatus.CREATING)

    backup_enabled = Column(Boolean, default=True)
    last_backup = Column(DateTime, nullable=True)

    pterodactyl_server_id = Column(Integer, nullable=True)
    pterodactyl_node_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="vps_instances")
    node = relationship("Node", back_populates="vps_instances")
    backups = relationship("Backup", back_populates="vps_instance")

    @property
    def status_value(self) -> str:
        return self.status.value if isinstance(self.status, VPSStatus) else str(self.status)

    @property
    def is_active(self) -> bool:
        return self.deleted_at is None and self.status != VPSStatus.DELETED


# Backwards-compatible alias used by older cogs
VPSServer = VPSInstance


class MinecraftServer(Base):
    __tablename__ = "minecraft_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    identifier = Column(String(100), nullable=False, unique=True,
                        default=lambda: str(uuid_lib.uuid4())[:8])

    owner_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=False, index=True)

    software = Column(String(50), nullable=False)
    version = Column(String(50), nullable=False)

    ram = Column(Integer, nullable=False)
    cpu = Column(Integer, nullable=False)
    disk = Column(Integer, nullable=False)

    port = Column(Integer, nullable=False, unique=True)
    allocation_id = Column(Integer, nullable=True)

    egg_id = Column(Integer, nullable=True)
    docker_image = Column(String(200), nullable=True)
    startup_command = Column(Text, nullable=True)

    status = Column(Enum(ServerStatus), default=ServerStatus.CREATING)

    pterodactyl_server_id = Column(Integer, nullable=True, unique=True)
    pterodactyl_node_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)

    owner = relationship("User", back_populates="minecraft_servers")
    node = relationship("Node", back_populates="minecraft_servers")
    backups = relationship("Backup", back_populates="minecraft_server")

    @property
    def status_value(self) -> str:
        return self.status.value if isinstance(self.status, ServerStatus) else str(self.status)


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_number = Column(Integer, nullable=False)

    guild_id = Column(BigInteger, nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=False, unique=True)

    creator_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    creator_discord_id = Column(BigInteger, nullable=False, index=True)
    claimer_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    claimer_discord_id = Column(BigInteger, nullable=True, index=True)

    category = Column(String(50), nullable=False)
    subject = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    status = Column(Enum(TicketStatus), default=TicketStatus.OPEN)

    claimed_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    transcript = Column(Text, nullable=True)
    transcript_channel_id = Column(BigInteger, nullable=True)
    message_ids = Column(SQLiteJSON, default=_json_list_default)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", back_populates="tickets", foreign_keys=[creator_id])
    claimer = relationship("User", back_populates="tickets_claimed", foreign_keys=[claimer_id])
    participants = relationship("TicketParticipant", back_populates="ticket")
    messages = relationship("TicketMessage", back_populates="ticket")

    # legacy alias used by old code paths
    @property
    def claimant_id(self):
        return self.claimer_id

    @claimant_id.setter
    def claimant_id(self, value):
        self.claimer_id = value

    @property
    def status_value(self) -> str:
        return self.status.value if isinstance(self.status, TicketStatus) else str(self.status)


class TicketMessage(Base):
    __tablename__ = "ticket_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)

    author_id = Column(BigInteger, nullable=False, index=True)
    author_name = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    is_staff = Column(Boolean, default=False)
    message_id = Column(BigInteger, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    ticket = relationship("Ticket", back_populates="messages")


class TicketParticipant(Base):
    __tablename__ = "ticket_participants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticket_id = Column(Integer, ForeignKey("tickets.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    added_by = Column(BigInteger, nullable=False)

    added_at = Column(DateTime, default=datetime.utcnow)

    ticket = relationship("Ticket", back_populates="participants")
    user = relationship("User")


class GuardianLog(Base):
    __tablename__ = "guardian_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    moderator_id = Column(BigInteger, nullable=True)

    # Free-form action string (prevents Enum LookupError for security events)
    action = Column(String(100), nullable=False, index=True)
    reason = Column(Text, nullable=True)
    details = Column(SQLiteJSON, default=_json_default)

    triggered_by = Column(String(50), nullable=True)
    target_id = Column(BigInteger, nullable=True)
    target_name = Column(String(100), nullable=True)
    moderator_name = Column(String(100), nullable=True)
    punishment = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="guardian_logs")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)

    action = Column(String(100), nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id = Column(BigInteger, nullable=True)

    before = Column(SQLiteJSON, default=_json_default)
    after = Column(SQLiteJSON, default=_json_default)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class Backup(Base):
    __tablename__ = "backups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    uuid = Column(String(64), nullable=False, unique=True, default=lambda: str(uuid_lib.uuid4()))

    vps_instance_id = Column(Integer, ForeignKey("vps_instances.id"), nullable=True, index=True)
    minecraft_server_id = Column(Integer, ForeignKey("minecraft_servers.id"), nullable=True, index=True)

    backup_type = Column(Enum(BackupType), default=BackupType.MANUAL)
    status = Column(Enum(BackupStatus), default=BackupStatus.PENDING)

    file_path = Column(String(500), nullable=True)
    file_size = Column(BigInteger, default=0)
    checksum = Column(String(64), nullable=True)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    vps_instance = relationship("VPSInstance", back_populates="backups")
    minecraft_server = relationship("MinecraftServer", back_populates="backups")

    # legacy alias
    @property
    def size(self):
        return self.file_size

    @property
    def status_value(self) -> str:
        return self.status.value if isinstance(self.status, BackupStatus) else str(self.status)


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    key_hash = Column(String(128), nullable=False, unique=True)
    key_prefix = Column(String(10), nullable=False)

    permissions = Column(SQLiteJSON, default=_json_list_default)
    is_active = Column(Boolean, default=True)

    last_used = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="api_keys")


class PortAllocation(Base):
    __tablename__ = "port_allocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=False, index=True)

    port = Column(Integer, nullable=False)
    protocol = Column(String(10), default="tcp")

    allocated_to_type = Column(String(50), nullable=True)
    allocated_to_id = Column(Integer, nullable=True)

    is_reserved = Column(Boolean, default=False)
    reserved_until = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    node = relationship("Node", back_populates="port_allocations")

    __table_args__ = (
        UniqueConstraint("node_id", "port", "protocol", name="unique_port_per_node"),
    )


class SSHKey(Base):
    __tablename__ = "ssh_keys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    node_id = Column(Integer, ForeignKey("nodes.id"), nullable=True, index=True)

    name = Column(String(100), nullable=False)
    public_key = Column(Text, nullable=False)
    private_key = Column(Text, nullable=True)

    is_default = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


# ────────────────────────── Billing models ──────────────────────────

class Wallet(Base):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    balance = Column(Integer, default=0)  # store cents
    currency = Column(String(10), default="USD")
    is_frozen = Column(Boolean, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")
    transactions = relationship("Transaction", back_populates="wallet")


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), nullable=False, index=True)

    amount = Column(Integer, nullable=False)  # cents
    type = Column(Enum(TransactionType), default=TransactionType.DEPOSIT)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.PENDING)

    description = Column(String(200), nullable=True)
    reference = Column(String(100), nullable=True, unique=True)
    metadata_json = Column("metadata", SQLiteJSON, default=_json_default)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    wallet = relationship("Wallet", back_populates="transactions")


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    price = Column(Integer, nullable=False, default=0)   # cents / month (0 = free via invites)
    currency = Column(String(10), default="USD")

    ram = Column(Integer, default=1024)
    cpu = Column(Integer, default=1)
    disk = Column(Integer, default=10)

    # Invite-earned hosting: how many invites a member needs to claim this plan.
    # 0 = not invite-gated (paid / staff-only plan).
    required_invites = Column(Integer, default=0, nullable=False)

    features = Column(SQLiteJSON, default=_json_list_default)
    is_active = Column(Boolean, default=True)
    is_premium = Column(Boolean, default=False)
    sort_order = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="plan")


# ────────────────────── Invite tracking (plans economy) ──────────────────────

class InviteStat(Base):
    """Net invite balance per (guild, inviter). Bonus invites are additive."""
    __tablename__ = "invite_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    inviter_id = Column(BigInteger, nullable=False, index=True)   # Discord ID

    real_invites = Column(Integer, default=0, nullable=False)     # from tracked joins
    bonus_invites = Column(Integer, default=0, nullable=False)    # granted by admins / codes

    # number of VPS the inviter has already claimed with invites (spent)
    invites_spent = Column(Integer, default=0, nullable=False)

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("guild_id", "inviter_id", name="uq_invite_stat_guild_inviter"),
    )

    @property
    def available(self) -> int:
        return max(0, (self.real_invites + self.bonus_invites) - self.invites_spent)


class InviteLog(Base):
    """One row per tracked member join, for audit + leave-decrement."""
    __tablename__ = "invite_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    invitee_id = Column(BigInteger, nullable=False, index=True)
    inviter_id = Column(BigInteger, nullable=True, index=True)    # None = unknown/vanity
    code = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    left_at = Column(DateTime, nullable=True)


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String(50), nullable=False, unique=True)

    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)

    amount = Column(Integer, nullable=False)
    currency = Column(String(10), default="USD")
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.PENDING)

    due_date = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User")
    plan = relationship("Plan", back_populates="invoices")


# ─────────────────────── Dashboard / AI models ───────────────────────

class DashboardUser(Base):
    """Web dashboard login account (password stored as salted hash)."""
    __tablename__ = "dashboard_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String(256), nullable=False)

    discord_id = Column(BigInteger, nullable=True)
    display_name = Column(String(100), nullable=True)
    role = Column(String(20), default="admin")  # admin | staff | viewer

    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class AIConversation(Base):
    """Per-user AI assistant chat history."""
    __tablename__ = "ai_conversations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=True, index=True)

    role = Column(String(20), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    tokens = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)


# ─────────────────────── Security Suite v3 models ───────────────────────

class SecurityIncident(Base):
    """Incident timeline entry (#19) — one row per detected security event."""
    __tablename__ = "security_incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)

    itype = Column(String(50), nullable=False, index=True)      # nuke, scam, raid, ...
    severity = Column(String(20), default="low", index=True)    # low | medium | high | critical

    actor_id = Column(BigInteger, nullable=True, index=True)    # who triggered it (if known)
    actor_name = Column(String(120), nullable=True)
    target_id = Column(BigInteger, nullable=True)
    target_name = Column(String(120), nullable=True)

    summary = Column(String(500), nullable=False)
    details = Column(SQLiteJSON, default=_json_default)         # structured context
    evidence = Column(SQLiteJSON, default=_json_list_default)   # #24 automatic evidence collection
    response = Column(String(200), nullable=True)               # containment taken

    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class LogChainEntry(Base):
    """Tamper-evident log record (#20) — sha256(prev_hash | ts | payload) chain."""
    __tablename__ = "log_chain"

    seq = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=True, index=True)
    ts = Column(String(40), nullable=False)
    kind = Column(String(50), nullable=False)
    payload = Column(Text, nullable=False)
    prev_hash = Column(String(64), nullable=False)
    entry_hash = Column(String(64), nullable=False, index=True)


class PendingApproval(Base):
    """Two-person rule queue (#17) — sensitive actions need a second approver."""
    __tablename__ = "pending_approvals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    action = Column(String(50), nullable=False)                 # restore_baseline | threat_import | ...
    payload = Column(SQLiteJSON, default=_json_default)
    title = Column(String(200), nullable=False)

    requester_id = Column(BigInteger, nullable=False)
    approver_id = Column(BigInteger, nullable=True)
    status = Column(String(20), default="pending", index=True)  # pending | approved | denied | expired
    decision_note = Column(String(300), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    decided_at = Column(DateTime, nullable=True)


class TempPrivilege(Base):
    """Temporary privilege grant (#18) — expires automatically."""
    __tablename__ = "temp_privileges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    perm = Column(String(50), nullable=False)                   # security | moderation | admin | restore
    granted_by = Column(BigInteger, nullable=True)
    reason = Column(String(200), nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ThreatIndicator(Base):
    """Cross-server threat indicator (#21) — domains, patterns, hashed users."""
    __tablename__ = "threat_indicators"

    id = Column(Integer, primary_key=True, autoincrement=True)
    indicator = Column(String(300), nullable=False, index=True)
    itype = Column(String(30), nullable=False, default="domain")  # domain | pattern | user_hash
    source = Column(String(30), default="local")                  # local | shared | sync
    guild_id = Column(BigInteger, nullable=True)
    hits = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("indicator", "itype", name="uq_threat_indicator"),
    )


class HoneypotChannel(Base):
    """Decoy channel (#16) — anyone posting in it gets flagged & banned."""
    __tablename__ = "honeypot_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    channel_id = Column(BigInteger, nullable=False)
    created_by = Column(BigInteger, nullable=True)
    hits = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("guild_id", "channel_id", name="uq_honeypot_guild_channel"),
    )


class OffenderStrike(Base):
    """Repeat-abuse tracking (#23) — members banned at SEC_EVASION_BAN_AT strikes."""
    __tablename__ = "offender_strikes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    user_name = Column(String(120), nullable=True)
    reason = Column(String(300), nullable=True)
    issued_by = Column(BigInteger, nullable=True)               # 0 = automatic
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class GuildBaseline(Base):
    """Role/admin snapshot for smart recovery (#13) + permission drift (#14)."""
    __tablename__ = "guild_baselines"

    guild_id = Column(BigInteger, primary_key=True)
    snapshot = Column(SQLiteJSON, default=_json_default)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ─────────────────────────── Engine & helpers ───────────────────────────

def _sqlite_path(url: str) -> str:
    return url.replace("sqlite+aiosqlite:///", "").lstrip("./")


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def _migrate_sqlite(conn) -> None:
    """Lightweight additive migrations for databases created by older versions.

    SQLite supports ADD COLUMN — we check pragma table_info and append any
    columns introduced after the first release. Never destructive.
    """
    from sqlalchemy import text

    migrations = {
        "plans": {
            "required_invites": "ALTER TABLE plans ADD COLUMN required_invites INTEGER NOT NULL DEFAULT 0",
        },
        "guild_configs": {
            "whitelist_user_ids":
                "ALTER TABLE guild_configs ADD COLUMN whitelist_user_ids JSON",
            "anti_nuke_action":
                "ALTER TABLE guild_configs ADD COLUMN anti_nuke_action VARCHAR(10) NOT NULL DEFAULT 'strip'",
            "anti_min_account_days":
                "ALTER TABLE guild_configs ADD COLUMN anti_min_account_days INTEGER NOT NULL DEFAULT 0",
            "anti_invite_enabled":
                "ALTER TABLE guild_configs ADD COLUMN anti_invite_enabled BOOLEAN NOT NULL DEFAULT 0",
            # Security Suite v3 columns
            "sec_safe_mode":
                "ALTER TABLE guild_configs ADD COLUMN sec_safe_mode BOOLEAN NOT NULL DEFAULT 0",
            "sec_panic_mode":
                "ALTER TABLE guild_configs ADD COLUMN sec_panic_mode BOOLEAN NOT NULL DEFAULT 0",
            "sec_admin_abuse":
                "ALTER TABLE guild_configs ADD COLUMN sec_admin_abuse BOOLEAN NOT NULL DEFAULT 1",
            "sec_escalation_block":
                "ALTER TABLE guild_configs ADD COLUMN sec_escalation_block BOOLEAN NOT NULL DEFAULT 1",
            "sec_role_guard":
                "ALTER TABLE guild_configs ADD COLUMN sec_role_guard BOOLEAN NOT NULL DEFAULT 1",
            "sec_anti_alt":
                "ALTER TABLE guild_configs ADD COLUMN sec_anti_alt BOOLEAN NOT NULL DEFAULT 1",
            "sec_alt_min_risk":
                "ALTER TABLE guild_configs ADD COLUMN sec_alt_min_risk INTEGER NOT NULL DEFAULT 60",
            "sec_anti_impersonation":
                "ALTER TABLE guild_configs ADD COLUMN sec_anti_impersonation BOOLEAN NOT NULL DEFAULT 1",
            "sec_imp_similarity":
                "ALTER TABLE guild_configs ADD COLUMN sec_imp_similarity INTEGER NOT NULL DEFAULT 85",
            "sec_anti_scam":
                "ALTER TABLE guild_configs ADD COLUMN sec_anti_scam BOOLEAN NOT NULL DEFAULT 1",
            "sec_scam_autodelete":
                "ALTER TABLE guild_configs ADD COLUMN sec_scam_autodelete BOOLEAN NOT NULL DEFAULT 1",
            "sec_raid_lockdown":
                "ALTER TABLE guild_configs ADD COLUMN sec_raid_lockdown BOOLEAN NOT NULL DEFAULT 1",
            "sec_auto_containment":
                "ALTER TABLE guild_configs ADD COLUMN sec_auto_containment BOOLEAN NOT NULL DEFAULT 1",
            "sec_perm_drift":
                "ALTER TABLE guild_configs ADD COLUMN sec_perm_drift BOOLEAN NOT NULL DEFAULT 1",
            "sec_honeypot":
                "ALTER TABLE guild_configs ADD COLUMN sec_honeypot BOOLEAN NOT NULL DEFAULT 1",
            "sec_two_person":
                "ALTER TABLE guild_configs ADD COLUMN sec_two_person BOOLEAN NOT NULL DEFAULT 1",
            "sec_temp_priv":
                "ALTER TABLE guild_configs ADD COLUMN sec_temp_priv BOOLEAN NOT NULL DEFAULT 1",
            "sec_threat_share":
                "ALTER TABLE guild_configs ADD COLUMN sec_threat_share BOOLEAN NOT NULL DEFAULT 1",
            "sec_nuke_threshold":
                "ALTER TABLE guild_configs ADD COLUMN sec_nuke_threshold INTEGER NOT NULL DEFAULT 3",
            "sec_abuse_threshold":
                "ALTER TABLE guild_configs ADD COLUMN sec_abuse_threshold INTEGER NOT NULL DEFAULT 8",
            "sec_bot_raid_threshold":
                "ALTER TABLE guild_configs ADD COLUMN sec_bot_raid_threshold INTEGER NOT NULL DEFAULT 3",
            "sec_lockdown_minutes":
                "ALTER TABLE guild_configs ADD COLUMN sec_lockdown_minutes INTEGER NOT NULL DEFAULT 15",
        },
    }
    for table, columns in migrations.items():
        try:
            rows = (await conn.execute(
                text(f"PRAGMA table_info({table})"))).fetchall()
            existing = {r[1] for r in rows}
            if not existing:
                continue  # table doesn't exist yet; create_all() will handle it
            for col, ddl in columns.items():
                if col not in existing:
                    await conn.execute(text(ddl))
        except Exception:
            # non-SQLite DBs (future Postgres) should use Alembic — ignore here
            pass


async def init_db():
    db_file = _sqlite_path(settings.DATABASE_URL)
    db_dir = os.path.dirname(db_file)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_sqlite(conn)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_db():
    await engine.dispose()


async def get_user(session: AsyncSession, discord_id: int) -> Optional[User]:
    result = await session.execute(select(User).where(User.discord_id == discord_id))
    return result.scalar_one_or_none()


async def get_or_create_user(session: AsyncSession, discord_id: int,
                             username: str = "unknown",
                             discriminator: str = None,
                             avatar: str = None) -> User:
    user = await get_user(session, discord_id)
    if not user:
        user = User(
            discord_id=discord_id,
            username=username,
            discriminator=discriminator,
            avatar=avatar,
        )
        session.add(user)
        await session.flush()
    else:
        # keep username fresh
        if username and username != "unknown" and user.username != username:
            user.username = username
    return user


async def get_guild_config(session: AsyncSession, guild_id: int) -> Optional[GuildConfig]:
    result = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
    return result.scalar_one_or_none()


async def get_or_create_guild_config(session: AsyncSession, guild_id: int) -> GuildConfig:
    config = await get_guild_config(session, guild_id)
    if not config:
        config = GuildConfig(guild_id=guild_id)
        session.add(config)
        await session.flush()
    return config


async def count_active_vps(session: AsyncSession, node_id: Optional[int] = None) -> int:
    query = select(func.count(VPSInstance.id)).where(
        VPSInstance.deleted_at.is_(None),
        VPSInstance.status != VPSStatus.DELETED,
    )
    if node_id is not None:
        query = query.where(VPSInstance.node_id == node_id)
    result = await session.execute(query)
    return int(result.scalar() or 0)
