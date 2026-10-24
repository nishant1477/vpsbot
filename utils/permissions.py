"""Arqon Bot — permission system.

Permission sources (highest → lowest):
  1. Bot owners (OWNER_IDS / OWNER_ID in .env)
  2. Bot admins  — global, granted with /addadmin, stored in the bot_admins DB
     table and mirrored into a synchronous in-process cache (this module)
  3. Guild roles (admin/staff/premium/vps/customer role bindings)
"""
from enum import IntEnum
from typing import Optional, Iterable, Tuple
import discord
from discord.ext import commands

from config import settings
from utils.embeds import error_embed

# ── bot admin cache (multi-user admin system) ─────────────────────────
# Populated at startup from the bot_admins table and refreshed whenever
# /addadmin or /removeadmin is used.  Keyed by Discord ID → 'admin' | 'staff'.
_bot_admin_levels: dict = {}


def set_bot_admin_cache(rows: Iterable[Tuple[int, str]]) -> None:
    """Replace the whole cache — called at startup and after admin changes."""
    global _bot_admin_levels
    _bot_admin_levels = {int(uid): (lvl or "admin") for uid, lvl in rows}


def get_bot_admin_level(discord_id: int) -> Optional[str]:
    """Return the bot-level admin level for a user ('admin'/'staff') or None."""
    return _bot_admin_levels.get(int(discord_id))


def all_bot_admins() -> dict:
    """A copy of the live bot-admin cache {discord_id: level}."""
    return dict(_bot_admin_levels)


def refresh_bot_admin_row(discord_id: int, level: Optional[str]) -> None:
    """Add, update or remove a single entry without a full reload."""
    if level is None:
        _bot_admin_levels.pop(int(discord_id), None)
    else:
        _bot_admin_levels[int(discord_id)] = level


class PermissionLevel(IntEnum):
    USER = 0
    CUSTOMER = 1
    VPS_USER = 2
    PREMIUM = 3
    STAFF = 4
    ADMIN = 5
    OWNER = 6


ROLE_LEVEL_MAP = {
    "owner": PermissionLevel.OWNER,
    "admin": PermissionLevel.ADMIN,
    "staff": PermissionLevel.STAFF,
    "premium": PermissionLevel.PREMIUM,
    "vps_user": PermissionLevel.VPS_USER,
    "customer": PermissionLevel.CUSTOMER,
}


def _owner_ids():
    return set(settings.effective_owner_ids)


def get_user_permission_level(member: discord.Member) -> PermissionLevel:
    if member.id in _owner_ids():
        return PermissionLevel.OWNER

    # Bot-level admins granted with /addadmin (global, works in every guild)
    bot_level = get_bot_admin_level(member.id)
    if bot_level == "admin":
        return PermissionLevel.ADMIN
    if bot_level == "staff":
        return PermissionLevel.STAFF

    # Guild owner is treated as admin — OFF by default: a hostile foreign
    # guild owner must never gain admin over the bot's cross-server systems
    # (nodes, plans, logs). Enable only for single-guild deployments via
    # GUILD_OWNER_IS_ADMIN=true in .env.
    if settings.GUILD_OWNER_IS_ADMIN and member.guild and \
            member.guild.owner_id == member.id:
        return PermissionLevel.ADMIN

    role_ids = {r.id for r in getattr(member, "roles", [])}

    if role_ids & set(settings.ADMIN_ROLE_IDS):
        return PermissionLevel.ADMIN
    if role_ids & set(settings.STAFF_ROLE_IDS):
        return PermissionLevel.STAFF
    if role_ids & set(settings.effective_premium_role_ids):
        return PermissionLevel.PREMIUM
    if role_ids & set(settings.VPS_USER_ROLE_IDS):
        return PermissionLevel.VPS_USER
    if role_ids & set(settings.CUSTOMER_ROLE_IDS):
        return PermissionLevel.CUSTOMER

    return PermissionLevel.USER


def has_permission(member: discord.Member, required: PermissionLevel) -> bool:
    return get_user_permission_level(member) >= required


def is_owner(member: discord.Member) -> bool:
    return member.id in _owner_ids()


def is_admin(member: discord.Member) -> bool:
    return has_permission(member, PermissionLevel.ADMIN)


def is_staff(member: discord.Member) -> bool:
    return has_permission(member, PermissionLevel.STAFF)


def is_premium(member: discord.Member) -> bool:
    return has_permission(member, PermissionLevel.PREMIUM)


def is_vps_user(member: discord.Member) -> bool:
    return has_permission(member, PermissionLevel.VPS_USER)


def is_customer(member: discord.Member) -> bool:
    return has_permission(member, PermissionLevel.CUSTOMER)


# ── command checks ────────────────────────────────────────────────────

def owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not is_owner(ctx.author):
            raise commands.CheckFailure("Owner only command")
        return True
    return commands.check(predicate)


def admin_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not is_admin(ctx.author):
            raise commands.CheckFailure("Admin only command")
        return True
    return commands.check(predicate)


def staff_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not is_staff(ctx.author):
            raise commands.CheckFailure("Staff only command")
        return True
    return commands.check(predicate)


def premium_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not is_premium(ctx.author):
            raise commands.CheckFailure("Premium only command")
        return True
    return commands.check(predicate)


def vps_user_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not is_vps_user(ctx.author):
            raise commands.CheckFailure("VPS User only command")
        return True
    return commands.check(predicate)


def customer_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not is_customer(ctx.author):
            raise commands.CheckFailure("Customer only command")
        return True
    return commands.check(predicate)


def require_permission(level: PermissionLevel):
    async def predicate(ctx: commands.Context) -> bool:
        if not isinstance(ctx.author, discord.Member) or not has_permission(ctx.author, level):
            raise commands.CheckFailure(f"Requires {level.name} permission")
        return True
    return commands.check(predicate)


def guild_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            raise commands.NoPrivateMessage("This command cannot be used in DMs")
        return True
    return commands.check(predicate)


def dm_only():
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild:
            raise commands.CheckFailure("This command can only be used in DMs")
        return True
    return commands.check(predicate)


def check_guild_permissions(*perms: str):
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True
        member_perms = ctx.author.guild_permissions
        missing = [p for p in perms if not getattr(member_perms, p, False)]
        if missing:
            raise commands.MissingPermissions(missing)
        return True
    return commands.check(predicate)


def check_bot_permissions(*perms: str):
    async def predicate(ctx: commands.Context) -> bool:
        if not ctx.guild:
            return True
        bot_perms = ctx.guild.me.guild_permissions
        missing = [p for p in perms if not getattr(bot_perms, p, False)]
        if missing:
            raise commands.BotMissingPermissions(missing)
        return True
    return commands.check(predicate)


class PermissionView(discord.ui.View):
    def __init__(self, required_level: PermissionLevel, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.required_level = required_level
        self.result: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or not has_permission(
                interaction.user, self.required_level):
            await interaction.response.send_message(
                embed=error_embed("Permission Denied",
                                  f"Requires {self.required_level.name} permission"),
                ephemeral=True,
            )
            return False
        return True
