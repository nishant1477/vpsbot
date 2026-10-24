import discord
from discord.ext import commands
from typing import Dict
from datetime import datetime, timezone

from utils import (
    success_embed, error_embed, info_embed, monitoring_embed, node_embed,
    get_logger, format_bytes, create_progress_bar, format_percentage,
    has_permission, PermissionLevel,
)
from utils.node_client import check_all_nodes, NodeClient, NodeConnectionError
from database import async_session_maker, Node, NodeStatus
from config import settings
from sqlalchemy import select

logger = get_logger("monitoring")

# In-memory alert dedup: {node_id: {metric: alerted_bool}}
_alerted: Dict[int, Dict[str, bool]] = {}


def _bar(current: int, total: int, length: int = 10) -> str:
    return create_progress_bar(current, total or 1, length)


async def check_node_alerts(bot) -> None:
    """Called every minute by bot.monitoring_task.

    - Alerts when a node crosses CPU/RAM/Disk thresholds.
    - De-duplicates so each threshold only fires once until it recovers.
    """
    if not settings.MONITORING_ENABLED:
        return

    channel_id = settings.MONITORING_ALERT_CHANNEL_ID
    if not channel_id:
        return
    channel = bot.get_channel(int(channel_id))
    if channel is None:
        return

    try:
        async with async_session_maker() as session:
            nodes = (await session.execute(select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712
        if not nodes:
            return

        health_map = await check_all_nodes(nodes)

        for node in nodes:
            health = health_map.get(node.id) or {}
            state = _alerted.setdefault(node.id, {})

            if not health.get("online"):
                if not state.get("offline"):
                    state["offline"] = True
                    try:
                        await channel.send(embed=error_embed(
                            "🔴 Node Offline",
                            f"**{node.name}** (`{node.host}:{node.port}`) failed the monitoring check.",
                        ))
                    except Exception:
                        pass
                continue

            if state.pop("offline", False):
                try:
                    await channel.send(embed=success_embed(
                        "🟢 Node Back Online", f"**{node.name}** is responding again."
                    ))
                except Exception:
                    pass

            # refresh metrics from agent
            try:
                client = NodeClient(node)
                data = await client.get_stats()
                stats = data.get("stats") or {}
                cpu = (stats.get("cpu") or {}).get("percent", 0)
                ram_pct = (stats.get("ram") or {}).get("percent", 0)
                disk_pct = (stats.get("disk") or {}).get("percent", 0)
            except NodeConnectionError:
                continue

            checks = {
                "cpu": (cpu, settings.MONITORING_ALERT_CPU),
                "ram": (ram_pct, settings.MONITORING_ALERT_RAM),
                "disk": (disk_pct, settings.MONITORING_ALERT_DISK),
            }
            for metric, (value, threshold) in checks.items():
                if value >= threshold and not state.get(metric):
                    state[metric] = True
                    try:
                        await channel.send(embed=monitoring_embed(
                            f"⚠️ High {metric.upper()} — {node.name}",
                            f"{metric.upper()} usage is **{value:.1f}%** "
                            f"(threshold {threshold}%).\n"
                            f"Node: `{node.host}:{node.port}` • VPS: {node.vps_count}",
                        ))
                    except Exception:
                        pass
                elif value < threshold - 5 and state.get(metric):
                    state[metric] = False
    except Exception as exc:
        logger.error(f"check_node_alerts error: {exc}")


class MonitoringCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="status", description="Quick fleet status overview")
    async def status(self, ctx: commands.Context):
        async with async_session_maker() as session:
            from database import VPSInstance, VPSStatus
            nodes = (await session.execute(select(Node))).scalars().all()
            running = len((await session.execute(
                select(VPSInstance.id).where(
                    VPSInstance.status == VPSStatus.RUNNING,
                    VPSInstance.deleted_at.is_(None)))).all())

        online = sum(1 for n in nodes if n.status == NodeStatus.ONLINE)
        offline = sum(1 for n in nodes if n.status == NodeStatus.OFFLINE)
        maintenance = sum(1 for n in nodes if n.status == NodeStatus.MAINTENANCE)

        embed = monitoring_embed("📊 Fleet Status", None)
        embed.add_field(name="🟢 Online Nodes", value=str(online), inline=True)
        embed.add_field(name="🔴 Offline Nodes", value=str(offline), inline=True)
        embed.add_field(name="🟡 Maintenance", value=str(maintenance), inline=True)
        embed.add_field(name="🚀 Running VPS", value=str(running), inline=True)
        embed.add_field(name="🖥️ Total Nodes", value=str(len(nodes)), inline=True)
        boot_ts = int(self.bot.boot_time.replace(tzinfo=timezone.utc).timestamp())
        embed.add_field(name="⏱️ Uptime", value=f"<t:{boot_ts}:R>", inline=True)
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="stats", description="Detailed node statistics")
    @commands.guild_only()
    async def stats(self, ctx: commands.Context):
        async with async_session_maker() as session:
            nodes = (await session.execute(select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712

        if not nodes:
            await ctx.send(embed=info_embed("No Nodes", "No active nodes to report on."), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        health_map = await check_all_nodes(nodes)

        embed = monitoring_embed("📈 Node Statistics", "Live metrics from every node agent")
        for node in nodes:
            health = health_map.get(node.id) or {}
            if health.get("online"):
                try:
                    data = await NodeClient(node).get_stats()
                    s = data.get("stats") or {}
                    ram = s.get("ram") or {}
                    disk = s.get("disk") or {}
                    cpu = s.get("cpu") or {}
                    ram_bar = _bar(ram.get("used", 1), ram.get("total", 1) or 1)
                    disk_bar = _bar(disk.get("used", 1), disk.get("total", 1) or 1)
                    embed.add_field(
                        name=f"🟢 {node.name} • {health.get('latency', 0)}ms",
                        value=(
                            f"**CPU** {cpu.get('percent', 0):.1f}% of {cpu.get('cores', '?')} cores\n"
                            f"**RAM** {format_bytes(ram.get('used', 0))} / {format_bytes(ram.get('total', 0))}\n"
                            f"`{ram_bar}`\n"
                            f"**Disk** {format_bytes(disk.get('used', 0))} / {format_bytes(disk.get('total', 0))}\n"
                            f"`{disk_bar}`\n"
                            f"**VPS** {s.get('vps_count', node.vps_count)}"
                        ),
                        inline=False,
                    )
                except NodeConnectionError:
                    embed.add_field(name=f"🟢 {node.name}", value="agent timeout", inline=False)
            else:
                embed.add_field(name=f"🔴 {node.name}", value="offline", inline=False)

        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="monitor", description="Set the monitoring alert channel")
    @commands.guild_only()
    async def monitor(self, ctx: commands.Context, channel: discord.TextChannel = None):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        channel = channel or ctx.channel
        # persist via dynamic import to keep settings single-source
        import config as cfg
        cfg.settings.MONITORING_ALERT_CHANNEL_ID = channel.id
        embed = success_embed("Monitoring Enabled", f"Alerts will be posted in {channel.mention}.")
        embed.set_footer(text="Set MONITORING_ALERT_CHANNEL_ID in .env to persist across restarts.")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(MonitoringCog(bot))
