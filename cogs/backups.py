import discord
from discord.ext import commands
from typing import Optional
from datetime import datetime, timezone, timedelta

from utils import (
    success_embed, error_embed, info_embed, backup_embed,
    get_logger, format_bytes, has_permission, PermissionLevel,
    log_vps_action,
)
from utils.node_client import NodeClient, NodeConnectionError
from database import (
    async_session_maker, Backup, BackupType, BackupStatus,
    VPSInstance, User, Node,
)
from config import settings
from sqlalchemy import select, desc

logger = get_logger("backups")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _run_backup_for_vps(bot, vps: VPSInstance, backup_type: BackupType) -> Optional[Backup]:
    """Trigger a REAL backup on the node agent and record the result truthfully:
    PENDING while queued, COMPLETED with the actual file size on success,
    FAILED with the agent's error message on failure."""
    started = _utcnow()
    backup = Backup(
        name=f"{'auto' if backup_type == BackupType.AUTO else 'manual'}-"
             f"{vps.hostname}-{started.strftime('%Y%m%d-%H%M%S')}",
        vps_instance_id=vps.id,
        backup_type=backup_type,
        status=BackupStatus.PENDING,
        started_at=started,
        file_size=0,
    )
    async with async_session_maker() as session:
        session.add(backup)
        await session.commit()
        await session.refresh(backup)
        backup_id = backup.id

    status, size, path, message = BackupStatus.FAILED, 0, None, None
    try:
        async with async_session_maker() as session:
            node = await session.get(Node, vps.node_id)
        if not node:
            message = "node no longer exists"
        else:
            result = await NodeClient(node, timeout=900).backup_vps(vps.uuid)
            if result.get("status") == "completed":
                status = BackupStatus.COMPLETED
                size = int(result.get("file_size") or 0)
                path = result.get("file_path")
            else:
                message = str(result.get("message") or "agent returned an unexpected status")
    except NodeConnectionError as exc:
        message = f"node unreachable: {exc}"
    except Exception as exc:
        message = str(exc)

    finished = _utcnow()
    async with async_session_maker() as session:
        db_backup = await session.get(Backup, backup_id)
        if db_backup:
            db_backup.status = status
            db_backup.file_size = size
            if path:
                db_backup.file_path = path
            if message:
                db_backup.error_message = message[:2000]
            if status == BackupStatus.COMPLETED:
                db_backup.completed_at = finished
                db_vps = await session.get(VPSInstance, vps.id)
                if db_vps:
                    db_vps.last_backup = finished
        await session.commit()
        if db_backup:
            await session.refresh(db_backup)
            return db_backup
    return None


async def run_scheduled_backups(bot) -> None:
    """Hourly task: back up every active VPS with backup_enabled=True via the
    node agent, then prune backups older than BACKUP_RETENTION_DAYS."""
    try:
        rows = []
        async with async_session_maker() as session:
            rows = (await session.execute(
                select(VPSInstance)
                .where(VPSInstance.backup_enabled == True)  # noqa: E712
                .where(VPSInstance.deleted_at.is_(None))
            )).scalars().all()

        for vps in rows:
            await _run_backup_for_vps(bot, vps, BackupType.AUTO)

        # retention: delete COMPLETED backups older than the retention window
        cutoff = _utcnow() - timedelta(days=settings.BACKUP_RETENTION_DAYS)
        async with async_session_maker() as session:
            old = (await session.execute(
                select(Backup)
                .where(Backup.created_at < cutoff)
                .where(Backup.status == BackupStatus.COMPLETED)
            )).scalars().all()
            for backup in old:
                await session.delete(backup)
            if old:
                await session.commit()
                logger.info(f"Retention pruned {len(old)} old backup records")

        if rows:
            logger.info(f"Scheduled backups processed for {len(rows)} VPS")
    except Exception as exc:
        logger.error(f"run_scheduled_backups error: {exc}")


class BackupsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="backup", description="Create a manual backup of a VPS")
    @commands.guild_only()
    async def backup(self, ctx: commands.Context, vps_id: int):
        async with async_session_maker() as session:
            vps = await session.get(VPSInstance, vps_id)
            if not vps or vps.deleted_at is not None:
                await ctx.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
                return

            owner = (await session.execute(
                select(User).where(User.id == vps.owner_id))).scalar_one_or_none()

        if owner and owner.discord_id != ctx.author.id and not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "You don't own this VPS."), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)

        backup = await _run_backup_for_vps(self.bot, vps, BackupType.MANUAL)
        if not backup:
            await ctx.send(embed=error_embed("Backup Failed", "Could not record the backup."), ephemeral=True)
            return

        log_vps_action("BACKUP", vps.id, ctx.author.id)
        if backup.status == BackupStatus.COMPLETED:
            embed = success_embed(
                "💾 Backup Created",
                f"**{backup.name}**\nPath: `{backup.file_path}`\n"
                f"Size: {format_bytes(backup.file_size)} • Status: completed ✅",
            )
        else:
            embed = error_embed(
                "💾 Backup Failed",
                f"**{backup.name}** failed: {backup.error_message or 'unknown error on the node agent'}",
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="backups", description="List backups for a VPS (owner or staff)")
    @commands.guild_only()
    async def backups_list(self, ctx: commands.Context, vps_id: int):
        async with async_session_maker() as session:
            vps = await session.get(VPSInstance, vps_id)
            if not vps:
                await ctx.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
                return
            from database import User
            owner = (await session.execute(
                select(User).where(User.id == vps.owner_id))).scalar_one_or_none()
            rows = (await session.execute(
                select(Backup)
                .where(Backup.vps_instance_id == vps_id)
                .order_by(desc(Backup.created_at))
                .limit(10)
            )).scalars().all()

        # tenant isolation: only the owner or staff may see a VPS's backups
        if owner and owner.discord_id != ctx.author.id \
                and not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed(
                "Permission Denied", "You don't own this VPS."), ephemeral=True)
            return
        if not owner and not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed(
                "Permission Denied", "You don't own this VPS."), ephemeral=True)
            return

        if not rows:
            await ctx.send(embed=info_embed("No Backups", f"No backups found for VPS #{vps_id}."), ephemeral=True)
            return

        embed = backup_embed(f"🗄️ Backups — VPS #{vps_id}", f"{len(rows)} most recent")
        for b in rows:
            status_emoji = {"completed": "✅", "failed": "❌", "pending": "⏳", "running": "🔄"}.get(b.status_value, "⚪")
            embed.add_field(
                name=f"{status_emoji} {b.name}",
                value=(f"Type: {b.backup_type.value} • Size: {format_bytes(b.file_size)}\n"
                       f"Created: <t:{int(b.created_at.replace(tzinfo=timezone.utc).timestamp())}:R>"),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="restore", description="Restore a VPS from backup")
    @commands.guild_only()
    async def restore(self, ctx: commands.Context, backup_id: int):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        async with async_session_maker() as session:
            backup = await session.get(Backup, backup_id)
            if not backup:
                await ctx.send(embed=error_embed("Not Found", "Backup not found"), ephemeral=True)
                return
            backup_id_ok = backup.status == BackupStatus.COMPLETED and bool(backup.file_path)
            vps_id = backup.vps_instance_id
            backup_name, backup_path = backup.name, backup.file_path

        if not backup_id_ok:
            await ctx.send(embed=error_embed(
                "Cannot Restore",
                "Only COMPLETED backups with a stored file path can be restored."), ephemeral=True)
            return

        vps = None
        async with async_session_maker() as session:
            vps = await session.get(VPSInstance, vps_id) if vps_id else None
            node = await session.get(Node, vps.node_id) if vps else None

        if not vps or not node:
            await ctx.send(embed=error_embed("Not Found", "VPS or node for this backup no longer exists."), ephemeral=True)
            return

        try:
            result = await NodeClient(node, timeout=900).restore_vps(vps.uuid, backup_path)
        except NodeConnectionError as exc:
            await ctx.send(embed=error_embed("Restore Failed", f"Node unreachable: {exc}"), ephemeral=True)
            return

        log_vps_action("RESTORE", vps_id, ctx.author.id)
        if result.get("status") == "restored":
            await ctx.send(embed=success_embed(
                "♻️ Restore Complete",
                f"Backup **{backup_name}** restored to VPS #{vps_id} "
                f"({result.get('message', 'restored')})."), ephemeral=True)
        else:
            await ctx.send(embed=error_embed(
                "♻️ Restore Failed",
                f"The node agent could not restore **{backup_name}**: "
                f"{result.get('message', 'unknown error')}"), ephemeral=True)

    @commands.hybrid_command(name="vps-backup-toggle", description="Toggle automatic backups for a VPS")
    @commands.guild_only()
    async def toggle_backup(self, ctx: commands.Context, vps_id: int):
        async with async_session_maker() as session:
            vps = await session.get(VPSInstance, vps_id)
            if not vps:
                await ctx.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
                return
            from database import User
            owner = (await session.execute(select(User).where(User.id == vps.owner_id))).scalar_one_or_none()

        if owner and owner.discord_id != ctx.author.id and not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "You don't own this VPS."), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_vps = await session.get(VPSInstance, vps_id)
            db_vps.backup_enabled = not db_vps.backup_enabled
            state = db_vps.backup_enabled
            await session.commit()

        await ctx.send(embed=success_embed(
            "Backup Setting Updated",
            f"Automatic backups are now **{'enabled ✅' if state else 'disabled ❌'}** for VPS #{vps_id}.",
        ), ephemeral=True)


async def setup(bot):
    await bot.add_cog(BackupsCog(bot))
