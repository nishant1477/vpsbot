"""Arqon Bot — next-level help menu.

A fully interactive, category-driven help experience:

    /help  →  hero panel + category dropdown
           →  per-category pages with styled command cards
           →  quick-action buttons (Home / All Commands / Invite / Support)
"""
import discord
from discord.ext import commands
from typing import Optional

from utils.emoji import E
from config import settings

CATEGORY_ORDER = [
    "start", "vps", "plans", "billing", "nodes",
    "security", "minecraft", "tickets", "ai", "utility", "admin",
]

CATEGORIES: dict = {
    "start": {
        "name": "Getting Started",
        "emoji": "🚀",
        "color": 0x9B59B6,
        "blurb": "New here? These three commands are all you need to fly. " + E("sparkles"),
        "commands": [
            ("plans", "Browse hosting plans and claim a free VPS with your invites"),
            ("invites", "See your invite count, progress bar and next unlock"),
            ("myvps", "List every VPS you own, with live status"),
        ],
        "tips": [
            f"{E('invite')} **Invite friends** to the server — every invite = hosting credit",
            f"{E('plans')} Run `/plans` → pick a plan → choose your OS from the menu → deployed!",
            f"{E('terminal')} Manage your machine with `/vps <id>` (start • stop • restart • creds)",
        ],
    },
    "vps": {
        "name": "VPS Management",
        "emoji": "🖥️",
        "color": 0x8E44AD,
        "blurb": "Full lifecycle control over your virtual machines.",
        "commands": [
            ("create", "Open the classic creation wizard (staff / custom specs)"),
            ("myvps", "List your VPS instances"),
            ("vps <id>", "VPS dashboard — power actions, credentials, delete"),
            ("reinstall <id>", "Reinstall the OS on a VPS"),
            ("ostemplates", "List every available OS image"),
            ("listvps [node]", "List all VPS on the cluster (staff)"),
        ],
        "tips": [
            f"{E('key')} Root passwords are encrypted at rest and shown **once**",
            f"{E('power')} Power buttons live inside `/vps <id>` — no typing commands",
        ],
    },
    "plans": {
        "name": "Plans & Invites",
        "emoji": "🛒",
        "color": 0xE91E63,
        "blurb": "Invite friends → unlock bigger servers. Simple as that.",
        "commands": [
            ("plans", "Interactive plan browser — claim with a click"),
            ("invites [user]", "Your invite stats + progress to the next plan"),
            ("inviteleaderboard", "Top inviters of the server " + E("trophy")),
            ("grantinvites <user> <n>", "Grant bonus invites (admin)"),
            ("resetinvites <user>", "Wipe a member's invite stats (admin)"),
            ("plan-add / plan-remove / plan-list / plan-seed", "Plan management (admin)"),
            ("plan-edit <name>", "Field-by-field template editor (admin)"),
            ("plan-toggle <name>", "Show/hide a plan (admin)"),
            ("plan-export / plan-import", "Move templates as JSON (admin)"),
        ],
        "tips": [
            f"{E('gift')} Bonus invites stack on top of real ones",
            f"{E('warning')} Members who leave take their invite back — no farming!",
            f"{E('gear')} `/plan-edit` + `/settings` = full no-code template control",
        ],
    },
    "billing": {
        "name": "Billing",
        "emoji": "💼",
        "color": 0x2ECC71,
        "blurb": "Wallets, deposits and invoices for paid hosting.",
        "commands": [
            ("balance", "Check your wallet balance"),
            ("deposit", "Add demo funds to your wallet"),
            ("transactions", "Your last 10 transactions"),
            ("createinvoice <user> <amount>", "Invoice a customer (admin)"),
        ],
        "tips": [f"{E('info')} Invite plans are 100% free — billing is only for paid tiers."],
    },
    "nodes": {
        "name": "Nodes & Cluster",
        "emoji": "🌐",
        "color": 0x2C3E50,
        "blurb": "The multi-node backbone that hosts every VPS.",
        "commands": [
            ("nodes", "Cluster overview — status, latency, load"),
            ("node <id>", "Detailed node stats with live bars"),
            ("node-add", "Register a node machine (admin)"),
            ("node-remove <id>", "Unregister a node (admin)"),
            ("linknode", "How to link a new machine as a node"),
        ],
        "tips": [f"{E('zap')} New VPS orders are auto-scheduled to the least-loaded node"],
    },
    "security": {
        "name": "Security & Guardian",
        "emoji": "🛡️",
        "color": 0xE74C3C,
        "blurb": "Guardian v2 enforcement + Security Suite v3 — 25 protections.",
        "commands": [
            ("security", "Suite control center — 25 protections, live toggles"),
            ("security-test", "Full self-test: permissions, chain, coverage"),
            ("incidents [n]", "Incident timeline with severity"),
            ("evidence <id>", "Collected evidence for an incident"),
            ("verifylogs", "Verify the tamper-evident hash chain"),
            ("panic <on|off>", "Maximum defence in one command (admin)"),
            ("safe-mode <on|off>", "Monitor-only: detect, never punish (admin)"),
            ("emergency [min]", "Freeze every channel instantly (admin)"),
            ("unlockall", "Lift the emergency lockdown (admin)"),
            ("sec-snapshot", "Save a recovery baseline (admin)"),
            ("sec-restore", "Two-person baseline restore (admin)"),
            ("honeypot <channel>", "Arm a decoy channel (admin)"),
            ("honeypots", "List armed honeypots (staff)"),
            ("threatshare export/import/channel/status", "Cross-server intel (admin)"),
            ("strikes <user> / strikes-clear", "Anti-evasion records (staff)"),
            ("granttemp <user> <perm> <30m|2h|3d>", "Expiring permissions (admin)"),
            ("temp-privs / revoketemp <id>", "Manage temp privileges (staff)"),
            ("approvals", "Pending two-person decisions (staff)"),
            ("guardian", "Guardian v2 dashboard with live toggles"),
            ("guardian-punish <strip|kick|ban>", "Anti-nuke response mode (admin)"),
            ("guardian-age <days>", "Kick accounts younger than N days (admin)"),
            ("antiinvite <on|off>", "Block Discord invite links (admin)"),
            ("warn <user> <reason>", "Warn a member — auto-punish at 3/5/6 warns"),
            ("warnings [user] / resetwarns <user>", "Warning records"),
            ("mute <user> <minutes> / unmute <user>", "Timeout control (staff)"),
            ("whitelist add/remove/list", "Guardian whitelist (admin)"),
            ("guardian-logs", "Recent Guardian events"),
        ],
        "tips": [
            f"{E('shield')} Suite watches nukes, abuse, escalation, webhooks, raids, alts, names, scams 24/7",
            f"{E('vault')} Every incident is hash-chained — `/verifylogs` proves nothing was tampered",
            f"{E('info')} Run `/sec-snapshot` once so smart recovery + drift watch are armed",
        ],
    },
    "minecraft": {
        "name": "Minecraft Hosting",
        "emoji": "⛏️",
        "color": 0x27AE60,
        "blurb": "Paper, Purpur, Fabric, Forge — deployed via Pterodactyl.",
        "commands": [
            ("mccreate", "Create a Minecraft server"),
            ("mcmy", "List your Minecraft servers"),
            ("mc <id>", "Minecraft server dashboard"),
            ("mcsoftware", "Supported server software & versions"),
        ],
        "tips": [f"{E('info')} Requires a Pterodactyl panel configured in `.env`"],
    },
    "tickets": {
        "name": "Tickets & Support",
        "emoji": "🎫",
        "color": 0xF39C12,
        "blurb": "Human help, one click away.",
        "commands": [
            ("ticket", "Open a support ticket"),
            ("ticket close", "Close your ticket"),
            ("ticket add <user>", "Add a member to the ticket"),
            ("tickets", "List open tickets (staff)"),
        ],
        "tips": [f"{E('clock')} Average response time: usually under an hour"],
    },
    "ai": {
        "name": "Arqon AI",
        "emoji": "🤖",
        "color": 0x3498DB,
        "blurb": "A built-in AI assistant for hosting questions " + E("ai"),
        "commands": [
            ("ai <question>", "Ask the AI anything — hosting, Linux, troubleshooting"),
            ("ai-reset", "Clear your AI conversation memory"),
            ("ai-status", "Show AI provider, model and state"),
            ("ai-toggle", "Enable / disable the assistant (admin)"),
        ],
        "tips": [
            f"{E('info')} Works with OpenAI, Groq, Z.ai, Ollama — any OpenAI-compatible API",
            f"{E('info')} The API key is **optional** — everything else works without it",
        ],
    },
    "utility": {
        "name": "Utility",
        "emoji": "🔧",
        "color": 0x95A5A6,
        "blurb": "Handy everyday tools.",
        "commands": [
            ("ping", "Bot latency"),
            ("profile [user]", "Member profile"),
            ("serverinfo", "Discord server statistics"),
            ("whois <user>", "Detailed user info (staff)"),
            ("password", "Generate a strong password"),
            ("uuid", "Generate a UUID"),
            ("avatar [user]", "Full-size avatar"),
        ],
        "tips": [],
    },
    "admin": {
        "name": "Admin",
        "emoji": "👑",
        "color": 0x9B59B6,
        "blurb": "Multi-admin team management, monitoring, backups, users.",
        "commands": [
            ("settings", "Interactive settings console — toggles, thresholds, channels (admin)"),
            ("addadmin <user> [admin|staff]", "Give ANY user bot admin — no roles needed (owner)"),
            ("removeadmin <user>", "Revoke a bot admin (owner)"),
            ("admins", "List the full admin team"),
            ("set-role <type> <role>", "Bind Discord roles to bot levels"),
            ("add-premium / remove-premium", "Manage premium members"),
            ("ban-user / unban-user", "Bot-level bans"),
            ("backups", "Backup management"),
            ("monitor", "Cluster monitoring controls"),
            ("logs", "Recent audit logs"),
            ("editemoji", "Set custom Discord emojis for the bot (admin)"),
        ],
        "tips": [
            f"{E('crown')} Admins granted with /addadmin work in EVERY server — perfect for staff teams",
            f"{E('gear')} `/settings` edits protections, thresholds & channels with zero code",
            f"{E('lock')} Full list: `docs/COMMANDS.md` in the repo",
        ],
    },
}

HOME_COLOR = 0x9B59B6


def _cmd_line(name: str, desc: str) -> str:
    return f"{E('zap')} `/{name}` — {desc}"


def _category_embed(cat_key: str) -> discord.Embed:
    cat = CATEGORIES[cat_key]
    embed = discord.Embed(
        title=f"{cat['emoji']} {cat['name']}",
        color=cat["color"],
    )
    lines = "\n".join(_cmd_line(n, d) for n, d in cat["commands"])
    embed.description = f"{cat['blurb']}\n\n{lines}"
    if cat["tips"]:
        embed.add_field(
            name=f"{E('gem')} Pro tips",
            value="\n".join(cat["tips"]),
            inline=False,
        )
    embed.set_footer(text=f"{settings.BOT_FOOTER} • use the menu to jump between categories")
    return embed


def _home_embed(bot) -> discord.Embed:
    counts = {
        "servers": len(bot.guilds),
        "commands": len([c for c in bot.tree.get_commands()]),
    }
    embed = discord.Embed(
        title=f"{E('brand')} {settings.BOT_NAME} — Help Center",
        description=(
            f"{E('sparkles')} **The all-in-one hosting bot.** Deploy free VPS with invites, "
            f"manage machines like a pro, and let AI answer your questions.\n\n"
            f"{E('chart')} Serving **{counts['servers']}** server(s) with "
            f"**{counts['commands']}** slash commands\n\n"
            f"{E('search')} **Pick a category below** to see everything this bot can do."
        ),
        color=HOME_COLOR,
    )
    embed.add_field(
        name=f"{E('rocket')} Quick start",
        value=(
            f"`/plans` — browse & claim a free VPS\n"
            f"`/invites` — track your progress\n"
            f"`/myvps` — manage your machines"
        ),
        inline=False,
    )
    embed.add_field(
        name=f"{E('ai')} Pro features",
        value=(
            f"Multi-node cluster " + E("node") + " • AI assistant " + E("bot") +
            " • Guardian anti-raid " + E("shield") + " • Live dashboard " + E("chart")
        ),
        inline=False,
    )
    if settings.BOT_LOGO_URL:
        embed.set_thumbnail(url=settings.BOT_LOGO_URL)
    embed.set_footer(text=f"{settings.BOT_FOOTER} • select a category in the menu below")
    return embed


class HelpView(discord.ui.View):
    def __init__(self, bot, requester: discord.Member, start_category: Optional[str] = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.requester = requester

        options = []
        for key in CATEGORY_ORDER:
            cat = CATEGORIES[key]
            options.append(discord.SelectOption(
                label=cat["name"],
                value=key,
                description=cat["blurb"][:90],
                emoji=cat["emoji"],
            ))
        self.select = discord.ui.Select(
            placeholder=f"{E('book')} Choose a help category…",
            options=options,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        if start_category:
            # prime the dropdown text; content is rendered on interaction
            self.select.default_values = [start_category]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Not Yours",
                    description="This help menu belongs to someone else — run `/help` for your own!",
                    color=0xE74C3C,
                ), ephemeral=True)
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        await interaction.response.edit_message(embed=_category_embed(key), view=self)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=_home_embed(self.bot), view=self)

    @discord.ui.button(label="All Commands", style=discord.ButtonStyle.primary, row=1)
    async def all_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        lines = []
        for key in CATEGORY_ORDER:
            cat = CATEGORIES[key]
            names = " • ".join(f"`/{n.split(' <')[0].split(' /')[0]}`" for n, _ in cat["commands"])
            lines.append(f"{cat['emoji']} **{cat['name']}**\n{names}")
        embed = discord.Embed(
            title=f"{E('list')} Every Command",
            description="\n".join(lines)[:4000],
            color=HOME_COLOR,
        )
        embed.set_footer(text=f"{settings.BOT_FOOTER} • /help <category> style menu for details")
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Support", style=discord.ButtonStyle.success, row=1, emoji="💬")
    async def support(self, interaction: discord.Interaction, button: discord.ui.Button):
        url = settings.BOT_SUPPORT_SERVER or settings.BOT_WEBSITE
        if url:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"{E('ticket')} Need a human?",
                    description=f"Join our support server: {url}",
                    color=0x2ECC71,
                ), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"{E('ticket')} Need a human?",
                    description="Open a ticket right here with `/ticket` — staff will jump in!",
                    color=0x2ECC71,
                ), ephemeral=True)


class HelpCog(commands.Cog):
    """Interactive help center."""

    def __init__(self, bot):
        self.bot = bot

    def build_home(self) -> discord.Embed:
        return _home_embed(self.bot)

    @commands.hybrid_command(name="help", description="Open the interactive help center")
    async def help_cmd(self, ctx: commands.Context, *, category: str = None):
        view = HelpView(self.bot, ctx.author)

        if category:
            key = category.lower().strip()
            for ck in CATEGORY_ORDER:
                if key in (ck, CATEGORIES[ck]["name"].lower()):
                    view.select.default_values = [ck]
                    await ctx.send(embed=_category_embed(ck), view=view)
                    return
            await ctx.send(
                embed=discord.Embed(
                    title=f"{E('search')} Unknown category",
                    description=f"**{category}** isn't a help category.\n"
                                f"Try one of: " + ", ".join(CATEGORIES[k]["name"] for k in CATEGORY_ORDER),
                    color=0xF39C12,
                ), ephemeral=True)
            return

        await ctx.send(embed=self.build_home(), view=view)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
