import discord
from discord.ext import commands
from discord.ui import View, Button, Modal, TextInput, Select
from typing import Optional
from datetime import datetime, timezone
import asyncio

from utils import (
    success_embed, error_embed, info_embed, vps_embed, node_embed,
    has_permission, PermissionLevel, get_logger,
    generate_password, generate_short_id, format_bytes,
    validate_vps_name, validate_vps_hostname, validate_ram, validate_cpu,
    validate_disk, validate_virtualization, validate_os_template,
    log_vps_action, is_admin, is_staff,
)
from utils.node_client import NodeClient, NodeConnectionError, pick_best_node
from utils.encryption import Encryption
from database import (
    async_session_maker, VPSInstance, VPSServer, Node, NodeStatus,
    VPSStatus, VirtualizationType, get_or_create_user, User,
)
from config import settings, OS_TEMPLATES
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

logger = get_logger("vps")


def _vps_status_embed(vps: VPSInstance) -> discord.Embed:
    emoji = {
        "creating": "⏳", "running": "🟢", "stopped": "🔴",
        "restarting": "🔄", "reinstalling": "🔁", "error": "⚠️",
        "suspended": "⛔", "deleted": "🗑️",
    }.get(vps.status_value, "⚪")
    embed = vps_embed(
        f"{emoji} {vps.name}",
        f"`{vps.hostname}` • {vps.os_template}",
    )
    embed.add_field(name="ID", value=f"#{vps.id}", inline=True)
    embed.add_field(name="UUID", value=f"`{vps.uuid[:8]}`", inline=True)
    embed.add_field(name="Status", value=vps.status_value.title(), inline=True)
    embed.add_field(name="RAM", value=f"{vps.ram} MB", inline=True)
    embed.add_field(name="CPU", value=f"{vps.cpu} core(s)", inline=True)
    embed.add_field(name="Disk", value=f"{vps.disk} GB", inline=True)
    embed.add_field(name="IP", value=vps.ip_address or "*pending*", inline=True)
    embed.add_field(name="SSH Port", value=str(vps.ssh_port), inline=True)
    embed.add_field(name="Virtualization", value=vps.virtualization.value, inline=True)
    return embed


class VPSCreateWizard(discord.ui.View):
    """Simplified wizard: modal collects specs, node auto-selected by scheduler."""

    def __init__(self, requester: discord.Member):
        super().__init__(timeout=300)
        self.requester = requester

    @discord.ui.button(label="Start Creation Wizard", style=discord.ButtonStyle.primary, emoji="🚀")
    async def start(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(VPSSpecModal(self.requester))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                embed=error_embed("Not Yours", "This wizard belongs to someone else."), ephemeral=True)
            return False
        return True


class VPSSpecModal(Modal, title="Create VPS"):
    def __init__(self, requester: discord.Member):
        super().__init__()
        self.requester = requester
        self.name_in = TextInput(label="VPS Name", placeholder="my-server", required=True, max_length=64)
        self.ram_in = TextInput(label=f"RAM MB (default {settings.VPS_DEFAULT_RAM})",
                                placeholder=str(settings.VPS_DEFAULT_RAM), required=True, max_length=7)
        self.cpu_in = TextInput(label=f"CPU cores (default {settings.VPS_DEFAULT_CPU})",
                                placeholder=str(settings.VPS_DEFAULT_CPU), required=True, max_length=3)
        self.disk_in = TextInput(label=f"Disk GB (default {settings.VPS_DEFAULT_DISK})",
                                 placeholder=str(settings.VPS_DEFAULT_DISK), required=True, max_length=5)
        self.os_in = TextInput(label="OS template",
                               placeholder=settings.VPS_DEFAULT_OS, required=True, max_length=50)
        for item in (self.name_in, self.ram_in, self.cpu_in, self.disk_in, self.os_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            name = validate_vps_name(self.name_in.value)
            ram = validate_ram(self.ram_in.value)
            cpu = validate_cpu(self.cpu_in.value)
            disk = validate_disk(self.disk_in.value)
            os_template = validate_os_template(self.os_in.value)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed("Invalid Input", str(exc)), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        async with async_session_maker() as session:
            # enforce per-user limit
            owner = await get_or_create_user(session, self.requester.id, self.requester.name)
            count = await session.execute(
                select(func.count(VPSInstance.id)).where(
                    VPSInstance.owner_id == owner.id,
                    VPSInstance.deleted_at.is_(None),
                )
            )
            owned = int(count.scalar() or 0)
            if owned >= settings.VPS_MAX_PER_USER and not has_permission(self.requester, PermissionLevel.STAFF):
                await interaction.followup.send(
                    embed=error_embed("Limit Reached",
                                      f"You already own **{owned}** VPS (max {settings.VPS_MAX_PER_USER})."),
                    ephemeral=True)
                return

            # schedule: pick least-loaded online node
            nodes = (await session.execute(select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712
            node = await pick_best_node(nodes, required={"ram": ram, "disk": disk})
            if node is None:
                await interaction.followup.send(
                    embed=error_embed(
                        "No Capacity",
                        "No online node currently has enough free resources for this plan. "
                        "Try smaller specs or ask an admin to add nodes (`!node-add`).",
                    ), ephemeral=True)
                return

            # unique hostname
            base_hostname = f"{name.lower().replace(' ', '-')}-{generate_short_id(4).lower()}"
            hostname = base_hostname
            suffix = 1
            while (await session.execute(
                    select(VPSInstance.id).where(VPSInstance.hostname == hostname))).scalar_one_or_none():
                hostname = f"{base_hostname}-{suffix}"
                suffix += 1

            virt = VirtualizationType(settings.VPS_DEFAULT_VIRTUALIZATION.replace("lxc_", "lxd_")
                                      if "lxc_" in settings.VPS_DEFAULT_VIRTUALIZATION
                                      else settings.VPS_DEFAULT_VIRTUALIZATION)
            root_password = generate_password(16)

            vps = VPSInstance(
                name=name,
                hostname=hostname,
                owner_id=owner.id,
                node_id=node.id,
                ram=ram, cpu=cpu, disk=disk,
                os_template=os_template,
                virtualization=virt,
                root_password=Encryption.encrypt(root_password),
                status=VPSStatus.CREATING,
                ssh_port=22,
            )
            session.add(vps)
            await session.commit()
            await session.refresh(vps)
            vps_id, vps_uuid, node_id = vps.id, vps.uuid, node.id
            node_name = node.name

        log_vps_action("CREATE_QUEUED", vps_id, self.requester.id, node_id)

        # Provision through the node agent (async, fire-and-poll)
        try:
            client = NodeClient(node_obj := await self._get_node(node_id))
            result = await client.create_vps(
                uuid=vps_uuid,
                name=name,
                hostname=hostname,
                os_template=os_template,
                ram=ram,
                cpu=cpu,
                disk=disk,
                virtualization=virt.value,
                ssh_port=22,
                root_password=root_password,
            )
            ip = result.get("ip_address")
            status = result.get("status", "creating")

            # Provisioning is queued in the agent's background — poll until it
            # leaves the creating state so the real IP/status actually lands in
            # the DB instead of staying NULL forever.
            if status == "creating":
                for _ in range(24):  # up to ~2 minutes
                    await asyncio.sleep(5)
                    try:
                        poll = await client.get_vps(vps_uuid)
                    except NodeConnectionError:
                        break
                    ip = poll.get("ip_address") or ip
                    status = poll.get("status", status)
                    if status != "creating":
                        break

            async with async_session_maker() as session:
                db_vps = await session.get(VPSInstance, vps_id)
                if db_vps:
                    if ip:
                        db_vps.ip_address = ip
                    if status == "running":
                        db_vps.status = VPSStatus.RUNNING
                    elif status == "error":
                        db_vps.status = VPSStatus.ERROR
                await session.commit()
        except NodeConnectionError as exc:
            async with async_session_maker() as session:
                db_vps = await session.get(VPSInstance, vps_id)
                if db_vps:
                    db_vps.status = VPSStatus.ERROR
                await session.commit()
            await interaction.followup.send(
                embed=error_embed(
                    "Provisioning Failed",
                    f"VPS **#{vps_id}** was registered but the node agent rejected the job:\n> {exc}\n"
                    f"It will retry when the node recovers — contact staff if it stays in error.",
                ), ephemeral=True)
            return

        embed = success_embed("🚀 VPS Created", f"**{name}** deployed on node **{node_name}**")
        embed.add_field(name="ID", value=f"#{vps_id}", inline=True)
        embed.add_field(name="Hostname", value=f"`{hostname}`", inline=True)
        embed.add_field(name="Specs", value=f"{ram}MB • {cpu} core • {disk}GB", inline=True)
        embed.add_field(name="IP", value=ip or "*assigned shortly*", inline=True)
        embed.add_field(name="Root Password", value=f"||{root_password}||", inline=False)
        embed.set_footer(text="Store this password safely — it is shown only once (encrypted at rest).")
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _get_node(self, node_id: int) -> Node:
        async with async_session_maker() as session:
            return await session.get(Node, node_id)


class RootPasswordModal(Modal, title="Show Root Password — Confirm"):
    def __init__(self, vps_id: int):
        super().__init__()
        self.vps_id = vps_id
        self.confirm = TextInput(label="Type SHOW to reveal", placeholder="SHOW", required=True, max_length=5)
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().upper() != "SHOW":
            await interaction.response.send_message(embed=error_embed("Cancelled", "Request cancelled"), ephemeral=True)
            return

        async with async_session_maker() as session:
            vps = (await session.execute(
                select(VPSInstance)
                .options(selectinload(VPSInstance.owner))
                .where(VPSInstance.id == self.vps_id)
            )).scalar_one_or_none()
            if not vps:
                await interaction.response.send_message(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
                return
            if vps.owner.discord_id != interaction.user.id and not is_staff(interaction.user):
                await interaction.response.send_message(embed=error_embed("Denied", "Not your VPS"), ephemeral=True)
                return
            raw = Encryption.decrypt(vps.root_password) if vps.root_password else None

        if not raw:
            await interaction.response.send_message(
                embed=error_embed("No Password", "No stored password for this VPS."), ephemeral=True)
            return

        embed = success_embed(f"🔑 Credentials — {vps.name}",
                              f"IP: `{vps.ip_address or vps.hostname}` • Port: `{vps.ssh_port}`")
        embed.add_field(name="Root Password", value=f"||{raw}||", inline=False)
        embed.set_footer(text="Shown once per request. Consider changing it after first login.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


class VPSActionView(View):
    def __init__(self, vps_id: int):
        super().__init__(timeout=None)
        self.vps_id = vps_id

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", custom_id="arqon:vps_start")
    async def start_btn(self, interaction: discord.Interaction, button: Button):
        await self._power(interaction, "start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.secondary, emoji="⏹️", custom_id="arqon:vps_stop")
    async def stop_btn(self, interaction: discord.Interaction, button: Button):
        await self._power(interaction, "stop")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔄", custom_id="arqon:vps_restart")
    async def restart_btn(self, interaction: discord.Interaction, button: Button):
        await self._power(interaction, "restart")

    @discord.ui.button(label="Credentials", style=discord.ButtonStyle.secondary, emoji="🔑", custom_id="arqon:vps_creds")
    async def creds_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(RootPasswordModal(self.vps_id))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, emoji="🗑️", custom_id="arqon:vps_delete")
    async def delete_btn(self, interaction: discord.Interaction, button: Button):
        await self._delete(interaction)

    async def _load_vps(self) -> Optional[VPSInstance]:
        async with async_session_maker() as session:
            # Eager-load owner: touching vps.owner on a lazy relationship in
            # async SQLAlchemy raises MissingGreenlet and crashed every action.
            vps = (await session.execute(
                select(VPSInstance)
                .options(selectinload(VPSInstance.owner))
                .where(VPSInstance.id == self.vps_id)
            )).scalar_one_or_none()
            return vps

    async def _authorize(self, interaction: discord.Interaction, vps: VPSInstance) -> bool:
        if vps.owner.discord_id == interaction.user.id:
            return True
        if isinstance(interaction.user, discord.Member) and is_staff(interaction.user):
            return True
        # _power/_delete already deferred the interaction — response.send_message
        # here raised InteractionResponded and the denial was never delivered.
        await interaction.followup.send(
            embed=error_embed("Permission Denied", "You don't own this VPS."), ephemeral=True)
        return False

    async def _power(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer(ephemeral=True)
        vps = await self._load_vps()
        if not vps:
            await interaction.followup.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
            return
        if not await self._authorize(interaction, vps):
            return

        vps_id, vps_uuid = vps.id, vps.uuid
        async with async_session_maker() as session:
            node = await session.get(Node, vps.node_id)
            node_name = node.name if node else "?"

        try:
            client = NodeClient(await self._get_node(vps.node_id))
            if action == "start":
                result = await client.start_vps(vps_uuid)
            elif action == "stop":
                result = await client.stop_vps(vps_uuid)
            else:
                result = await client.restart_vps(vps_uuid)
        except NodeConnectionError as exc:
            await interaction.followup.send(
                embed=error_embed("Node Unreachable", f"Action failed: {exc}"), ephemeral=True)
            return

        new_status = {"start": VPSStatus.RUNNING, "stop": VPSStatus.STOPPED,
                      "restart": VPSStatus.RUNNING}[action]
        async with async_session_maker() as session:
            db_vps = await session.get(VPSInstance, vps_id)
            if db_vps:
                db_vps.status = new_status
                await session.commit()

        log_vps_action(action.upper(), vps_id, interaction.user.id)
        await interaction.followup.send(
            embed=success_embed(f"{action.title()} Successful",
                                f"**{vps.name}** on node **{node_name}**: {result.get('message', action + 'ed')}"),
            ephemeral=True)

    async def _delete(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        vps = await self._load_vps()
        if not vps:
            await interaction.followup.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
            return
        if not await self._authorize(interaction, vps):
            return

        vps_id, vps_uuid, vps_name = vps.id, vps.uuid, vps.name

        try:
            client = NodeClient(await self._get_node(vps.node_id))
            await client.delete_vps(vps_uuid)
        except NodeConnectionError as exc:
            logger.warning(f"Delete call failed for #{vps_id}: {exc} — marking deleted anyway")

        async with async_session_maker() as session:
            db_vps = await session.get(VPSInstance, vps_id)
            if db_vps:
                db_vps.status = VPSStatus.DELETED
                db_vps.deleted_at = datetime.now(timezone.utc)
                await session.commit()

        log_vps_action("DELETE", vps_id, interaction.user.id)
        await interaction.followup.send(
            embed=success_embed("🗑️ Deleted", f"**{vps_name}** has been permanently removed."),
            ephemeral=True)

    async def _get_node(self, node_id: int) -> Node:
        async with async_session_maker() as session:
            return await session.get(Node, node_id)


class VPSReinstallModal(Modal, title="Reinstall VPS"):
    def __init__(self, vps_id: int):
        super().__init__()
        self.vps_id = vps_id
        self.os_in = TextInput(label="New OS template (blank = same)",
                               placeholder=settings.VPS_DEFAULT_OS, required=False, max_length=50)
        self.confirm = TextInput(label="Type REINSTALL to confirm", placeholder="REINSTALL", required=True, max_length=10)
        self.add_item(self.os_in)
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().upper() != "REINSTALL":
            await interaction.response.send_message(embed=error_embed("Cancelled", "Reinstall cancelled"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)

        async with async_session_maker() as session:
            vps = (await session.execute(
                select(VPSInstance)
                .options(selectinload(VPSInstance.owner))
                .where(VPSInstance.id == self.vps_id)
            )).scalar_one_or_none()
            if not vps:
                await interaction.followup.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
                return
            if vps.owner.discord_id != interaction.user.id and not is_staff(interaction.user):
                await interaction.followup.send(embed=error_embed("Denied", "Not your VPS"), ephemeral=True)
                return
            os_template = vps.os_template

        new_os = self.os_in.value.strip()
        if new_os:
            try:
                os_template = validate_os_template(new_os)
            except ValueError as exc:
                await interaction.followup.send(embed=error_embed("Invalid Input", str(exc)), ephemeral=True)
                return

        try:
            client = NodeClient(await self._get_node(vps.node_id))
            await client.reinstall_vps(vps.uuid, os_template)
        except NodeConnectionError as exc:
            await interaction.followup.send(embed=error_embed("Reinstall Failed", str(exc)), ephemeral=True)
            return

        async with async_session_maker() as session:
            db_vps = await session.get(VPSInstance, self.vps_id)
            if db_vps:
                db_vps.os_template = os_template
                db_vps.status = VPSStatus.REINSTALLING
                await session.commit()

        log_vps_action("REINSTALL", self.vps_id, interaction.user.id)
        await interaction.followup.send(
            embed=success_embed("🔁 Reinstall Started", f"**{vps.name}** is reinstalling **{os_template}**."),
            ephemeral=True)

    async def _get_node(self, node_id: int) -> Node:
        async with async_session_maker() as session:
            return await session.get(Node, node_id)


class VPSCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._create_cooldowns = {}

    @commands.hybrid_command(name="create", description="Create a new VPS with custom specs (staff)")
    @commands.guild_only()
    async def create(self, ctx: commands.Context):
        # Custom-spec creation bypasses the invite economy — staff only.
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed(
                "Staff Only",
                "Custom-spec VPS creation is a staff tool.\n"
                "Members can earn servers with invites via `/plans`."
            ), ephemeral=True)
            return
        # per-user cooldown (uses the GuildConfig/global cooldown_create_vps)
        cooldown = settings.COOLDOWN_CREATE_VPS
        last = self._create_cooldowns.get(ctx.author.id, 0.0)
        now = asyncio.get_event_loop().time()
        wait = cooldown - (now - last)
        if wait > 0:
            await ctx.send(embed=error_embed(
                "Slow Down", f"Please wait **{int(wait)}s** before creating another VPS."),
                ephemeral=True)
            return
        self._create_cooldowns[ctx.author.id] = now
        embed = vps_embed(
            "🚀 VPS Creation Wizard",
            f"Deploy a cloud server in under a minute.\n\n"
            f"**Default specs** — RAM {settings.VPS_DEFAULT_RAM}MB • CPU {settings.VPS_DEFAULT_CPU} core • "
            f"Disk {settings.VPS_DEFAULT_DISK}GB • OS {settings.VPS_DEFAULT_OS}\n"
            f"**Limit** — {settings.VPS_MAX_PER_USER} VPS per user\n\n"
            f"Press **Start Creation Wizard** below to begin.",
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed, view=VPSCreateWizard(ctx.author), ephemeral=True)

    @commands.hybrid_command(name="myvps", description="List your VPS instances")
    @commands.guild_only()
    async def myvps(self, ctx: commands.Context):
        async with async_session_maker() as session:
            owner = await get_or_create_user(session, ctx.author.id, ctx.author.name)
            result = await session.execute(
                select(VPSInstance)
                .where(VPSInstance.owner_id == owner.id, VPSInstance.deleted_at.is_(None))
                .order_by(VPSInstance.created_at.desc())
            )
            vps_list = result.scalars().all()

        if not vps_list:
            await ctx.send(embed=info_embed("No VPS", "You don't own any VPS yet. Use `!create` to deploy one!"),
                           ephemeral=True)
            return

        embed = vps_embed(f"🖥️ Your VPS ({len(vps_list)})", None)
        for vps in vps_list[:10]:
            emoji = {"running": "🟢", "stopped": "🔴", "creating": "⏳", "error": "⚠️",
                     "reinstalling": "🔁", "restarting": "🔄"}.get(vps.status_value, "⚪")
            embed.add_field(
                name=f"{emoji} {vps.name} • #{vps.id}",
                value=(f"`{vps.hostname}` • {vps.ip_address or 'IP pending'}\n"
                       f"{vps.ram}MB RAM • {vps.cpu} core • {vps.disk}GB • {vps.os_template}"),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="listvps", description="List all VPS (staff)")
    @commands.guild_only()
    async def listvps(self, ctx: commands.Context, node_id: Optional[int] = None):
        if not is_staff(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        async with async_session_maker() as session:
            query = (select(VPSInstance, User)
                     .join(User, VPSInstance.owner_id == User.id)
                     .where(VPSInstance.deleted_at.is_(None)))
            if node_id:
                query = query.where(VPSInstance.node_id == node_id)
            query = query.order_by(VPSInstance.created_at.desc()).limit(25)
            rows = (await session.execute(query)).all()

        if not rows:
            await ctx.send(embed=info_embed("No VPS", "No active VPS found."), ephemeral=True)
            return

        embed = vps_embed(f"📋 All VPS ({len(rows)})", None)
        for vps, owner in rows:
            emoji = "🟢" if vps.status == VPSStatus.RUNNING else "🔴"
            embed.add_field(
                name=f"{emoji} #{vps.id} {vps.name}",
                value=(f"`{vps.hostname}` • {vps.ip_address or '—'} • node #{vps.node_id}\n"
                       f"owner: <@{owner.discord_id}> • {vps.status_value}"),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="vps", description="VPS details + action buttons")
    @commands.guild_only()
    async def vps_info(self, ctx: commands.Context, vps_id: int):
        async with async_session_maker() as session:
            # eager-load owner (lazy load would crash the async session)
            vps = (await session.execute(
                select(VPSInstance)
                .options(selectinload(VPSInstance.owner))
                .where(VPSInstance.id == vps_id)
            )).scalar_one_or_none()
        if not vps:
            await ctx.send(embed=error_embed("Not Found", "VPS not found"), ephemeral=True)
            return
        if vps.owner.discord_id != ctx.author.id and not is_staff(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "You don't own this VPS."), ephemeral=True)
            return

        embed = _vps_status_embed(vps)
        await ctx.send(embed=embed, view=VPSActionView(vps.id), ephemeral=True)

    @commands.hybrid_command(name="reinstall", description="Reinstall VPS OS")
    @commands.guild_only()
    async def reinstall(self, ctx: commands.Context, vps_id: int):
        await ctx.send_modal(VPSReinstallModal(vps_id))

    @commands.hybrid_command(name="ostemplates", description="List available OS templates")
    async def os_templates(self, ctx: commands.Context):
        lines = [f"• **{key}** — {name}" for key, name in OS_TEMPLATES.items()]
        embed = info_embed("💾 OS Templates", "\n".join(lines))
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    bot.add_view(VPSActionView(0))
    await bot.add_cog(VPSCog(bot))
