import discord
from discord.ext import commands
from discord.ui import View, Select, Modal, TextInput, Button
from typing import Optional
from datetime import datetime, timezone

from utils import (
    minecraft_embed, success_embed, error_embed, info_embed,
    has_permission, PermissionLevel, get_logger,
    validate_vps_name, is_admin, is_staff,
)
from database import (
    async_session_maker, Node, NodeStatus, User, MinecraftServer,
    ServerStatus, get_or_create_user,
)
from config import settings, MC_EGG_CONFIGS
from sqlalchemy import select
from utils.ptero_api import get_pterodactyl_api, get_client_api, PterodactylAPIError

logger = get_logger("minecraft")


def find_minecraft_egg(software: str):
    """Return the egg config dict for a Minecraft software type."""
    return MC_EGG_CONFIGS.get((software or "").lower())


class MCCreateWizard(View):
    def __init__(self, author_id: int):
        super().__init__(timeout=300)
        self.author_id = author_id
        self.data = {}

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(embed=error_embed("Not Yours", "This wizard belongs to someone else."), ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="1️⃣ Select Node", custom_id="arqon:mc_node_select")
    async def node_select(self, interaction: discord.Interaction, select_menu: Select):
        self.data["node_id"] = int(select_menu.values[0])
        await self.show_software_select(interaction)

    async def show_software_select(self, interaction: discord.Interaction):
        options = [
            discord.SelectOption(label="Paper", value="paper", description="High performance PaperMC", emoji="📄"),
            discord.SelectOption(label="Purpur", value="purpur", description="Optimized Paper fork", emoji="🟣"),
            discord.SelectOption(label="Fabric", value="fabric", description="Lightweight modding platform", emoji="🧵"),
            discord.SelectOption(label="Forge", value="forge", description="Classic modding platform", emoji="🔨"),
            discord.SelectOption(label="NeoForge", value="neoforge", description="Modern Forge fork", emoji="⛏️"),
            discord.SelectOption(label="Spigot", value="spigot", description="Classic Spigot", emoji="🔌"),
            discord.SelectOption(label="Vanilla", value="vanilla", description="Official Mojang server", emoji="🟩"),
        ]
        select_menu = Select(placeholder="2️⃣ Select Software", options=options, custom_id="arqon:mc_software_select")

        async def callback(inter: discord.Interaction):
            self.data["software"] = select_menu.values[0]
            await self.show_version_select(inter)

        select_menu.callback = callback
        view = View()
        view.add_item(select_menu)
        await interaction.response.send_message(
            embed=info_embed("🧩 Select Software", "Choose the Minecraft server software."),
            view=view, ephemeral=True)

    async def show_version_select(self, interaction: discord.Interaction):
        versions = {
            "paper": ["1.21.4", "1.21.3", "1.21.1", "1.20.6", "1.20.4", "1.19.4", "1.18.2", "1.16.5"],
            "purpur": ["1.21.4", "1.21.3", "1.21.1", "1.20.6", "1.20.4", "1.19.4", "1.18.2"],
            "fabric": ["1.21.4", "1.21.3", "1.21.1", "1.20.6", "1.20.4", "1.19.4", "1.18.2"],
            "forge": ["1.21.1", "1.20.6", "1.20.4", "1.19.4", "1.18.2", "1.16.5"],
            "neoforge": ["1.21.1", "1.20.6", "1.20.4", "1.19.4"],
            "spigot": ["1.21.4", "1.20.6", "1.19.4", "1.18.2", "1.16.5", "1.12.2"],
            "vanilla": ["1.21.4", "1.21.3", "1.21.1", "1.20.6", "1.20.4", "1.19.4", "1.18.2", "1.16.5"],
        }
        opts = [discord.SelectOption(label=v, value=v)
                for v in versions.get(self.data.get("software"), ["1.21.4"])]
        select_menu = Select(placeholder="3️⃣ Select Version", options=opts, custom_id="arqon:mc_version_select")

        async def callback(inter: discord.Interaction):
            self.data["version"] = select_menu.values[0]
            await inter.response.send_modal(MCNameModal(self))

        select_menu.callback = callback
        view = View()
        view.add_item(select_menu)
        await interaction.response.send_message(
            embed=info_embed("🔢 Select Version", "Choose the Minecraft version."),
            view=view, ephemeral=True)


class MCNameModal(Modal, title="Server Details"):
    def __init__(self, wizard: MCCreateWizard):
        super().__init__(custom_id="arqon:mc_name_modal")
        self.wizard = wizard
        self.name_in = TextInput(label="Server Name", placeholder="My MC Server", required=True, max_length=64)
        self.ram_in = TextInput(label="RAM MB (default 2048)", placeholder="2048", required=False, max_length=6)
        self.add_item(self.name_in)
        self.add_item(self.ram_in)

    async def on_submit(self, interaction: discord.Interaction):
        self.wizard.data["name"] = self.name_in.value.strip()
        try:
            self.wizard.data["ram"] = int(self.ram_in.value or "2048")
        except ValueError:
            self.wizard.data["ram"] = 2048

        d = self.wizard.data
        embed = minecraft_embed("✅ Confirm Server Creation", "Review your configuration:")
        embed.add_field(name="Name", value=d["name"], inline=True)
        embed.add_field(name="Software", value=d["software"].title(), inline=True)
        embed.add_field(name="Version", value=d["version"], inline=True)
        embed.add_field(name="RAM", value=f"{d['ram']} MB", inline=True)

        async with async_session_maker() as session:
            node = await session.get(Node, d["node_id"])
            node_name = node.name if node else "Unknown"

        embed.add_field(name="Node", value=node_name, inline=True)
        await interaction.response.send_message(embed=embed, view=ConfirmMCView(self.wizard), ephemeral=True)


class ConfirmMCView(View):
    def __init__(self, wizard: MCCreateWizard):
        super().__init__(timeout=120)
        self.wizard = wizard

    @discord.ui.button(label="Deploy", style=discord.ButtonStyle.success, emoji="🚀")
    async def deploy(self, interaction: discord.Interaction, button: Button):
        await self.create_server(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(embed=error_embed("Cancelled", "Server creation cancelled."), view=None)

    async def create_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        d = self.wizard.data

        api = get_pterodactyl_api()
        if not api.configured:
            await interaction.followup.send(
                embed=error_embed("Not Configured", "Pterodactyl URL/API key missing in .env"), ephemeral=True)
            return

        egg_config = find_minecraft_egg(d["software"])
        if not egg_config:
            await interaction.followup.send(embed=error_embed("Error", "Invalid software selection."), ephemeral=True)
            return

        ram = d.get("ram", 2048)
        disk_mb = 10240
        cpu_pct = 200  # 2 cores

        try:
            # resolve egg id from Pterodactyl (by name search)
            eggs = await api.list_eggs()
            egg_id = None
            egg_startup = egg_config["startup"]
            for e in eggs:
                attrs = e.get("attributes", {})
                if attrs.get("name", "").lower() == egg_config["egg"]:
                    egg_id = attrs.get("id")
                    egg_startup = attrs.get("startup", egg_startup)
                    break
            if egg_id is None:
                egg_id = settings.PTERODACTYL_DEFAULT_EGG

            # pick the pterodactyl node backing our Arqon node
            async with async_session_maker() as session:
                arqon_node = await session.get(Node, d["node_id"])
            ptero_nodes = await api.list_nodes()
            ptero_node_id = None
            for pn in ptero_nodes:
                attrs = pn.get("attributes", {})
                if arqon_node and str(attrs.get("name", "")).lower() == arqon_node.name.lower():
                    ptero_node_id = attrs.get("id")
                if ptero_node_id is None:
                    ptero_node_id = attrs.get("id")
            if ptero_node_id is None:
                ptero_node_id = settings.PTERODACTYL_DEFAULT_NODE

            free_port = await api.find_free_port(ptero_node_id, settings.MC_ALLOCATION_START, settings.MC_ALLOCATION_END)
            if free_port is None:
                await interaction.followup.send(embed=error_embed("No Capacity", "No free ports on that node."), ephemeral=True)
                return

            server_data = {
                "name": d["name"],
                "user": 1,
                "node": ptero_node_id,
                "nest": 1,
                "egg": egg_id,
                "docker_image": egg_config["docker_image"],
                "startup": egg_startup,
                "environment": {"VERSION": d["version"], "SERVER_JARFILE": "server.jar"},
                "limits": {"memory": ram, "swap": 0, "disk": disk_mb, "io": 500, "cpu": cpu_pct},
                "feature_limits": {"databases": 2, "allocations": 1, "backups": 5},
                "deploy": {"default_allocation": free_port, "start_on_completion": True},
            }

            created = await api.create_server(server_data)
            ptero_server_id = created.get("id")
            ptero_uuid = created.get("uuid", "")

            # record in database (owner resolved to DB user)
            async with async_session_maker() as session:
                owner = await get_or_create_user(session, interaction.user.id, interaction.user.name)
                mc_server = MinecraftServer(
                    name=d["name"],
                    identifier=ptero_uuid[:8] or d["name"][:8],
                    owner_id=owner.id,
                    node_id=d["node_id"],
                    software=d["software"],
                    version=d["version"],
                    ram=ram,
                    cpu=2,
                    disk=10,
                    port=free_port,
                    egg_id=egg_id,
                    docker_image=egg_config["docker_image"],
                    startup_command=egg_startup,
                    status=ServerStatus.RUNNING,
                    pterodactyl_server_id=ptero_server_id,
                    pterodactyl_node_id=ptero_node_id,
                )
                session.add(mc_server)
                await session.commit()

            embed = success_embed("🚀 Server Deployed", f"Minecraft server **{d['name']}** is starting!")
            embed.add_field(name="Software", value=f"{d['software'].title()} {d['version']}", inline=True)
            embed.add_field(name="RAM", value=f"{ram} MB", inline=True)
            embed.add_field(name="Port", value=str(free_port), inline=True)
            embed.add_field(name="Panel ID", value=str(ptero_server_id), inline=True)
            embed.set_footer(text=f"Join at your-node-ip:{free_port}")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except PterodactylAPIError as exc:
            logger.error(f"MC deploy failed: {exc}")
            await interaction.followup.send(embed=error_embed("Deploy Failed", str(exc)), ephemeral=True)
        except Exception as exc:
            logger.error(f"MC deploy error: {exc}", exc_info=exc)
            await interaction.followup.send(embed=error_embed("Error", str(exc)), ephemeral=True)


class MinecraftCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="create-mc", description="Create a Minecraft server (Admin)")
    @commands.guild_only()
    async def create_mc(self, ctx: commands.Context):
        if not has_permission(ctx.author, PermissionLevel.ADMIN):
            await ctx.send(embed=error_embed("Permission Denied", "Admin only"), ephemeral=True)
            return

        if not settings.PTERODACTYL_URL or not (settings.PTERODACTYL_API_KEY or settings.PTERODACTYL_APP_API_KEY):
            await ctx.send(embed=error_embed(
                "Not Configured",
                "Pterodactyl is not configured. Set `PTERODACTYL_URL` and `PTERODACTYL_API_KEY` in `.env`."),
                ephemeral=True)
            return

        async with async_session_maker() as session:
            nodes = (await session.execute(
                select(Node).where(Node.is_active == True)  # noqa: E712
            )).scalars().all()
            online_nodes = [n for n in nodes if n.status == NodeStatus.ONLINE]

        if not online_nodes:
            await ctx.send(embed=error_embed("No Nodes", "No online nodes available."), ephemeral=True)
            return

        options = [discord.SelectOption(label=f"{n.name}", value=str(n.id),
                                        description=f"{n.location or 'Unknown location'}")
                   for n in online_nodes]

        wizard = MCCreateWizard(ctx.author.id)
        node_select = wizard.node_select
        node_select.options = options

        embed = minecraft_embed(
            "⛏️ Minecraft Server Creator",
            "Deploy a Minecraft server via Pterodactyl in 4 steps:\n"
            "1️⃣ Node → 2️⃣ Software → 3️⃣ Version → 4️⃣ Name",
        )
        embed.add_field(name="Supported Software",
                        value="Paper • Purpur • Fabric • Forge • NeoForge • Spigot • Vanilla", inline=False)
        embed.add_field(name="Auto-configuration",
                        value="Port allocation, egg config and Docker image handled automatically.", inline=False)
        await ctx.send(embed=embed, view=wizard, ephemeral=True)

    @commands.hybrid_command(name="mc-list", description="List your Minecraft servers")
    @commands.guild_only()
    async def mc_list(self, ctx: commands.Context):
        async with async_session_maker() as session:
            owner = await get_or_create_user(session, ctx.author.id, ctx.author.name)
            servers = (await session.execute(
                select(MinecraftServer)
                .where(MinecraftServer.owner_id == owner.id,
                       MinecraftServer.deleted_at.is_(None))
                .order_by(MinecraftServer.created_at.desc())
            )).scalars().all()

        if not servers:
            await ctx.send(embed=info_embed("No Servers", "You don't have any Minecraft servers."), ephemeral=True)
            return

        embed = minecraft_embed(f"⛏️ Your Minecraft Servers ({len(servers)})", None)
        for s in servers:
            emoji = {"running": "🟢", "stopped": "🔴", "creating": "⏳", "error": "⚠️",
                     "suspended": "⛔"}.get(s.status_value, "⚪")
            embed.add_field(
                name=f"{emoji} {s.name}",
                value=(f"{s.software.title()} {s.version} • {s.ram}MB RAM\n"
                       f"Port: {s.port} • Node #{s.node_id} • ID: `{s.identifier}`"),
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="mc-power", description="Power action on a Minecraft server (staff)")
    @commands.guild_only()
    async def mc_power(self, ctx: commands.Context, server_identifier: str, action: str = "restart"):
        if not has_permission(ctx.author, PermissionLevel.STAFF):
            await ctx.send(embed=error_embed("Permission Denied", "Staff only"), ephemeral=True)
            return

        action = action.lower()
        if action not in ("start", "stop", "restart", "kill"):
            await ctx.send(embed=error_embed("Invalid Action", "Use: start / stop / restart / kill"), ephemeral=True)
            return

        client = get_client_api()
        if not client.configured:
            await ctx.send(embed=error_embed("Not Configured", "Client API key missing in .env"), ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        try:
            await client.power(server_identifier, action)
            await ctx.send(embed=success_embed("Power Action Sent", f"`{action}` sent to `{server_identifier}`."), ephemeral=True)
        except PterodactylAPIError as exc:
            await ctx.send(embed=error_embed("Failed", str(exc)), ephemeral=True)


async def setup(bot):
    await bot.add_cog(MinecraftCog(bot))
