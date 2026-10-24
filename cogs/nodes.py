import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Button, Modal, TextInput
from typing import Optional, List
from datetime import datetime, timezone

from utils import (
    node_embed, success_embed, error_embed, info_embed,
    has_permission, PermissionLevel,
    log_node_event, get_logger, validate_node_name, validate_node_host,
    validate_node_port, generate_api_key, format_bytes,
)
from utils.node_client import NodeClient, NodeConnectionError
from database import async_session_maker, Node, NodeStatus
from config import settings
from sqlalchemy import select, func

logger = get_logger("nodes")

STATUS_EMOJI = {
    "online": "🟢",
    "offline": "🔴",
    "maintenance": "🟡",
    "error": "⚠️",
}


def _fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "Never"
    try:
        return discord.utils.format_dt(dt, style="R")
    except Exception:
        return dt.strftime("%Y-%m-%d %H:%M:%S")


def node_overview_embed(node: Node) -> discord.Embed:
    """Build the detail embed used by both `!node` and the buttons."""
    status_emoji = STATUS_EMOJI.get(node.status_value, "⚪")
    embed = node_embed(
        f"{status_emoji} Node: {node.name}",
        f"📍 {node.location or 'Unknown location'} • `{node.host}:{node.port}`",
    )
    embed.add_field(name="ID", value=str(node.id), inline=True)
    embed.add_field(name="Status", value=node.status_value.title(), inline=True)
    embed.add_field(name="Latency", value=f"{node.latency}ms", inline=True)

    total_ram = node.total_ram or 0
    used_ram = node.used_ram or 0
    embed.add_field(
        name="RAM",
        value=f"{format_bytes(used_ram * 1024 * 1024)} / {format_bytes(total_ram * 1024 * 1024)}"
              f" ({node.ram_percent}%)",
        inline=True,
    )
    embed.add_field(
        name="Disk",
        value=f"{format_bytes(node.used_disk * 1024 ** 3)} / {format_bytes(node.total_disk * 1024 ** 3)}"
              f" ({node.disk_percent}%)",
        inline=True,
    )
    embed.add_field(name="CPU", value=f"{node.cpu_percent}%", inline=True)
    embed.add_field(name="VPS Count", value=str(node.vps_count), inline=True)
    embed.add_field(
        name="Virtualization",
        value=", ".join(node.get_virtualization_types()) or "Unknown (refresh to detect)",
        inline=True,
    )
    embed.add_field(name="Maintenance", value="Yes" if node.maintenance_mode else "No", inline=True)
    embed.add_field(name="Active", value="Yes" if node.is_active else "No", inline=True)
    embed.add_field(name="Agent", value=node.agent_version or "—", inline=True)
    embed.add_field(name="Last Heartbeat", value=_fmt_dt(node.last_heartbeat), inline=False)
    return embed


class NodeRegisterModal(Modal, title="Register Node"):
    def __init__(self):
        super().__init__()
        self.name_in = TextInput(label="Node Name", placeholder="us-east-1", required=True, max_length=64)
        self.host_in = TextInput(label="Host (IP or Domain)", placeholder="192.168.1.100", required=True, max_length=255)
        self.port_in = TextInput(label="Agent Port", placeholder="8080", required=True, max_length=5)
        self.location_in = TextInput(label="Location", placeholder="New York, USA", required=False, max_length=100)
        self.api_key_in = TextInput(label="API Key (blank = auto-generate)", required=False, max_length=120, style=discord.TextStyle.short)
        for item in (self.name_in, self.host_in, self.port_in, self.location_in, self.api_key_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            name = validate_node_name(self.name_in.value)
            host = validate_node_host(self.host_in.value)
            port = validate_node_port(self.port_in.value)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed("Invalid Input", str(exc)), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        api_key = self.api_key_in.value.strip() or generate_api_key("node")

        async with async_session_maker() as session:
            existing = await session.execute(select(Node).where(Node.name == name))
            if existing.scalar_one_or_none():
                await interaction.followup.send(
                    embed=error_embed("Already Exists", "A node with this name already exists."),
                    ephemeral=True,
                )
                return

            node = Node(
                name=name,
                host=host,
                port=port,
                api_key=api_key,
                jwt_secret=generate_api_key("njwt"),
                location=self.location_in.value.strip() or None,
                is_active=True,
                status=NodeStatus.OFFLINE,
                max_vps=0,
            )
            session.add(node)
            await session.commit()
            await session.refresh(node)
            node_id = node.id

        log_node_event("REGISTER", node_id, f"Registered by {interaction.user}")

        embed = success_embed("Node Registered", f"Node **{name}** registered successfully")
        embed.add_field(name="Host", value=f"{host}:{port}", inline=True)
        embed.add_field(name="Location", value=self.location_in.value or "—", inline=True)
        embed.add_field(name="API Key", value=f"||{api_key}||", inline=False)
        embed.set_footer(text="Install the node agent on this host and set the same API key. Use the Test Connection button to verify.")
        await interaction.followup.send(embed=embed, ephemeral=True)


class DeleteNodeModal(Modal, title="Delete Node — Confirm"):
    def __init__(self, node_id: int):
        super().__init__()
        self.node_id = node_id
        self.confirm = TextInput(label="Type DELETE to confirm", placeholder="DELETE", required=True, max_length=6)
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_permission(interaction.user, PermissionLevel.ADMIN):
            await interaction.response.send_message(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        if self.confirm.value.strip().upper() != "DELETE":
            await interaction.response.send_message(embed=error_embed("Cancelled", "Deletion cancelled"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        async with async_session_maker() as session:
            node = await session.get(Node, self.node_id)
            if not node:
                await interaction.followup.send(embed=error_embed("Not Found", "Node not found"), ephemeral=True)
                return

            count = await session.execute(
                select(func.count(VPSInstance.id)).where(VPSInstance.node_id == self.node_id)
            )
            vps_count = int(count.scalar() or 0)
            if vps_count > 0:
                await interaction.followup.send(
                    embed=error_embed(
                        "Node Not Empty",
                        f"This node still hosts **{vps_count}** VPS instance(s). "
                        "Delete or migrate them first.",
                    ),
                    ephemeral=True,
                )
                return

            name = node.name
            await session.delete(node)
            await session.commit()

        log_node_event("DELETE", self.node_id, f"Deleted by {interaction.user}")
        await interaction.followup.send(embed=success_embed("Deleted", f"Node **{name}** has been deleted"), ephemeral=True)


# import after alias resolution
from database import VPSInstance  # noqa: E402


class NodeActionView(View):
    def __init__(self, node_id: int):
        super().__init__(timeout=None)
        self.node_id = node_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Infrastructure buttons are staff-only — anyone clicking without
        staff powers gets rejected before the handler runs."""
        if not has_permission(interaction.user, PermissionLevel.STAFF):
            await interaction.response.send_message(
                embed=error_embed("Staff Only",
                                  "Node management actions require staff permissions."),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Test Connection", style=discord.ButtonStyle.primary, emoji="🔌", custom_id="arqon:node_test")
    async def test_btn(self, interaction: discord.Interaction, button: Button):
        await self.test_connection(interaction)

    @discord.ui.button(label="Refresh Stats", style=discord.ButtonStyle.secondary, emoji="🔄", custom_id="arqon:node_refresh")
    async def refresh_btn(self, interaction: discord.Interaction, button: Button):
        await self.refresh_stats(interaction)

    @discord.ui.button(label="Maintenance", style=discord.ButtonStyle.success, emoji="🛠️", custom_id="arqon:node_maint")
    async def maint_btn(self, interaction: discord.Interaction, button: Button):
        await self.toggle_maintenance(interaction)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="arqon:node_delete")
    async def delete_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DeleteNodeModal(self.node_id))

    async def _get_node(self):
        async with async_session_maker() as session:
            return await session.get(Node, self.node_id)

    async def test_connection(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        node = await self._get_node()
        if not node:
            await interaction.followup.send(embed=error_embed("Not Found", "Node not found"), ephemeral=True)
            return

        client = NodeClient(node)
        try:
            result = await client.ping()
        except NodeConnectionError as exc:
            async with async_session_maker() as session:
                db_node = await session.get(Node, self.node_id)
                if db_node:
                    db_node.status = NodeStatus.OFFLINE
                    await session.commit()
            await interaction.followup.send(
                embed=error_embed("Connection Failed", str(exc)), ephemeral=True)
            return

        # Detect virtualization support while we're connected
        virt = []
        try:
            vdata = await client.detect_virtualization()
            virt = vdata.get("supported", [])
        except Exception:
            pass

        async with async_session_maker() as session:
            db_node = await session.get(Node, self.node_id)
            if db_node:
                db_node.status = NodeStatus.ONLINE
                db_node.last_heartbeat = datetime.now(timezone.utc)
                db_node.latency = int(result.get("latency_ms", 0))
                if virt:
                    db_node.set_virtualization_types(virt)
                db_node.agent_version = (result.get("version") or "")[:50] or db_node.agent_version
                await session.commit()

        log_node_event("TEST_OK", node.id, f"Tested by {interaction.user} ({result.get('latency_ms', 0)}ms)")
        embed = success_embed(
            "Connection OK",
            f"Node **{node.name}** responded in **{result.get('latency_ms', 0)}ms**"
            + (f"\nVirtualization: **{', '.join(virt)}**" if virt else ""),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def refresh_stats(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        node = await self._get_node()
        if not node:
            await interaction.followup.send(embed=error_embed("Not Found", "Node not found"), ephemeral=True)
            return

        client = NodeClient(node)
        try:
            data = await client.get_stats()
        except NodeConnectionError as exc:
            await interaction.followup.send(embed=error_embed("Refresh Failed", str(exc)), ephemeral=True)
            return

        stats = data.get("stats") or {}
        cpu = stats.get("cpu") or {}
        ram = stats.get("ram") or {}
        disk = stats.get("disk") or {}
        net = stats.get("network") or {}

        async with async_session_maker() as session:
            db_node = await session.get(Node, self.node_id)
            if not db_node:
                await interaction.followup.send(
                    embed=error_embed("Not Found", "Node no longer exists."), ephemeral=True)
                return
            db_node.status = NodeStatus.ONLINE
            db_node.last_heartbeat = datetime.now(timezone.utc)
            # cpu.percent is 0-100, cpu.cores is a core count — store used_cpu
            # in CORES so the cpu_percent property (used/total*100) stays sane.
            cores = int(cpu.get("cores") or db_node.total_cpu or 0)
            percent = float(cpu.get("percent") or 0)
            db_node.total_cpu = cores
            db_node.used_cpu = int(round(percent / 100.0 * cores)) if cores else 0
            db_node.total_ram = int(ram.get("total", 0) // (1024 * 1024))
            db_node.used_ram = int(ram.get("used", 0) // (1024 * 1024))
            db_node.total_disk = int(disk.get("total", 0) // (1024 ** 3))
            db_node.used_disk = int(disk.get("used", 0) // (1024 ** 3))
            db_node.vps_count = int(stats.get("vps_count", 0))
            db_node.network_info = {"rx": net.get("rx", 0), "tx": net.get("tx", 0)}
            db_node.agent_version = str(data.get("version", ""))[:50]
            virt = data.get("virtualization") or []
            if virt:
                db_node.set_virtualization_types(virt)
            await session.commit()
            refreshed = node_overview_embed(db_node)
            refreshed.set_footer(text=f"Stats refreshed by {interaction.user.display_name}")

        log_node_event("STATS_REFRESH", node.id, f"Refreshed by {interaction.user}")
        await interaction.followup.send(embed=refreshed, ephemeral=True)

    async def toggle_maintenance(self, interaction: discord.Interaction):
        if not has_permission(interaction.user, PermissionLevel.ADMIN):
            await interaction.response.send_message(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            node = await session.get(Node, self.node_id)
            if not node:
                await interaction.response.send_message(embed=error_embed("Not Found", "Node not found"), ephemeral=True)
                return
            node.maintenance_mode = not node.maintenance_mode
            node.status = NodeStatus.MAINTENANCE if node.maintenance_mode else NodeStatus.ONLINE
            await session.commit()
            state = "entered **maintenance mode** — new VPS will not be scheduled here" if node.maintenance_mode \
                else "exited **maintenance mode**"
            name = node.name

        log_node_event("MAINTENANCE_TOGGLE", self.node_id, f"By {interaction.user}")
        await interaction.response.send_message(
            embed=success_embed("Maintenance Toggled", f"Node **{name}** {state}."),
            ephemeral=True,
        )


class NodesCog(commands.Cog):
    """Multi-node fleet management."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="node-add", description="Register a new node")
    @commands.guild_only()
    async def node_add(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return
        await ctx.send_modal(NodeRegisterModal())

    @commands.hybrid_command(name="nodes", description="List all nodes (staff)")
    @commands.guild_only()
    async def nodes_list(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            # regular members only get a friendly summary — never host:port
            async with async_session_maker() as session:
                nodes = (await session.execute(select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712
            online = sum(1 for n in nodes if n.status == NodeStatus.ONLINE)
            await ctx.send(embed=node_embed(
                "🖥️ Node Fleet",
                f"**{len(nodes)}** node(s) registered • **{online}** online\n"
                f"Detailed infrastructure info is staff-only."), ephemeral=True)
            return
        async with async_session_maker() as session:
            nodes = (await session.execute(select(Node).order_by(Node.created_at.desc()))).scalars().all()

        if not nodes:
            await ctx.send(embed=info_embed(
                "No Nodes",
                "No nodes registered yet. Use `!node-add` to register your first node.",
            ), ephemeral=True)
            return

        online = sum(1 for n in nodes if n.status == NodeStatus.ONLINE)
        embed = node_embed("🖥️ Node Fleet", f"**{len(nodes)}** nodes • **{online}** online")
        for node in nodes:
            emoji = STATUS_EMOJI.get(node.status_value, "⚪")
            virt = ", ".join(node.get_virtualization_types()) or "unknown"
            embed.add_field(
                name=f"{emoji} {node.name} • #{node.id}",
                value=(
                    f"`{node.host}:{node.port}` • {node.location or 'Unknown'}\n"
                    f"RAM {node.ram_percent}% • Disk {node.disk_percent}% • "
                    f"VPS {node.vps_count}{'/' + str(node.max_vps) if node.max_vps else ''}\n"
                    f"Virt: {virt} • Ping: {node.latency}ms"
                ),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="node", description="View node details (staff)")
    @commands.guild_only()
    async def node_info(self, ctx: commands.Context, node_id: int):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        async with async_session_maker() as session:
            node = await session.get(Node, node_id)
        if not node:
            await ctx.send(embed=error_embed("Not Found", "Node not found"), ephemeral=True)
            return
        embed = node_overview_embed(node)
        await ctx.send(embed=embed, view=NodeActionView(node.id), ephemeral=True)

    @commands.hybrid_command(name="node-refresh", description="Refresh all node stats now")
    @commands.guild_only()
    async def node_refresh_all(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        msg = await ctx.send(embed=info_embed("🔄 Refreshing", "Health-checking all nodes..."), ephemeral=True)

        async with async_session_maker() as session:
            nodes = (await session.execute(select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712

        from utils.node_client import check_all_nodes
        health_map = await check_all_nodes(nodes)

        async with async_session_maker() as session:
            for node in nodes:
                health = health_map.get(node.id)
                db_node = await session.get(Node, node.id)
                if not db_node:
                    continue
                if health and health["online"]:
                    db_node.status = NodeStatus.ONLINE
                    db_node.latency = int(health.get("latency", 0))
                    db_node.last_heartbeat = datetime.now(timezone.utc)
                    result = health.get("result") or {}
                    stats = result.get("stats") or {}
                    cpu = stats.get("cpu") or {}
                    ram = stats.get("ram") or {}
                    disk = stats.get("disk") or {}
                    db_node.used_cpu = int(cpu.get("percent", 0))
                    db_node.total_cpu = int(cpu.get("cores", 0))
                    db_node.total_ram = int(ram.get("total", 0) // (1024 * 1024))
                    db_node.used_ram = int(ram.get("used", 0) // (1024 * 1024))
                    db_node.total_disk = int(disk.get("total", 0) // (1024 ** 3))
                    db_node.used_disk = int(disk.get("used", 0) // (1024 ** 3))
                    db_node.vps_count = int(stats.get("vps_count", 0))
                    virt = result.get("virtualization") or []
                    if virt:
                        db_node.set_virtualization_types(virt)
                else:
                    db_node.status = NodeStatus.OFFLINE
            await session.commit()

        online = sum(1 for n in nodes if (health_map.get(n.id) or {}).get("online"))
        embed = success_embed("Refresh Complete", f"🟢 {online} online • 🔴 {len(nodes) - online} offline")
        await msg.edit(embed=embed)

    @commands.hybrid_command(name="node-ping", description="Ping every node agent and show latency (staff)")
    @commands.guild_only()
    async def node_ping(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return
        async with async_session_maker() as session:
            nodes = (await session.execute(select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712

        if not nodes:
            await ctx.send(embed=info_embed("No Nodes", "No active nodes registered."), ephemeral=True)
            return

        from utils.node_client import check_all_nodes
        await ctx.defer(ephemeral=True)
        health_map = await check_all_nodes(nodes)

        embed = node_embed("📡 Node Latency", "Live agent ping results")
        for node in sorted(nodes, key=lambda n: n.name):
            health = health_map.get(node.id) or {}
            if health.get("online"):
                embed.add_field(
                    name=f"🟢 {node.name}",
                    value=f"**{health.get('latency', 0)}ms**",
                    inline=True,
                )
            else:
                embed.add_field(name=f"🔴 {node.name}", value="unreachable", inline=True)
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    bot.add_view(NodeActionView(0))
    await bot.add_cog(NodesCog(bot))
