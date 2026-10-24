"""Arqon Bot — interactive bot settings editor (/settings).

A fully button-driven settings console for admins:

    /settings  →  home + section dropdown
               →  🛡 Security toggles      (one-click, saved instantly)
               →  🎚 Thresholds            (pick a value → modal editor)
               →  📋 Logs & Channels       (channel IDs via modal)
               →  🖥 VPS & Ticket defaults (numeric defaults via modal)

Everything writes straight into the per-guild GuildConfig row — no restart,
no .env editing, no CLI.
"""
import discord
from discord.ext import commands
from typing import Optional

from utils import success_embed, error_embed, info_embed, is_admin, get_logger
from utils.embeds import base_embed
from utils.emoji import E
from config import settings
from database import async_session_maker, GuildConfig
from sqlalchemy import select

logger = get_logger("settings_ui")


TOGGLES = [
    ("guardian_enabled", "Guardian"), ("anti_raid_enabled", "Anti-Raid"),
    ("anti_spam_enabled", "Anti-Spam"), ("anti_bot_enabled", "Anti-Bot"),
    ("anti_webhook_enabled", "Anti-Webhook"), ("anti_nuke_enabled", "Anti-Nuke"),
    ("anti_mention_enabled", "Anti-Mention"), ("anti_invite_enabled", "Anti-Invite"),
    ("sec_admin_abuse", "Abuse Detector"), ("sec_escalation_block", "Escalation Block"),
    ("sec_role_guard", "Role Guard"), ("sec_anti_alt", "Alt Detect"),
    ("sec_anti_impersonation", "Impersonation"), ("sec_anti_scam", "Scam Intel"),
    ("sec_scam_autodelete", "Scam Delete"), ("sec_auto_containment", "Containment"),
    ("sec_raid_lockdown", "Auto-Lockdown"), ("sec_perm_drift", "Drift Watch"),
    ("sec_honeypot", "Honeypots"), ("sec_two_person", "2-Person Rule"),
    ("sec_temp_priv", "Temp Privs"), ("sec_threat_share", "Threat Share"),
    ("sec_safe_mode", "Safe Mode"),
]

THRESHOLDS = [
    ("anti_spam_threshold", "Anti-spam messages"),
    ("anti_spam_window", "Anti-spam window (s)"),
    ("anti_raid_threshold", "Raid join threshold"),
    ("anti_raid_window", "Raid window (s)"),
    ("anti_mention_limit", "Mention limit"),
    ("anti_nuke_threshold", "Nuke actions (Guardian)"),
    ("anti_min_account_days", "Min account age (days, 0=off)"),
    ("sec_nuke_threshold", "Nuke actions (Suite)"),
    ("sec_abuse_threshold", "Abuse actions per window"),
    ("sec_bot_raid_threshold", "Bots before raid alert"),
    ("sec_alt_min_risk", "Alt risk score (0-100)"),
    ("sec_imp_similarity", "Name similarity %"),
    ("sec_lockdown_minutes", "Lockdown duration (min)"),
    ("warn_timeout_at", "Warns → timeout"),
    ("warn_kick_at", "Warns → kick"),
    ("warn_ban_at", "Warns → ban"),
]

LOG_KEYS = [
    ("guardian_log_channel_id", "Security log channel ID"),
    ("ticket_log_channel_id", "Ticket log channel ID"),
    ("ticket_category_id", "Ticket category ID"),
    ("ticket_transcript_channel_id", "Transcript channel ID"),
]

VPS_KEYS = [
    ("vps_default_ram", "Default RAM (MB)"),
    ("vps_default_cpu", "Default CPU cores"),
    ("vps_default_disk", "Default disk (GB)"),
    ("max_tickets_per_user", "Max tickets per user"),
    ("cooldown_create_vps", "VPS create cooldown (s)"),
]


async def load_cfg(guild_id: int) -> GuildConfig:
    async with async_session_maker() as session:
        row = (await session.execute(
            select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one_or_none()
        if row is None:
            row = GuildConfig(guild_id=guild_id)
            session.add(row)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                row = (await session.execute(
                    select(GuildConfig).where(GuildConfig.guild_id == guild_id))).scalar_one()
        return row


async def save_cfg(guild_id: int, attr: str, value) -> bool:
    if attr not in _WRITABLE_ATTRS:
        logger.error(f"save_cfg rejected non-whitelisted attribute '{attr}'")
        return False
    try:
        row = await load_cfg(guild_id)
        async with async_session_maker() as session:
            merged = await session.merge(row)
            setattr(merged, attr, value)
            await session.commit()
        return True
    except Exception as exc:
        logger.error(f"save_cfg({attr}) failed: {exc}")
        return False


# Every GuildConfig column the settings console is allowed to write, with the
# minimum sane value for numeric attrs (a threshold of -5 would punish
# everyone instantly; 0 would disable a protection entirely).
_WRITABLE_ATTRS = {attr for attr, _ in TOGGLES} \
    | {attr for attr, _ in LOG_KEYS} \
    | {attr for attr, _ in VPS_KEYS} \
    | {attr for attr, _ in THRESHOLDS} | {"anti_nuke_action"}

_MIN_BOUNDS = {
    "anti_spam_threshold": 2, "anti_spam_window": 3,
    "anti_raid_threshold": 2, "anti_raid_window": 5,
    "anti_mention_limit": 2, "anti_nuke_threshold": 2,
    "anti_min_account_days": 0,
    "sec_nuke_threshold": 2, "sec_abuse_threshold": 2,
    "sec_bot_raid_threshold": 1, "sec_alt_min_risk": 10,
    "sec_imp_similarity": 50, "sec_lockdown_minutes": 1,
    "warn_timeout_at": 1, "warn_kick_at": 2, "warn_ban_at": 3,
    "max_tickets_per_user": 1, "cooldown_create_vps": 0,
    "cooldown_create_mc": 0, "cooldown_ticket": 0,
    "vps_default_ram": 128, "vps_default_cpu": 1, "vps_default_disk": 2,
    "guardian_log_channel_id": 0, "ticket_log_channel_id": 0,
    "ticket_category_id": 0, "ticket_transcript_channel_id": 0,
}


def _onoff(value) -> str:
    return "🟢 ON" if value else "🔴 OFF"


def _home_embed(guild: discord.Guild) -> discord.Embed:
    embed = base_embed(
        f"{E('gear')} Bot Settings — {guild.name}",
        f"Configure **everything** about how the bot behaves in this server — "
        f"protections, thresholds, log channels and defaults — without touching "
        f"`.env` or the terminal.\n\n"
        f"{E('search')} **Pick a section below.** Changes save instantly.",
        0x9B59B6,
        footer=f"{settings.BOT_FOOTER} • admin only",
    )
    embed.add_field(
        name="Sections",
        value=(
            f"{E('shield')} **Security** — 23 protection toggles\n"
            f"{E('chart')} **Thresholds** — 16 tunable values\n"
            f"{E('channel')} **Logs & Channels** — where alerts land\n"
            f"{E('plans')} **VPS & Tickets** — defaults & cooldowns"
        ),
        inline=False,
    )
    return embed


def _toggles_embed(guild: discord.Guild, cfg: GuildConfig) -> discord.Embed:
    chunks = [TOGGLES[i:i + 3] for i in range(0, len(TOGGLES), 3)]
    lines = "\n".join(
        "   ".join(f"{_onoff(getattr(cfg, attr, True))} {name}"
                   for attr, name in row)
        for row in chunks)
    return base_embed(
        f"{E('shield')} Security Toggles",
        f"{lines}\n\n{E('info')} Click a button below to flip a protection. "
        f"**Safe Mode** keeps monitoring but never punishes.",
        0x8E44AD,
        footer=f"{settings.BOT_FOOTER} • changes apply instantly",
    )


def _numbers_embed(title: str, keys, cfg: GuildConfig, color=0x3498DB) -> discord.Embed:
    lines = "\n".join(
        f"{E('bolt')} **{label}** — `{getattr(cfg, attr, '?')}`"
        for attr, label in keys)
    return base_embed(
        title, f"{lines}\n\n{E('info')} Pick a value below to edit it.",
        color,
        footer=f"{settings.BOT_FOOTER} • changes apply instantly",
    )


class NumberModal(discord.ui.Modal):
    """Generic single-value editor (int or enum-string)."""

    def __init__(self, cog_view, guild_id: int, attr: str, label: str,
                 current, allow_zero: bool = True):
        self.cog_view = cog_view
        self.guild_id = guild_id
        self.attr = attr
        self.label = label
        self.allow_zero = allow_zero
        super().__init__(title=f"Edit: {label}"[:45], timeout=120)
        self.value_input = discord.ui.TextInput(
            label=label[:40],
            default=str(current if current is not None else 0),
            max_length=60,
            required=True,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.value_input.value).strip()
        if self.attr == "anti_nuke_action":
            val = raw.lower()
            if val not in ("strip", "kick", "ban"):
                return await interaction.response.send_message(
                    embed=error_embed("Invalid Choice",
                                      "Allowed: `strip`, `kick`, `ban`."),
                    ephemeral=True)
        else:
            try:
                val = int(raw)
            except ValueError:
                return await interaction.response.send_message(
                    embed=error_embed("Invalid Number", f"`{raw}` is not a number."),
                    ephemeral=True)
            minimum = _MIN_BOUNDS.get(self.attr, 0)
            if val < minimum:
                return await interaction.response.send_message(
                    embed=error_embed("Out of Range",
                                      f"Minimum for **{self.label}** is {minimum}."),
                    ephemeral=True)
            if val > 4_000_000_000:
                return await interaction.response.send_message(
                    embed=error_embed("Out of Range", "Value is absurdly large."),
                    ephemeral=True)
        if not await save_cfg(self.guild_id, self.attr, val):
            return await interaction.response.send_message(
                embed=error_embed("Save Failed",
                                  "This setting could not be written."),
                ephemeral=True)
        await interaction.response.send_message(
            embed=success_embed("Saved",
                                f"**{self.label}** → `{val}` — applied instantly."),
            ephemeral=True)


class SettingsHomeView(discord.ui.View):
    def __init__(self, bot, requester: discord.Member):
        super().__init__(timeout=300)
        self.bot = bot
        self.requester = requester

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or \
                not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Admin Only",
                                  "Only admins can change bot settings."),
                ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="⚙️ Choose a settings section…", options=[
        discord.SelectOption(label="Security Toggles", value="security",
                             description="23 protection switches",
                             emoji="🛡️"),
        discord.SelectOption(label="Thresholds", value="thresholds",
                             description="Raid, spam, nuke & alt tuning",
                             emoji="🎚️"),
        discord.SelectOption(label="Logs & Channels", value="logs",
                             description="Where alerts & tickets land",
                             emoji="📋"),
        discord.SelectOption(label="VPS & Ticket Defaults", value="vps",
                             description="Default specs & cooldowns",
                             emoji="🖥️"),
    ])
    async def pick(self, interaction: discord.Interaction,
                   select: discord.ui.Select):
        section = select.values[0]
        cfg = await load_cfg(interaction.guild_id)
        if section == "security":
            embed = _toggles_embed(interaction.guild, cfg)
            view = SecurityTogglesView(self.bot, self.requester)
        elif section == "thresholds":
            embed = _numbers_embed(f"{E('chart')} Thresholds", THRESHOLDS, cfg)
            view = ThresholdsView(self.bot, self.requester)
        elif section == "logs":
            embed = _numbers_embed(f"{E('channel')} Logs & Channels", LOG_KEYS, cfg)
            view = LogsView(self.bot, self.requester)
        else:
            embed = _numbers_embed(f"{E('plans')} VPS & Ticket Defaults",
                                   VPS_KEYS, cfg)
            view = VPSDefaultsView(self.bot, self.requester)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary,
                       emoji="🔄", row=1)
    async def refresh(self, interaction: discord.Interaction,
                      button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=_home_embed(interaction.guild), view=self)


class _SectionView(discord.ui.View):
    """Shared scaffolding: requester lock + back-to-home button."""

    def __init__(self, bot, requester: discord.Member):
        super().__init__(timeout=300)
        self.bot = bot
        self.requester = requester

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member) or \
                not is_admin(interaction.user):
            await interaction.response.send_message(
                embed=error_embed("Admin Only",
                                  "Only admins can change bot settings."),
                ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Back", style=discord.ButtonStyle.primary, emoji="⬅")
    async def back(self, interaction: discord.Interaction,
                   button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=_home_embed(interaction.guild),
            view=SettingsHomeView(self.bot, self.requester))


class SecurityTogglesView(_SectionView):
    def __init__(self, bot, requester: discord.Member):
        super().__init__(bot, requester)
        row, per_row = 0, 0
        for attr, name in TOGGLES:
            btn = discord.ui.Button(label=name,
                                    style=discord.ButtonStyle.secondary, row=row)
            btn.callback = self._make_toggle(attr)
            self.add_item(btn)
            per_row += 1
            if per_row == 5:
                row += 1
                per_row = 0

    def _make_toggle(self, attr: str):
        async def cb(interaction: discord.Interaction):
            cfg = await load_cfg(interaction.guild_id)
            current = bool(getattr(cfg, attr, True))
            await save_cfg(interaction.guild_id, attr, not current)
            cfg = await load_cfg(interaction.guild_id)
            await interaction.response.edit_message(
                embed=_toggles_embed(interaction.guild, cfg), view=self)
        return cb


class ThresholdsView(_SectionView):
    def __init__(self, bot, requester: discord.Member):
        super().__init__(bot, requester)
        options = [discord.SelectOption(label=label, value=attr)
                   for attr, label in THRESHOLDS[:25]]
        select = discord.ui.Select(placeholder="🎚️ Pick a threshold to edit…",
                                   options=options)
        select.callback = self._pick
        self.add_item(select)

    async def _pick(self, interaction: discord.Interaction):
        attr = interaction.data["values"][0]
        label = next(l for a, l in THRESHOLDS if a == attr)
        cfg = await load_cfg(interaction.guild_id)
        await interaction.response.send_modal(NumberModal(
            self, interaction.guild_id, attr, label,
            getattr(cfg, attr, 0)))


class LogsView(_SectionView):
    def __init__(self, bot, requester: discord.Member):
        super().__init__(bot, requester)
        options = [discord.SelectOption(label=label, value=attr)
                   for attr, label in LOG_KEYS]
        select = discord.ui.Select(placeholder="📋 Pick a channel binding…",
                                   options=options)
        select.callback = self._pick
        self.add_item(select)

    async def _pick(self, interaction: discord.Interaction):
        attr = interaction.data["values"][0]
        label = next(l for a, l in LOG_KEYS if a == attr)
        cfg = await load_cfg(interaction.guild_id)
        await interaction.response.send_modal(NumberModal(
            self, interaction.guild_id, attr, label,
            getattr(cfg, attr, None) or 0))


class VPSDefaultsView(_SectionView):
    def __init__(self, bot, requester: discord.Member):
        super().__init__(bot, requester)
        options = [discord.SelectOption(label=label, value=attr)
                   for attr, label in VPS_KEYS]
        options.append(discord.SelectOption(
            label="Anti-nuke response", value="anti_nuke_action",
            description="strip | kick | ban"))
        select = discord.ui.Select(placeholder="🖥️ Pick a default to edit…",
                                   options=options)
        select.callback = self._pick
        self.add_item(select)

    async def _pick(self, interaction: discord.Interaction):
        attr = interaction.data["values"][0]
        if attr == "anti_nuke_action":
            label = "Anti-nuke response (strip/kick/ban)"
            current = getattr(await load_cfg(interaction.guild_id), attr, "strip")
            modal = NumberModal(self, interaction.guild_id, attr, label, current)
        else:
            label = next(l for a, l in VPS_KEYS if a == attr)
            cfg = await load_cfg(interaction.guild_id)
            modal = NumberModal(self, interaction.guild_id, attr, label,
                                getattr(cfg, attr, 0), allow_zero=False)
        await interaction.response.send_modal(modal)


class SettingsCog(commands.Cog):
    """Interactive per-guild settings console."""

    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(
        name="settings",
        description="Interactive bot settings console (admin)")
    @commands.guild_only()
    async def settings_cmd(self, ctx: commands.Context):
        if not is_admin(ctx.author):
            return await ctx.send(
                embed=error_embed("Admin Only",
                                  "Only admins can open the settings console."),
                ephemeral=True)
        await ctx.send(embed=_home_embed(ctx.guild),
                       view=SettingsHomeView(self.bot, ctx.author),
                       ephemeral=True)


async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
