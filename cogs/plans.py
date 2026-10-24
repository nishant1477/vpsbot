"""Arqon Bot — interactive plans & invite-earned VPS deployment.

/plans now runs a fully button-driven flow:

    /plans  →  plan cards + [Claim] buttons
            →  ✅ eligibility check (invites)
            →  💿 OS dropdown (select menu — NO text box anywhere)
            →  📋 summary + [Deploy Now]
            →  ⚙️ animated provisioning loader (live progress)
            →  🚀 VPS credentials card

Admin extras: /plan-add, /plan-remove, /plan-list, /plan-seed
"""
import discord
from discord.ext import commands
from typing import Optional
import asyncio

from utils import (
    success_embed, error_embed, info_embed, purple_embed, vps_embed, warning_embed,
    is_admin, is_staff, get_logger,
    generate_password, generate_short_id,
)
from utils.emoji import E
from utils.node_client import NodeClient, NodeConnectionError, pick_best_node
from utils.encryption import Encryption
from database import (
    async_session_maker, VPSInstance, Node, VPSStatus, VirtualizationType,
    Plan, InviteStat, get_or_create_user,
)
from config import settings, OS_TEMPLATES
from sqlalchemy import select, func, update as sa_update, case as sa_case
import re as _re

logger = get_logger("plans")

# Plan-name sanitiser for hostnames: only [a-z0-9-] may reach a hostname.
_HOSTNAME_SAFE = _re.compile(r"[^a-z0-9-]+")

# OS emoji shortcuts for the dropdown
_OS_EMOJIS = {
    "ubuntu": "🟠", "debian": "🔴", "almalinux": "🔵", "rockylinux": "🪨",
    "centos": "🟢", "alpine": "🏔️", "fedora": "🟣", "arch": "🏔️",
}


def _os_emoji(key: str) -> str:
    base = key.split("-")[0].lower()
    return _OS_EMOJIS.get(base, "💿")


def _loading_embed(step_idx: int, plan: Plan, os_label: str) -> discord.Embed:
    """Animated provisioning loader — progress bar + rotating step text."""
    steps = [
        f"{E('gear')} Contacting node agent…",
        f"{E('package')} Allocating resources…",
        f"{E('os')} Installing {os_label}…",
        f"{E('network')} Configuring network & SSH…",
        f"{E('lock')} Generating root credentials…",
        f"{E('sparkles')} Finalizing deployment…",
    ]
    pct = min(100, int((step_idx + 1) / len(steps) * 100))
    bar = "▰" * int(pct // 10) + "▱" * (10 - int(pct // 10))

    embed = vps_embed(
        f"{E('loading')} Deploying **{plan.name}**…",
        f"**{bar}**  {pct}%\n\n{steps[step_idx % len(steps)]}",
    )
    embed.add_field(name=f"{E('plans')} Plan", value=plan.name, inline=True)
    embed.add_field(name=f"{E('os')} Operating System", value=os_label, inline=True)
    embed.add_field(name=f"{E('clock')} ETA", value="~1-2 minutes", inline=True)
    embed.set_footer(text=f"{settings.BOT_FOOTER} • provisioning in real time")
    return embed


class PlanSelectView(discord.ui.View):
    """[Claim] buttons for up to 5 plans; a dropdown appears if there are more."""

    def __init__(self, bot, requester: discord.Member, plans: list):
        super().__init__(timeout=240)
        self.bot = bot
        self.requester = requester
        self.plans = plans
        for i, plan in enumerate(plans[:5]):
            btn = discord.ui.Button(
                label=f"{plan.name}",
                style=discord.ButtonStyle.primary if i == 0 else discord.ButtonStyle.secondary,
                emoji=E("rocket") if i == 0 else E("package"),
                custom_id=f"arqon:plan_claim:{plan.id}",
            )
            btn.callback = self._make_callback(plan)
            self.add_item(btn)
        if len(plans) > 5:
            select = discord.ui.Select(
                placeholder=f"{E('plans')} More plans…",
                options=[
                    discord.SelectOption(
                        label=f"{p.name} — {p.required_invites} invites",
                        value=str(p.id),
                        description=f"{p.ram}MB RAM • {p.cpu} vCPU • {p.disk}GB SSD",
                        emoji=_os_emoji(p.name),
                    ) for p in plans[5:25]
                ],
            )
            select.callback = self._select_callback
            self.add_item(select)

    def _make_callback(self, plan: Plan):
        async def cb(interaction: discord.Interaction):
            await self._claim(interaction, plan)
        return cb

    async def _select_callback(self, interaction: discord.Interaction):
        plan_id = int(interaction.data["values"][0])
        plan = next((p for p in self.plans if p.id == plan_id), None)
        if plan:
            await self._claim(interaction, plan)

    async def _claim(self, interaction: discord.Interaction, plan: Plan):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                embed=error_embed("Not Yours", f"This panel belongs to **{self.requester.display_name}**."),
                ephemeral=True)
            return

        from cogs.invites import InvitesCog  # circular-safe lazy import
        invites_cog: Optional[InvitesCog] = self.bot.get_cog("InvitesCog")
        available = 0
        if invites_cog:
            stat = await invites_cog._total_invites(interaction.guild_id, interaction.user.id)
            available = stat.available

        bypass = is_staff(self.requester)
        if not bypass and available < plan.required_invites:
            missing = plan.required_invites - available
            await interaction.response.send_message(
                embed=error_embed(
                    f"{E('lock')} Not Enough Invites",
                    f"**{plan.name}** needs **{plan.required_invites}** invites — you have **{available}**.\n\n"
                    f"{E('invite')} Invite **{missing}** more friend{'s' if missing != 1 else ''} to this server "
                    f"and come back!\nCheck progress anytime with `/invites`.",
                ), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(
            embed=purple_embed(
                f"{E('os')} Choose an Operating System",
                f"Selected plan: **{plan.name}** — {plan.ram}MB RAM • {plan.cpu} vCPU • {plan.disk}GB SSD\n\n"
                f"{E('search')} **Pick the OS you want from the menu below** — no typing needed. "
                f"More distros can be added by admins in `config.py → OS_TEMPLATES`.",
            ),
            view=OSSelectView(self.requester, plan),
        )


class OSSelectView(discord.ui.View):
    """The OS picker — a proper select menu, exactly what the user asked for."""

    def __init__(self, requester: discord.Member, plan: Plan):
        super().__init__(timeout=180)
        self.requester = requester
        self.plan = plan

        options = [
            discord.SelectOption(
                label=friendly,
                value=key,
                description=f"Deploy {plan.name} with {friendly}",
                emoji=_os_emoji(key),
            )
            for key, friendly in OS_TEMPLATES.items()
        ][:25]

        select = discord.ui.Select(
            placeholder=f"{E('os')} Select the OS for your new VPS…",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, emoji=E("error"))
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                embed=error_embed("Not Yours", "This menu belongs to someone else."), ephemeral=True)
            return False
        return True

    async def _on_cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            embed=info_embed(f"{E('info')} Cancelled", "No worries — your invites were **not** spent. "
                             f"Run `/plans` again anytime!"), view=None)

    async def _on_select(self, interaction: discord.Interaction):
        os_key = interaction.data["values"][0]
        os_label = OS_TEMPLATES.get(os_key, os_key)
        await interaction.response.edit_message(
            embed=purple_embed(
                f"{E('list')} Confirm Deployment",
                f"{E('plans')} **Plan:** {self.plan.name}\n"
                f"{E('ram')} RAM: **{self.plan.ram} MB**\n"
                f"{E('cpu')} CPU: **{self.plan.cpu} vCore{'s' if self.plan.cpu != 1 else ''}**\n"
                f"{E('disk')} SSD: **{self.plan.disk} GB** NVMe\n"
                f"{E('os')} OS: **{os_label}**\n\n"
                f"{E('invite')} Cost: **{self.plan.required_invites} invites**"
                f"{' *(staff bypass)*' if is_staff(self.requester) else ''}",
            ),
            view=DeployConfirmView(self.requester, self.plan, os_key, os_label),
        )


class DeployConfirmView(discord.ui.View):
    def __init__(self, requester: discord.Member, plan: Plan, os_key: str, os_label: str):
        super().__init__(timeout=120)
        self.requester = requester
        self.plan = plan
        self.os_key = os_key
        self.os_label = os_label
        self._deploying = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                embed=error_embed("Not Yours", "This panel belongs to someone else."), ephemeral=True)
            return False
        if self._deploying:
            await interaction.response.send_message(
                embed=info_embed(f"{E('loading')} Hold on", "A deployment is already running."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Deploy Now", style=discord.ButtonStyle.success)
    async def deploy(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._deploying = True
        button.disabled = True
        await interaction.response.edit_message(
            embed=_loading_embed(0, self.plan, self.os_label), view=self)

        try:
            result = await run_plan_deployment(interaction, self.requester, self.plan,
                                               self.os_key, self.os_label,
                                               progress_cb=self._progress)
        except Exception as exc:
            logger.error(f"Plan deployment failed: {exc}", exc_info=exc)
            await _safe_edit(interaction, error_embed(
                f"{E('error')} Deployment Failed",
                f"Something went wrong while provisioning your VPS:\n> {exc}\n\n"
                f"Your invites were **not** spent — please try again or open a ticket with {E('ticket')}."))
            return

        if isinstance(result, str):  # error code
            errors = {
                "limit": f"You already own **{settings.VPS_MAX_PER_USER}** VPS — the maximum per member. "
                         f"{E('info')} Delete one with `/myvps` first.",
                "invites": f"You no longer have enough invites for **{self.plan.name}** — another "
                           f"deployment already used them. {E('invite')} Check `/invites`.",
                "capacity": f"No online node currently has enough free capacity for **{self.plan.name}**. "
                            f"{E('node')} Ask staff to add nodes or try again shortly.",
                "agent": f"The node agent rejected the provisioning job. "
                         f"{E('tools')} Your invites were **not** spent — staff have been notified.",
            }
            await _safe_edit(interaction, error_embed(
                f"{E('error')} Deployment Problem", errors.get(result, result)))
            return

        vps_id, hostname, root_password, node_name, ip = result
        embed = success_embed(
            f"{E('rocket')} VPS Deployed — {self.plan.name}",
            f"Your server is **live**! {E('fire')}\n\n"
            f"{E('vps')} **Name:** {self.plan.name}-{vps_id}\n"
            f"{E('server')} **Hostname:** `{hostname}`\n"
            f"{E('node')} **Node:** {node_name}\n"
            f"{E('globe')} **IP:** `{ip or 'assigned shortly'}`\n"
            f"{E('os')} **OS:** {self.os_label}\n"
            f"{E('ram')} **{self.plan.ram}MB** • {E('cpu')} **{self.plan.cpu} vCore** • {E('disk')} **{self.plan.disk}GB SSD**",
        )
        embed.add_field(name=f"{E('key')} Root Password",
                        value=f"||`{root_password}`||  *(click to reveal — shown only once!)*", inline=False)
        embed.add_field(name=f"{E('terminal')} Connect",
                        value=f"`ssh root@{ip or hostname}`", inline=True)
        embed.add_field(name=f"{E('invoice')} Spent",
                        value=f"{self.plan.required_invites} invites", inline=True)
        embed.set_footer(text=f"{settings.BOT_FOOTER} • manage it anytime with /myvps and /vps {vps_id}")
        await _safe_edit(interaction, embed, view=None)

    async def _progress(self, step_idx: int):
        pass  # animation handled by run_plan_deployment

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=info_embed(f"{E('info')} Cancelled", "Deployment aborted — invites untouched. "
                             f"Run `/plans` again anytime!"), view=None)


async def _safe_edit(interaction: discord.Interaction, embed: discord.Embed, view=None):
    try:
        await interaction.edit_original_response(embed=embed, view=view)
    except (discord.NotFound, discord.HTTPException):
        pass


async def run_plan_deployment(interaction, requester: discord.Member, plan: Plan,
                              os_key: str, os_label: str, progress_cb=None):
    """Full provisioning pipeline for an invite-plan order.

    Returns (vps_id, hostname, root_password, node_name, ip) on success,
    or a short error code string ("limit" | "capacity" | "agent" | "invites").

    Invite cost is RESERVED atomically BEFORE provisioning (a single UPDATE
    with an availability condition) and refunded if provisioning fails — so
    firing many deploy panels at once can never double-spend invites.
    """
    ram, cpu, disk = plan.ram, plan.cpu, plan.disk
    bypass = is_staff(requester)
    reserved = 0

    # ── atomic invite reservation (concurrency-safe) ───────────────────
    if not bypass and plan.required_invites > 0:
        async with async_session_maker() as session:
            res = await session.execute(
                sa_update(InviteStat)
                .where(
                    InviteStat.guild_id == interaction.guild_id,
                    InviteStat.inviter_id == requester.id,
                    (InviteStat.real_invites + InviteStat.bonus_invites
                     - InviteStat.invites_spent) >= plan.required_invites,
                )
                .values(invites_spent=InviteStat.invites_spent + plan.required_invites)
            )
            await session.commit()
            reserved = res.rowcount or 0
        if not reserved:
            return "invites"

    async with async_session_maker() as session:
        owner = await get_or_create_user(session, requester.id, requester.name)

        # per-user limit
        owned = int((await session.execute(
            select(func.count(VPSInstance.id)).where(
                VPSInstance.owner_id == owner.id,
                VPSInstance.deleted_at.is_(None),
            ))).scalar() or 0)
        if owned >= settings.VPS_MAX_PER_USER and not is_staff(requester):
            return "limit"

        # schedule on the least-loaded online node
        nodes = (await session.execute(
            select(Node).where(Node.is_active == True))).scalars().all()  # noqa: E712
        node = await pick_best_node(nodes, required={"ram": ram, "disk": disk})
        if node is None:
            return "capacity"

        # unique hostname (plan name sanitised — only [a-z0-9-] may land in one)
        safe_name = _HOSTNAME_SAFE.sub("-", plan.name.lower().replace(" ", "-")).strip("-") or "vps"
        base = f"{safe_name}-{generate_short_id(4).lower()}"
        hostname, suffix = base, 1
        while (await session.execute(
                select(VPSInstance.id).where(VPSInstance.hostname == hostname))).scalar_one_or_none():
            hostname = f"{base}-{suffix}"
            suffix += 1

        virt = VirtualizationType(settings.VPS_DEFAULT_VIRTUALIZATION)
        root_password = generate_password(16)

        name = f"{plan.name}-{generate_short_id(3).upper()}"
        vps = VPSInstance(
            name=name, hostname=hostname, owner_id=owner.id, node_id=node.id,
            ram=ram, cpu=cpu, disk=disk, os_template=os_key,
            virtualization=virt, root_password=Encryption.encrypt(root_password),
            status=VPSStatus.CREATING, ssh_port=22,
        )
        session.add(vps)
        await session.commit()
        await session.refresh(vps)
        vps_id, vps_uuid, node_id, node_name = vps.id, vps.uuid, node.id, node.name

    # ── provision with animated loader ────────────────────────────────
    client = NodeClient(node)
    deploy_error: Optional[Exception] = None
    result: dict = {}
    finished = False

    async def provision():
        nonlocal deploy_error, result, finished
        try:
            res = await client.create_vps(
                uuid=vps_uuid, name=name, hostname=hostname,
                os_template=os_key, ram=ram, cpu=cpu, disk=disk,
                virtualization=virt.value, ssh_port=22,
                root_password=root_password,
            )
            result = res
            if res.get("status") == "creating":
                for _ in range(24):  # poll up to ~2 minutes
                    await asyncio.sleep(5)
                    try:
                        poll = await client.get_vps(vps_uuid)
                    except NodeConnectionError:
                        break
                    result["ip_address"] = poll.get("ip_address") or result.get("ip_address")
                    result["status"] = poll.get("status", result.get("status"))
                    if result.get("status") != "creating":
                        break
        except NodeConnectionError as exc:
            deploy_error = exc
        except Exception as exc:
            logger.error(f"Provisioning crashed: {exc}", exc_info=exc)
            deploy_error = exc
        finally:
            finished = True

    task = asyncio.create_task(provision())

    # animation loop — rotate loader frames every ~4s while provisioning runs
    step = 0
    while not finished:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=4.0)
            break
        except asyncio.TimeoutError:
            step += 1
            await _safe_edit(interaction, _loading_embed(step, plan, os_label), view=None)
        except Exception:
            break  # task raised — handled below via deploy_error

    if deploy_error is not None:
        async with async_session_maker() as session:
            db_vps = await session.get(VPSInstance, vps_id)
            if db_vps:
                db_vps.status = VPSStatus.ERROR
            await session.commit()
        if reserved:
            await _refund_invites(interaction, requester, reserved)
        raise deploy_error

    ip = result.get("ip_address")
    status = result.get("status", "creating")

    if status == "error":
        # node itself reported a failed provisioning — refund, do NOT charge
        async with async_session_maker() as session:
            db_vps = await session.get(VPSInstance, vps_id)
            if db_vps:
                db_vps.status = VPSStatus.ERROR
            await session.commit()
        if reserved:
            await _refund_invites(interaction, requester, reserved)
        raise RuntimeError("The node agent reported a provisioning error.")

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

    # invites were already reserved atomically before provisioning — nothing
    # more to charge here; staff bypass never reserves.
    return vps_id, hostname, root_password, node_name, ip


async def _refund_invites(interaction, requester: discord.Member, amount: int) -> None:
    """Return reserved invites when a deployment fails (never below zero)."""
    try:
        async with async_session_maker() as session:
            await session.execute(
                sa_update(InviteStat)
                .where(InviteStat.guild_id == interaction.guild_id,
                       InviteStat.inviter_id == requester.id)
                .values(invites_spent=sa_case(
                    (InviteStat.invites_spent - amount < 0, 0),
                    else_=InviteStat.invites_spent - amount))
            )
            await session.commit()
    except Exception as exc:
        logger.error(f"invite refund failed for {requester.id}: {exc}")


class PlansCog(commands.Cog):
    """Invite-earned plans: /plans, plan management."""

    def __init__(self, bot):
        self.bot = bot

    # ── public ───────────────────────────────────────────────────────

    @commands.hybrid_command(name="plans", description="View hosting plans & claim one with your invites")
    @commands.guild_only()
    async def plans(self, ctx: commands.Context):
        async with async_session_maker() as session:
            plan_list = (await session.execute(
                select(Plan).where(Plan.is_active == True)  # noqa: E712
                .order_by(Plan.sort_order, Plan.required_invites))).scalars().all()

        if not plan_list:
            await ctx.send(embed=info_embed(
                f"{E('plans')} Hosting Plans",
                f"No plans are configured yet. {E('tools')} Admins can run `/plan-seed` to load the "
                f"default invite tiers, or create custom ones with `/plan-add`."), ephemeral=True)
            return

        # user's invite balance banner
        stat = None
        cog = self.bot.get_cog("InvitesCog")
        if cog:
            stat = await cog._total_invites(ctx.guild.id, ctx.author.id)
        available = stat.available if stat else 0

        embed = purple_embed(
            f"{E('plans')} Arqon Host — Plans",
            f"{E('sparkles')} **Earn your VPS with invites!** Every friend you invite "
            f"unlocks bigger and faster servers.\n\n"
            f"{E('invite')} Your available invites: **{available}**\n"
            f"{E('search')} Progress: `/invites` • Leaderboard: `/inviteleaderboard`",
        )

        for p in plan_list:
            locked = p.required_invites > available and not is_staff(ctx.author)
            badge = f"{E('lock')} *need {p.required_invites - available} more*" if locked \
                else f"{E('success')} **ready to claim**"
            embed.add_field(
                name=f"{E('rocket')} {p.name} — {p.required_invites} invites",
                value=(f"{E('ram')} {p.ram}MB • {E('cpu')} {p.cpu} vCore • {E('disk')} {p.disk}GB SSD\n"
                       f"{badge}"),
                inline=True,
            )

        embed.set_footer(text=f"{settings.BOT_FOOTER} • pick a plan below to get started")
        view = PlanSelectView(self.bot, ctx.author, plan_list)
        await ctx.send(embed=embed, view=view)

    # ── admin plan management ────────────────────────────────────────

    @commands.hybrid_command(name="plan-add", description="Create a hosting plan (Admin)")
    @commands.guild_only()
    async def plan_add(self, ctx: commands.Context, name: str, ram_mb: int, disk_gb: int,
                       cpu_cores: int, required_invites: int):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return
        name = (name or "").strip()
        if not name or len(name) > 60 or "\n" in name:
            await ctx.send(embed=error_embed("Invalid Name",
                             "Plan name must be 1-60 characters (single line)."), ephemeral=True)
            return
        if ram_mb < 128 or ram_mb > 262144 or disk_gb < 2 or disk_gb > 4096 \
                or cpu_cores < 1 or cpu_cores > 64 or required_invites < 0 \
                or required_invites > 100000:
            await ctx.send(embed=error_embed("Invalid Specs",
                             "Ranges: RAM 128MB-256GB, disk 2GB-4TB, CPU 1-64, invites 0-100000."), ephemeral=True)
            return

        async with async_session_maker() as session:
            exists = (await session.execute(
                select(Plan).where(Plan.name == name))).scalar_one_or_none()
            if exists:
                await ctx.send(embed=error_embed("Duplicate", f"A plan named **{name}** already exists."),
                               ephemeral=True)
                return
            plan = Plan(name=name, ram=ram_mb, cpu=cpu_cores, disk=disk_gb,
                        required_invites=required_invites, price=0, description="Custom plan")
            session.add(plan)
            await session.commit()

        await ctx.send(embed=success_embed(
            f"{E('plans')} Plan Created",
            f"**{name}** — {E('invite')} {required_invites} invites → "
            f"{E('ram')} {ram_mb}MB • {E('cpu')} {cpu_cores} vCore • {E('disk')} {disk_gb}GB SSD",
        ), ephemeral=True)

    @commands.hybrid_command(name="plan-remove", description="Delete a hosting plan (Admin)")
    @commands.guild_only()
    async def plan_remove(self, ctx: commands.Context, *, name: str):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return
        async with async_session_maker() as session:
            plan = (await session.execute(
                select(Plan).where(Plan.name == name))).scalar_one_or_none()
            if not plan:
                await ctx.send(embed=error_embed("Not Found", f"No plan named **{name}**."), ephemeral=True)
                return
            await session.delete(plan)
            await session.commit()
        await ctx.send(embed=success_embed(f"{E('trash')} Plan Removed", f"**{name}** has been deleted."),
                       ephemeral=True)

    @commands.hybrid_command(name="plan-list", description="List every plan incl. inactive (Admin)")
    @commands.guild_only()
    async def plan_list(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return
        async with async_session_maker() as session:
            plan_list = (await session.execute(
                select(Plan).order_by(Plan.sort_order, Plan.required_invites))).scalars().all()
        if not plan_list:
            await ctx.send(embed=info_embed("No Plans", "Run `/plan-seed` to load defaults."), ephemeral=True)
            return
        embed = info_embed(f"{E('list')} All Plans ({len(plan_list)})", None)
        for p in plan_list:
            embed.add_field(
                name=f"#{p.id} {p.name} {'🟢' if p.is_active else '🔴'}",
                value=f"{E('invite')} {p.required_invites} • {E('ram')} {p.ram}MB • "
                      f"{E('cpu')} {p.cpu} • {E('disk')} {p.disk}GB",
                inline=True,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="plan-seed", description="Load the default invite plans (Admin)")
    @commands.guild_only()
    async def plan_seed(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."), ephemeral=True)
            return

        defaults = [
            # (name, invites, ram MB, disk GB, cpu) — "3 invites = 3GB RAM / 12GB SSD / 1 core" tier included
            ("Starter", 1, 1024, 8, 1),
            ("Basic",   2, 2048, 10, 1),
            ("Pro",     3, 3072, 12, 1),
            ("Ultra",   5, 4096, 20, 2),
            ("Legend",  8, 8192, 40, 4),
        ]
        created = []
        async with async_session_maker() as session:
            for i, (name, inv, ram, disk, cpu) in enumerate(defaults):
                exists = (await session.execute(
                    select(Plan).where(Plan.name == name))).scalar_one_or_none()
                if exists:
                    continue
                session.add(Plan(
                    name=name, required_invites=inv, ram=ram, cpu=cpu, disk=disk,
                    price=0, sort_order=i,
                    description=f"{inv} invites → {ram // 1024}GB RAM / {disk}GB SSD / {cpu} core",
                ))
                created.append(name)
            await session.commit()

        if created:
            await ctx.send(embed=success_embed(
                f"{E('sparkles')} Default Plans Loaded",
                "Created: " + ", ".join(f"**{c}**" for c in created) +
                f"\n\n{E('search')} Run `/plans` to see the new tiers."),
                ephemeral=True)
        else:
            await ctx.send(embed=info_embed(
                f"{E('info')} Nothing To Seed",
                "All default plans already exist. Manage them with `/plan-list`."), ephemeral=True)


    # ── plan template editor (admin) ─────────────────────────────────

    @commands.hybrid_command(name="plan-edit",
                             description="Edit a plan template field-by-field (Admin)")
    @commands.guild_only()
    async def plan_edit(self, ctx: commands.Context, *, name: str):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."),
                           ephemeral=True)
            return
        async with async_session_maker() as session:
            plan = (await session.execute(
                select(Plan).where(Plan.name == name))).scalar_one_or_none()
        if not plan:
            await ctx.send(embed=error_embed("Not Found", f"No plan named **{name}**."),
                           ephemeral=True)
            return
        view = PlanEditView(plan.id, plan.name)
        embed = info_embed(
            f"{E('gear')} Editing Plan — {plan.name}",
            f"{E('invite')} Invites: `{plan.required_invites}` • "
            f"{E('ram')} RAM: `{plan.ram}MB` • {E('cpu')} Cores: `{plan.cpu}` • "
            f"{E('disk')} Disk: `{plan.disk}GB`\n"
            f"{E('gem')} Price: `{plan.price}` cents • Active: "
            f"`{'yes' if plan.is_active else 'no'}`\n\n"
            f"Pick a field below to edit — changes save instantly.")
        await ctx.send(embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(name="plan-toggle",
                             description="Enable/disable a plan (Admin)")
    @commands.guild_only()
    async def plan_toggle(self, ctx: commands.Context, *, name: str):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."),
                           ephemeral=True)
            return
        async with async_session_maker() as session:
            plan = (await session.execute(
                select(Plan).where(Plan.name == name))).scalar_one_or_none()
            if not plan:
                await ctx.send(embed=error_embed("Not Found", f"No plan named **{name}**."),
                               ephemeral=True)
                return
            plan.is_active = not plan.is_active
            state = plan.is_active
            await session.commit()
        await ctx.send(embed=success_embed(
            f"Plan {'Enabled' if state else 'Disabled'}",
            f"**{name}** is now {'visible in /plans' if state else 'hidden from /plans'}."),
            ephemeral=True)

    @commands.hybrid_command(name="plan-export",
                             description="Export all plans as a JSON template (Admin)")
    @commands.guild_only()
    async def plan_export(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."),
                           ephemeral=True)
            return
        import io, json as _json
        from datetime import datetime as _dt
        async with async_session_maker() as session:
            plans = (await session.execute(
                select(Plan).order_by(Plan.sort_order))).scalars().all()
        data = {
            "app": "arqon-plan-template", "v": 1,
            "exported": _dt.utcnow().isoformat(timespec="seconds"),
            "plans": [{
                "name": p.name, "description": p.description,
                "required_invites": p.required_invites, "ram": p.ram,
                "cpu": p.cpu, "disk": p.disk, "price": p.price,
                "is_active": p.is_active, "is_premium": p.is_premium,
                "sort_order": p.sort_order, "features": p.features or [],
            } for p in plans],
        }
        blob = _json.dumps(data, indent=2)
        await ctx.send(
            embed=success_embed(f"{E('plans')} Template Exported",
                                f"**{len(plans)}** plan(s) exported as JSON."),
            file=discord.File(io.BytesIO(blob.encode()),
                              filename="arqon-plan-template.json"),
            ephemeral=True)

    @commands.hybrid_command(name="plan-import",
                             description="Import a plan template JSON file (Admin)")
    @commands.guild_only()
    async def plan_import(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only."),
                           ephemeral=True)
            return
        att = next((a for a in ctx.message.attachments
                    if a.filename.endswith(".json")), None)
        if not att:
            await ctx.send(embed=info_embed(
                "Import", "Attach an `arqon-plan-template.json` file to this command."),
                ephemeral=True)
            return
        if att.size > 512_000:
            await ctx.send(embed=error_embed("File Too Large",
                             "Plan templates are limited to 500 KB."), ephemeral=True)
            return
        import json as _json
        try:
            data = _json.loads((await att.read()).decode("utf-8", "replace"))
            assert isinstance(data, dict) and data.get("app") == "arqon-plan-template"
            assert isinstance(data.get("plans", []), list)
        except Exception:
            await ctx.send(embed=error_embed(
                "Invalid File", "That doesn't look like an Arqon plan template."),
                ephemeral=True)
            return

        # ── strict validation: a hostile template can no longer inject
        #    negative invites/ram, wrong types, or thousands of plans ──────
        FIELD_LIMITS = {
            "required_invites": (0, 100000),
            "ram": (128, 262144),
            "cpu": (1, 64),
            "disk": (2, 4096),
            "price": (0, 100000000),
            "sort_order": (0, 100000),
        }

        def _clean(item: dict):
            """Return (clean_fields, error). Only whitelisted fields survive."""
            out = {}
            name = str(item.get("name") or "").strip()
            if not name or len(name) > 60 or "\n" in name:
                return None, "name must be 1-60 chars"
            out["name"] = name
            if "description" in item:
                desc = str(item.get("description") or "")[:500]
                out["description"] = desc
            for field, (lo, hi) in FIELD_LIMITS.items():
                if field not in item:
                    continue
                try:
                    val = int(item[field])
                except (TypeError, ValueError):
                    return None, f"{field} must be an integer"
                if not (lo <= val <= hi):
                    return None, f"{field} must be between {lo} and {hi}"
                out[field] = val
            for field in ("is_active", "is_premium"):
                if field in item:
                    if isinstance(item[field], bool):
                        out[field] = item[field]
                    elif item[field] in (0, 1):
                        out[field] = bool(item[field])
                    else:
                        return None, f"{field} must be a boolean"
            if "features" in item:
                feats = item.get("features")
                if not isinstance(feats, list) or len(feats) > 30:
                    return None, "features must be a list of at most 30 strings"
                cleaned = []
                for f in feats:
                    fs = str(f).strip()[:100]
                    if fs:
                        cleaned.append(fs)
                out["features"] = cleaned
            return out, None

        raw_items = data.get("plans", []) or []
        if len(raw_items) > 50:
            await ctx.send(embed=error_embed(
                "Too Many Plans", "A template may contain at most **50** plans."),
                ephemeral=True)
            return
        imported = updated = skipped = 0
        errors = []
        async with async_session_maker() as session:
            for item in raw_items:
                if not isinstance(item, dict):
                    skipped += 1
                    continue
                clean, err = _clean(item)
                if clean is None:
                    skipped += 1
                    if len(errors) < 5:
                        errors.append(f"`{str(item.get('name', '?'))[:40]}` — {err}")
                    continue
                plan = (await session.execute(
                    select(Plan).where(Plan.name == clean["name"]))).scalar_one_or_none()
                if plan:
                    for field, value in clean.items():
                        if field == "name":
                            continue
                        setattr(plan, field, value)
                    updated += 1
                else:
                    session.add(Plan(
                        name=clean["name"],
                        description=clean.get("description", "Imported plan"),
                        required_invites=clean.get("required_invites", 0),
                        ram=clean.get("ram", 1024),
                        cpu=clean.get("cpu", 1),
                        disk=clean.get("disk", 10),
                        price=clean.get("price", 0),
                        is_active=clean.get("is_active", True),
                        is_premium=clean.get("is_premium", False),
                        sort_order=clean.get("sort_order", 0),
                        features=clean.get("features", []),
                    ))
                    imported += 1
            await session.commit()
        desc = f"Created **{imported}** new plan(s), updated **{updated}**, skipped **{skipped}**."
        if errors:
            desc += "\nRejected: " + ", ".join(errors)
        await ctx.send(embed=(success_embed(
            f"{E('sparkles')} Template Imported", desc) if not skipped else warning_embed(
            f"{E('plans')} Template Imported (with rejections)", desc)),
            ephemeral=True)


class PlanEditView(discord.ui.View):
    """Field-by-field plan template editor with modal inputs."""

    FIELDS = [
        ("required_invites", "Required invites"),
        ("ram", "RAM (MB)"),
        ("cpu", "CPU cores"),
        ("disk", "Disk (GB)"),
        ("price", "Price (cents/month)"),
    ]

    def __init__(self, plan_id: int, plan_name: str):
        super().__init__(timeout=240)
        self.plan_id = plan_id
        self.plan_name = plan_name
        options = [discord.SelectOption(label=label, value=attr)
                   for attr, label in self.FIELDS]
        options.append(discord.SelectOption(label="Active toggle", value="is_active",
                                            description="Show/hide in /plans"))
        select = discord.ui.Select(placeholder="Pick a field to edit…",
                                   options=options)
        select.callback = self._pick
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from utils import is_admin as _is_admin
        if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Admin Only", "Only admins can edit plans."),
                ephemeral=True)
            return False
        return True

    async def _pick(self, interaction: discord.Interaction):
        field = interaction.data["values"][0]
        if field == "is_active":
            async with async_session_maker() as session:
                plan = (await session.execute(
                    select(Plan).where(Plan.id == self.plan_id))).scalar_one_or_none()
                if not plan:
                    return await interaction.response.send_message(
                        embed=error_embed("Gone", "This plan was deleted."),
                        ephemeral=True)
                plan.is_active = not plan.is_active
                state = plan.is_active
                await session.commit()
            await interaction.response.send_message(
                embed=success_embed(
                    "Saved",
                    f"**{self.plan_name}** is now "
                    f"{'active' if state else 'inactive'}."), ephemeral=True)
            return
        async with async_session_maker() as session:
            plan = (await session.execute(
                select(Plan).where(Plan.id == self.plan_id))).scalar_one_or_none()
            current = getattr(plan, field, 0) if plan else 0
        label = next(l for a, l in self.FIELDS if a == field)
        await interaction.response.send_modal(PlanFieldModal(
            self.plan_id, self.plan_name, field, label, current))


class PlanFieldModal(discord.ui.Modal):
    def __init__(self, plan_id: int, plan_name: str, field: str, label: str, current):
        self.plan_id = plan_id
        self.plan_name = plan_name
        self.field = field
        super().__init__(title=f"Edit {plan_name} — {label}"[:45], timeout=120)
        self.value_input = discord.ui.TextInput(
            label=label[:40], default=str(current), max_length=20, required=True)
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            value = int(str(self.value_input.value).strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=error_embed("Invalid Number",
                                  f"`{self.value_input.value}` is not a number."),
                ephemeral=True)
        minimums = {"ram": 128, "cpu": 1, "disk": 2, "required_invites": 0,
                    "price": 0}
        if value < minimums.get(self.field, 0):
            return await interaction.response.send_message(
                embed=error_embed("Out of Range",
                                  f"Minimum for this field is {minimums.get(self.field, 0)}."),
                ephemeral=True)
        async with async_session_maker() as session:
            plan = (await session.execute(
                select(Plan).where(Plan.id == self.plan_id))).scalar_one_or_none()
            if not plan:
                return await interaction.response.send_message(
                    embed=error_embed("Gone", "This plan was deleted."), ephemeral=True)
            setattr(plan, self.field, value)
            await session.commit()
        await interaction.response.send_message(
            embed=success_embed("Saved",
                                f"**{self.plan_name}** → {self.field} = `{value}`. "
                                f"Members see it instantly in `/plans`."),
            ephemeral=True)


async def setup(bot):
    await bot.add_cog(PlansCog(bot))
