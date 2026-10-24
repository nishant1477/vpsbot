import discord
from discord.ext import commands, tasks
import asyncio
import sys
from datetime import datetime

import config
from config import settings
from database import init_db, close_db, async_session_maker, GuildConfig, Node, NodeStatus, get_or_create_user
from utils import setup_logging, get_logger, bot_logger
from utils.embeds import success_embed, error_embed, info_embed
from utils.emoji import E
from utils.node_client import check_all_nodes

setup_logging()
logger = get_logger("bot")


class ArqonBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.presences = True
        intents.message_content = True
        intents.messages = True
        intents.webhooks = True
        intents.moderation = True
        intents.voice_states = True
        intents.reactions = True
        intents.invites = True
        intents.integrations = True

        super().__init__(
            command_prefix=settings.BOT_PREFIX,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            strip_after_prefix=True,
            activity=discord.Activity(
                type=getattr(discord.ActivityType, settings.BOT_ACTIVITY_TYPE.lower(), discord.ActivityType.playing),
                name=settings.BOT_STATUS,
            ),
            status=discord.Status.online,
            allowed_mentions=discord.AllowedMentions(
                everyone=False, users=True, roles=True, replied_user=False,
            ),
        )

        self.initial_extensions = [
            "cogs.tickets",
            "cogs.guardian",
            "cogs.security_suite",  # Security Suite v3 — 25 protections
            "cogs.vps",
            "cogs.plans",          # invite-earned plan store (/plans)
            "cogs.invites",        # invite tracking engine
            "cogs.minecraft",
            "cogs.nodes",
            "cogs.monitoring",
            "cogs.backups",
            "cogs.admin",
            "cogs.users",
            "cogs.ports",
            "cogs.firewall",
            "cogs.dashboard",
            "cogs.logs",
            "cogs.utils",
            "cogs.pterodactyl",
            "cogs.billing",
            "cogs.emojis",         # custom emoji management
            "cogs.settings_ui",    # interactive /settings console
            "cogs.ai_assistant",
            "cogs.help",           # loaded LAST so /help sees every command
        ]

        self.guild_configs_cache = {}
        self.boot_time = datetime.utcnow()
        # banned-user cache for the global gate (refreshed with status_task)
        self._banned_cache: set = set()

        # register the global banned-user gate for every command invocation
        self.before_invoke(self._ban_gate)

    async def _ban_gate(self, ctx: commands.Context):
        """Block banned accounts from using ANY command (#9 in the audit)."""
        if not ctx.author or ctx.author.id in self._banned_cache:
            if ctx.author and ctx.author.id in self._banned_cache:
                raise commands.CheckFailure("Account banned")
            return

    async def refresh_banned_cache(self):
        try:
            from sqlalchemy import select
            from database import User
            async with async_session_maker() as session:
                rows = (await session.execute(
                    select(User.discord_id).where(User.is_banned == True))).all()  # noqa: E712
            self._banned_cache = {int(r[0]) for r in rows}
        except Exception as exc:
            logger.debug(f"banned cache refresh failed: {exc}")

    async def setup_hook(self):
        logger.info("Setting up bot...")

        await init_db()
        logger.info("Database initialized")

        await self.refresh_banned_cache()
        await self.load_bot_admin_cache()
        await self.load_extensions()

        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} slash commands")
        except Exception as exc:
            logger.error(f"Slash sync failed: {exc}")

        self.status_task.start()
        self.node_heartbeat.start()
        self.backup_task.start()
        self.monitoring_task.start()

        logger.info("Bot setup complete")

    async def load_extensions(self):
        for ext in self.initial_extensions:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as exc:
                logger.error(f"Failed to load extension {ext}: {exc}", exc_info=exc)

    async def load_bot_admin_cache(self):
        """Load /addadmin-granted bot admins into the sync permission cache."""
        from sqlalchemy import select
        from database import BotAdmin
        from utils.permissions import set_bot_admin_cache
        try:
            async with async_session_maker() as session:
                rows = (await session.execute(select(BotAdmin))).scalars().all()
            set_bot_admin_cache([(r.discord_id, r.level) for r in rows])
            logger.info(f"Loaded {len(rows)} bot admins into permission cache")
        except Exception as exc:
            logger.error(f"Bot admin cache load failed: {exc}")

    # ── lifecycle events ─────────────────────────────────────────────

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        for guild in self.guilds:
            await self.ensure_guild_config(guild.id)

    async def on_guild_join(self, guild: discord.Guild):
        await self.ensure_guild_config(guild.id)
        logger.info(f"Joined guild: {guild.name} (ID: {guild.id})")

    async def on_guild_remove(self, guild: discord.Guild):
        self.guild_configs_cache.pop(guild.id, None)
        logger.info(f"Left guild: {guild.name} (ID: {guild.id})")

    async def ensure_guild_config(self, guild_id: int):
        async with async_session_maker() as session:
            await self.get_or_create_guild_config(session, guild_id)
            await session.commit()

    # ── background tasks ─────────────────────────────────────────────

    async def _rotating_activities(self):
        """Fresh, alive-looking presence rotation for the bot status."""
        name = settings.BOT_NAME
        return [
            discord.Game(name=f"/help • {name}"),
            discord.Activity(type=discord.ActivityType.watching, name=f"{len(self.guilds)} servers " + E("sparkles")),
            discord.Activity(type=discord.ActivityType.listening, name="/plans — free VPS with invites"),
            discord.Activity(type=discord.ActivityType.watching, name="the node cluster " + E("node")),
            discord.Activity(type=discord.ActivityType.playing, name="/ai — ask me anything"),
        ]

    @tasks.loop(minutes=2)
    async def status_task(self):
        """Rotate presence every 2 minutes so the bot feels alive."""
        try:
            acts = await self._rotating_activities()
            idx = getattr(self, "_activity_idx", 0) % len(acts)
            await self.change_presence(activity=acts[idx], status=discord.Status.online)
            self._activity_idx = idx + 1
        except Exception as exc:
            logger.error(f"Status task error: {exc}")
        # keep the banned-user gate fresh (every ~2 min is plenty)
        await self.refresh_banned_cache()

    @tasks.loop(seconds=30)
    async def node_heartbeat(self):
        """Real multi-node heartbeat: ping every agent, update status/latency/stats."""
        from sqlalchemy import select, update

        try:
            async with async_session_maker() as session:
                result = await session.execute(select(Node).where(Node.is_active == True))  # noqa: E712
                nodes = result.scalars().all()
                if not nodes:
                    return

                health_map = await check_all_nodes(nodes)

                for node in nodes:
                    health = health_map.get(node.id)
                    now = datetime.utcnow()
                    # Recovery detection must use the PERSISTED status — the ORM
                    # objects are re-queried each loop, so any transient marker
                    # set here would be lost.
                    was_online = node.status == NodeStatus.ONLINE

                    if health and health["online"]:
                        stats = (health.get("result") or {})
                        s = stats.get("stats") or {}

                        node.status = NodeStatus.ONLINE
                        node.last_heartbeat = now
                        node.latency = int(health.get("latency", 0))

                        # RAM arrives in bytes -> store MB; disk in bytes -> GB.
                        ram = s.get("ram") or {}
                        disk = s.get("disk") or {}
                        node.total_ram = int(ram.get("total", 0) // (1024 * 1024)) or node.total_ram
                        node.used_ram = int(ram.get("used", 0) // (1024 * 1024))
                        node.total_disk = int(disk.get("total", 0) // (1024 ** 3)) or node.total_disk
                        node.used_disk = int(disk.get("used", 0) // (1024 ** 3))

                        # cpu.percent is 0-100 usage, cpu.cores is a core count.
                        # used_cpu must stay in cores (matching cpu_percent property:
                        # used/total*100), not in percent — otherwise /node shows >1000%.
                        cores = int(s.get("cpu", {}).get("cores") or node.total_cpu or 0)
                        percent = float(s.get("cpu", {}).get("percent") or 0)
                        if cores:
                            node.total_cpu = cores
                            node.used_cpu = int(round(percent / 100.0 * cores))

                        node.vps_count = int(s.get("vps_count", node.vps_count or 0))
                        node.agent_version = s.get("version") or stats.get("version") or node.agent_version
                        node.network_info = s.get("network") or node.network_info

                        if not was_online:
                            await self.notify_node_status(node, "recovered")
                    else:
                        if was_online or not node.last_heartbeat:
                            await self.notify_node_status(node, "down")
                        node.status = NodeStatus.OFFLINE
                        node.last_heartbeat = now

                await session.commit()
        except Exception as exc:
            logger.error(f"Node heartbeat error: {exc}")

    async def notify_node_status(self, node: Node, event: str):
        """Alert the first guild's monitoring channel when a node goes down/up."""
        try:
            channel_id = settings.MONITORING_ALERT_CHANNEL_ID
            if not channel_id:
                return
            channel = self.get_channel(int(channel_id))
            if not channel:
                return
            if event == "down":
                embed = error_embed(
                    "🔴 Node Offline",
                    f"Node **{node.name}** (`{node.host}:{node.port}`) is not responding.\n"
                    f"VPS instances on this node may be unreachable.",
                )
            else:
                embed = success_embed(
                    "🟢 Node Recovered",
                    f"Node **{node.name}** is back online.\n"
                    f"Latency: **{node.latency}ms** • VPS: **{node.vps_count}**",
                )
            await channel.send(embed=embed)
        except Exception as exc:
            logger.debug(f"notify_node_status failed: {exc}")

    @tasks.loop(hours=1)
    async def backup_task(self):
        if not settings.BACKUP_ENABLED:
            return
        try:
            from cogs.backups import run_scheduled_backups
            await run_scheduled_backups(self)
        except Exception as exc:
            logger.error(f"Backup task error: {exc}")

    @tasks.loop(minutes=1)
    async def monitoring_task(self):
        try:
            from cogs.monitoring import check_node_alerts
            await check_node_alerts(self)
        except Exception as exc:
            logger.error(f"Monitoring task error: {exc}")

    # ── helpers for cogs ─────────────────────────────────────────────

    async def get_or_create_guild_config(self, session, guild_id: int) -> GuildConfig:
        from database import get_or_create_guild_config
        cfg = await get_or_create_guild_config(session, guild_id)
        self.guild_configs_cache[guild_id] = cfg
        return cfg

    def get_guild_config(self, guild_id: int):
        return self.guild_configs_cache.get(guild_id)

    async def get_or_create_db_user(self, interaction_or_ctx) -> "object":
        """Resolve a Discord member to a DB User (created on first sight)."""
        user = getattr(interaction_or_ctx, "user", None) or getattr(interaction_or_ctx, "author", None)
        async with async_session_maker() as session:
            db_user = await get_or_create_user(
                session,
                discord_id=user.id,
                username=getattr(user, "name", "unknown"),
                discriminator=getattr(user, "discriminator", None),
                avatar=getattr(user.display_avatar, "url", None) if hasattr(user, "display_avatar") else None,
            )
            await session.commit()
            return db_user

    # ── error handling ───────────────────────────────────────────────

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(embed=error_embed(
                "Missing Argument",
                f"Missing required argument: `{error.param.name}`"
            ))
            return

        if isinstance(error, commands.BadArgument):
            await ctx.send(embed=error_embed("Invalid Argument", str(error)))
            return

        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(embed=error_embed(
                "Command on Cooldown",
                f"Try again in {error.retry_after:.1f} seconds."
            ))
            return

        if isinstance(error, commands.CheckFailure):
            await ctx.send(embed=error_embed(
                "Permission Denied",
                "You don't have permission to use this command."
            ))
            return

        logger.error(f"Command error in {ctx.command}: {error}", exc_info=error)
        await ctx.send(embed=error_embed(
            "Command Error",
            "An error occurred while executing this command. Please try again later."
        ))

    async def on_app_command_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"App command error: {error}", exc_info=error)
        embed = error_embed("Error", "An unexpected error occurred. Please try again later.")
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception:
            pass

    async def on_error(self, event: str, *args, **kwargs):
        logger.error(f"Error in event {event}", exc_info=True)

    async def close(self):
        logger.info("Shutting down bot...")
        for task in (self.status_task, self.node_heartbeat, self.backup_task, self.monitoring_task):
            task.cancel()
        await close_db()
        await super().close()
        logger.info("Bot shutdown complete")


bot = ArqonBot()


def _warn_default_secrets():
    """Loud startup warnings when deployed with publicly-known default secrets."""
    import sys as _sys
    problems = []
    enc = (settings.ENCRYPTION_KEY or "").strip()
    if not enc or enc in ("arqon-default-encryption-key-change-me", "arqon-insecure-default-key"):
        problems.append("ENCRYPTION_KEY is unset/default — root passwords are NOT safely "
                        "encrypted. Set a strong random value in .env (e.g. `python -c "
                        "'import secrets; print(secrets.token_urlsafe(32))'`).")
    jwt = (settings.JWT_SECRET or "").strip()
    if not jwt or jwt == "arqon-jwt-secret-change-me":
        problems.append("JWT_SECRET is unset/default — dashboard/node tokens can be "
                        "forged by anyone. Set a strong random value in .env.")
    for p in problems:
        logger.critical(f"SECURITY: {p}")
    if problems:
        print("\n" + "=" * 70, file=_sys.stderr)
        for p in problems:
            print(f"  !! SECURITY WARNING: {p}", file=_sys.stderr)
        print("=" * 70 + "\n", file=_sys.stderr)
    return not problems


def run_bot():
    try:
        _warn_default_secrets()
        bot.run(settings.DISCORD_TOKEN, log_handler=None)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as exc:
        logger.critical(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run_bot()
