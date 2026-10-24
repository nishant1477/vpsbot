#!/usr/bin/env python3
"""
════════════════════════════════════════════════════════════════════
  Arqon Host VPS Bot — Interactive Setup Wizard
════════════════════════════════════════════════════════════════════

Walks you through first-time configuration and writes your .env file.

  • Discord bot token + owner IDs
  • Custom bot name (rebrand the whole bot in one answer)
  • Dashboard login + auto-generated secrets
  • AI API key — 100% OPTIONAL (press Enter to skip, add later anytime)
  • Optional: initialize database, seed dashboard admin, install systemd

Usage (interactive):   python setup.py
Usage (automated):     python setup.py --token <TOKEN> --owner-id <ID> --name "My Host" --yes

Set up a NODE machine (a VPS host) instead:
                       python link_node.py
"""

import argparse
import base64
import hashlib
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
ENV_FILE = ROOT / ".env"

BOT_NAME_DEFAULT = "Arqon Host VPS Bot"
VERSION = "3.0.0"

# ──────────────────────────────────────────────────────────────────────
#  Terminal helpers
# ──────────────────────────────────────────────────────────────────────

def use_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if use_color() else text


BOLD, DIM = "1", "2"
GREEN, YELLOW, RED, CYAN, MAGENTA = "92", "93", "91", "96", "95"

BANNER = r"""
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║      █████╗ ██████╗ ██████╗  ██████╗ ███╗   ██╗          ║
    ║     ██╔══██╗██╔══██╗██╔══██╗██╔═══██╗████╗  ██║          ║
    ║     ███████║██████╔╝██████╔╝██║   ██║██╔██╗ ██║          ║
    ║     ██╔══██║██╔══██╗██╔═══╝ ██║   ██║██║╚██╗██║          ║
    ║     ██║  ██║██║  ██║██║     ╚██████╔╝██║ ╚████║          ║
    ║     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝      ╚═════╝ ╚═╝  ╚═══╝          ║
    ║                                                          ║
    ║        A R Q O N   H O S T  •  V P S   B O T             ║
    ║              Setup Wizard  •  v{VERSION}                   ║
    ╚══════════════════════════════════════════════════════════╝
""".replace("{VERSION}", VERSION)


def info(msg: str) -> None:
    print(c(CYAN, f"  [i] {msg}"))


def ok(msg: str) -> None:
    print(c(GREEN, f"  [+] {msg}"))


def warn(msg: str) -> None:
    print(c(YELLOW, f"  [!] {msg}"))


def err(msg: str) -> None:
    print(c(RED, f"  [x] {msg}"))


def ask(prompt: str, default: str = "", required: bool = False,
        validator=None, secret: bool = False, hint: str = "") -> str:
    """Prompt with default + validation loop. Empty input returns default."""
    tail = f" {c(DIM, f'[{default}]')}" if default else ""
    hint_txt = f"  {c(DIM, hint)}" if hint else ""
    while True:
        try:
            value = input(c(MAGENTA, f"  ? {prompt}{tail}{hint_txt}: ")).strip()
        except EOFError:
            value = default
        if secret and value:
            print(c(DIM, "      (kept private)"))
        if not value:
            if default:
                return default
            if required:
                err("This field is required.")
                continue
            return ""
        if validator:
            problem = validator(value)
            if problem:
                err(problem)
                continue
        return value


def ask_bool(prompt: str, default: bool = True) -> bool:
    tail = " [Y/n]" if default else " [y/N]"
    while True:
        try:
            v = input(c(MAGENTA, f"  ? {prompt}{tail}: ")).strip().lower()
        except EOFError:
            return default
        if not v:
            return default
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False
        err("Please answer y or n.")


# ──────────────────────────────────────────────────────────────────────
#  Generators / validators
# ──────────────────────────────────────────────────────────────────────

def gen_fernet_key() -> str:
    """Generate a Fernet-format key without importing cryptography."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def gen_hex(n: int = 32) -> str:
    return secrets.token_hex(n)


def gen_password(n: int = 14) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def validate_token(token: str) -> str | None:
    if not re.fullmatch(r"[\w-]{20,}\.[\w-]{5,}\.[\w-]{20,}", token):
        return ("That doesn't look like a Discord bot token — get it from "
                "discord.com/developers > Applications > Bot > Reset Token.")
    return None


def validate_ids(value: str) -> str | None:
    ids = [x.strip() for x in value.split(",") if x.strip()]
    if not ids:
        return "Enter at least one Discord user ID."
    for i in ids:
        if not i.isdigit() or not (15 <= len(i) <= 21):
            return f"'{i}' is not a valid Discord user ID (15-21 digits)."
    return None


def validate_color(value: str) -> str | None:
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value.lstrip("#")):
        return "Use a 6-digit hex color like 9B59B6."
    return None


# ──────────────────────────────────────────────────────────────────────
#  .env template
# ──────────────────────────────────────────────────────────────────────

ENV_TEMPLATE = """# ═══════════════════════════════════════════════════════════════════
# {bot_name} — Environment Configuration
# Generated by setup.py v{version}
# ═══════════════════════════════════════════════════════════════════

# ── Discord Bot ──────────────────────────────────────────────────────
DISCORD_TOKEN={token}
BOT_PREFIX={prefix}
BOT_STATUS={status}
BOT_ACTIVITY_TYPE={activity}
BOT_NAME={bot_name}
BOT_FOOTER={bot_name} • Premium Hosting Automation
{client_line}
# Bot owners (comma-separated Discord user IDs)
OWNER_IDS={owner_ids}

# ── Database ─────────────────────────────────────────────────────────
DATABASE_URL=sqlite+aiosqlite:///./database/arqon.db
DATABASE_ECHO=false

# ── Security (auto-generated — keep private!) ────────────────────────
ENCRYPTION_KEY={encryption_key}
JWT_SECRET={jwt_secret}
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# ── Node Agents ──────────────────────────────────────────────────────
NODE_AGENT_HOST=0.0.0.0
NODE_AGENT_PORT={node_port}
# Shared default key — link nodes with: python link_node.py
NODE_AGENT_API_KEY={node_api_key}
NODE_AGENT_SECRET={node_secret}
NODE_HEARTBEAT_INTERVAL=30
NODE_TIMEOUT=60
NODE_REQUEST_TIMEOUT=15

# ── AI Assistant (OPTIONAL — any OpenAI-compatible provider) ─────────
AI_ENABLED={ai_enabled}
AI_API_KEY={ai_key}
AI_BASE_URL={ai_base_url}
AI_MODEL={ai_model}
AI_MAX_TOKENS=1000

# ── Pterodactyl (optional, for Minecraft deploys) ────────────────────
PTERODACTYL_URL={ptero_url}
PTERODACTYL_API_KEY={ptero_key}

# ── Monitoring ───────────────────────────────────────────────────────
MONITORING_ENABLED=true
MONITORING_ALERT_CPU=90
MONITORING_ALERT_RAM=90
MONITORING_ALERT_DISK=90
MONITORING_ALERT_CHANNEL_ID=

# ── Backups ──────────────────────────────────────────────────────────
BACKUP_ENABLED=true
BACKUP_INTERVAL_HOURS=24
BACKUP_RETENTION_DAYS=7
BACKUP_PATH=./backups

# ── Logging ──────────────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_FILE=./logs/bot.log

# ── Web Dashboard ────────────────────────────────────────────────────
DASHBOARD_ENABLED={dash_enabled}
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT={dash_port}
DASHBOARD_SECRET={dash_secret}
# Default panel login (seeded on first run — CHANGE THE PASSWORD AFTER LOGIN)
DASHBOARD_USER={dash_user}
DASHBOARD_PASSWORD={dash_password}
"""

# ──────────────────────────────────────────────────────────────────────
#  Post-setup actions
# ──────────────────────────────────────────────────────────────────────

def init_database() -> bool:
    """Create the SQLite database + tables (needs requirements installed)."""
    info("Initializing database...")
    for d in ("database", "logs", "backups"):
        (ROOT / d).mkdir(exist_ok=True)
    try:
        r = subprocess.run(
            [sys.executable, "-c",
             "import asyncio, database; asyncio.run(database.init_db())"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
            env={**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///./database/arqon.db"},
        )
    except OSError as e:
        warn(f"Could not launch python: {e}")
        return False
    if r.returncode == 0:
        ok("Database ready: database/arqon.db")
        return True
    warn("Database init failed — are requirements installed? Details:")
    for line in (r.stderr or r.stdout or "").strip().splitlines()[-4:]:
        print(c(DIM, f"      {line}"))
    info("After 'pip install -r requirements.txt', rerun: python setup.py --init-db")
    return False


def seed_dashboard_admin(username: str, password: str) -> bool:
    """Pre-seed the dashboard admin account (same PBKDF2 format as app.py)."""
    db = ROOT / "database" / "arqon.db"
    if not db.exists():
        return False
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()
    hashed = f"{salt.hex()}${digest}"
    try:
        con = sqlite3.connect(db)
        cur = con.execute(
            "INSERT OR IGNORE INTO dashboard_users "
            "(username, password_hash, display_name, role, is_active, created_at) "
            "VALUES (?, ?, 'Administrator', 'admin', 1, datetime('now'))",
            (username, hashed),
        )
        con.commit()
        con.close()
        return cur.rowcount == 1
    except sqlite3.Error:
        # Table missing → the dashboard auto-seeds from DASHBOARD_USER /
        # DASHBOARD_PASSWORD env vars at boot, so nothing is lost here.
        return False


def install_systemd() -> None:
    """Install systemd services (Linux + root only)."""
    if sys.platform != "linux":
        err("systemd install is Linux-only. On Windows just run: python bot.py")
        return
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        err("Please run as root:  sudo python setup.py  → then choose this option again.")
        return

    target = Path("/opt/arqon-host")
    info(f"Deploying bot to {target} ...")
    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns("venv", "__pycache__", "*.pyc", ".git", "logs/*", "database/*.db")
    shutil.copytree(ROOT, target, dirs_exist_ok=True, ignore=ignore)

    subprocess.run(["useradd", "-r", "-s", "/bin/bash", "-m", "arqon"],
                   capture_output=True)  # ok if already exists
    subprocess.run(["usermod", "-aG", "docker", "arqon"], capture_output=True)
    subprocess.run(["chown", "-R", "arqon:arqon", str(target)], check=False)

    info("Creating virtualenv + installing dependencies ...")
    venv = target / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=False)
    for req in ("requirements.txt", "dashboard/requirements.txt", "node-agent/requirements.txt"):
        subprocess.run([f"{venv}/bin/pip", "install", "-q", "-r", f"{target}/{req}"], check=False)

    info("Installing systemd units ...")
    for unit in ("arqon-bot.service", "arqon-dashboard.service"):
        src = ROOT / "services" / unit
        if src.exists():
            shutil.copy(src, "/etc/systemd/system/")
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "enable", "arqon-bot.service", "arqon-dashboard.service"], check=False)
    subprocess.run(["systemctl", "start", "arqon-bot.service", "arqon-dashboard.service"], check=False)
    ok("Installed + started:  arqon-bot  •  arqon-dashboard")
    info("Logs:   journalctl -u arqon-bot -f")
    info("Panel:  http://<your-ip>:5000")
    info("NOTE: if you used the wizard just now, copy the fresh .env too:")
    info(f"      cp {ENV_FILE} {target}/.env && systemctl restart arqon-bot")


# ──────────────────────────────────────────────────────────────────────
#  Main wizard
# ──────────────────────────────────────────────────────────────────────

def run_wizard(args) -> dict:
    """Collect every setting → returns the config dict."""
    print(BANNER)
    print(c(DIM, "  Answers in [brackets] are defaults — press Enter to accept."))
    print(c(DIM, "  Nothing is sent anywhere; everything stays in your local .env.\n"))

    vals = {}

    # ── 1. Discord bot token (REQUIRED) ──────────────────────────────
    print(c(BOLD, "  ── 1. Discord Bot ──────────────────────────────────────"))
    info("Create a bot at https://discord.com/developers → New Application → Bot")
    info("Enable ALL Privileged Gateway Intents on that page, then Reset Token.")
    if args.token:
        vals["token"] = args.token
        ok("Token loaded from --token argument")
    else:
        vals["token"] = ask("Discord bot token", required=True,
                            validator=validate_token, secret=True)
    vals["client_id"] = args.client_id or ask(
        "Application / Client ID", "",
        hint="(optional — used for the invite link; find it under General Information)")

    # ── 2. Bot identity (CUSTOM NAME!) ───────────────────────────────
    print()
    print(c(BOLD, "  ── 2. Bot Identity (your brand) ────────────────────────"))
    vals["bot_name"] = args.name or ask(
        "Bot name (shown everywhere: embeds, dashboard, status)",
        BOT_NAME_DEFAULT,
        hint="(this renames the entire bot — embeds, panel, AI persona)")
    vals["prefix"] = args.prefix or ask("Command prefix", "!")
    vals["status"] = ask("Bot activity text", f"{vals['bot_name']} | {vals['prefix']}help")
    vals["activity"] = ask("Activity type (playing/watching/listening/competing)", "playing")

    # ── 3. Owners ────────────────────────────────────────────────────
    print()
    print(c(BOLD, "  ── 3. Owners ───────────────────────────────────────────"))
    info("Right-click yourself in Discord → Copy User ID (enable Developer Mode first)")
    if args.owner_id:
        vals["owner_ids"] = args.owner_id
    else:
        vals["owner_ids"] = ask("Owner Discord user ID(s), comma-separated",
                                required=True, validator=validate_ids)

    # ── 4. Dashboard ─────────────────────────────────────────────────
    print()
    print(c(BOLD, "  ── 4. Web Dashboard ────────────────────────────────────"))
    vals["dash_enabled"] = "true" if ask_bool("Enable the web dashboard?", True) else "false"
    if vals["dash_enabled"] == "true":
        vals["dash_port"] = args.dash_port or ask("Dashboard port", "5000")
        vals["dash_user"] = ask("Dashboard admin username", "admin")
        vals["dash_password"] = args.dash_password or gen_password()
        vals["dash_secret"] = gen_hex(32)
        ok(f"Dashboard login → {vals['dash_user']} / {vals['dash_password']}  "
           f"{c(YELLOW, '(write this down — shown once!)')}")
    else:
        vals["dash_port"], vals["dash_user"] = "5000", "admin"
        vals["dash_password"], vals["dash_secret"] = "", gen_hex(32)

    # ── 5. Node cluster ──────────────────────────────────────────────
    print()
    print(c(BOLD, "  ── 5. Node Cluster ─────────────────────────────────────"))
    info("Nodes = machines that actually host customer VPS containers.")
    info("Link them later with:  python link_node.py")
    vals["node_port"] = args.node_port or ask("Node agent port (on node machines)", "8080")
    vals["node_api_key"] = gen_hex(24)
    vals["node_secret"] = gen_hex(24)
    ok(f"Shared node API key generated: {vals['node_api_key']}")
    info("Write it down — you'll paste it on every node machine.")

    # ── 6. AI assistant (OPTIONAL KEY!) ──────────────────────────────
    print()
    print(c(BOLD, "  ── 6. AI Assistant  (OPTIONAL) ─────────────────────────"))
    warn("No API key yet? Just press Enter — the bot works fine without it.")
    info("You can add a key later in .env (AI_API_KEY=...) or rerun this wizard.")
    vals["ai_key"] = args.ai_key or ask(
        "AI API key", "",
        hint="(optional — OpenAI / Groq / Z.ai / Ollama, any OpenAI-compatible)")
    if vals["ai_key"]:
        vals["ai_enabled"] = "true"
        print()
        info("Pick your AI provider:")
        info("  1) OpenAI   — https://api.openai.com/v1        (gpt-4o-mini)")
        info("  2) Groq     — https://api.groq.com/openai/v1   (llama-3.3-70b-versatile)")
        info("  3) Z.ai     — https://api.z.ai/api/paas/v4     (glm-4-flash)")
        info("  4) Ollama   — http://127.0.0.1:11434/v1        (llama3.1, local)")
        info("  5) Custom   — any OpenAI-compatible URL")
        choice = ask("Provider", "1")
        presets = {
            "1": ("https://api.openai.com/v1", "gpt-4o-mini"),
            "2": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
            "3": ("https://api.z.ai/api/paas/v4", "glm-4-flash"),
            "4": ("http://127.0.0.1:11434/v1", "llama3.1"),
        }
        if choice in presets:
            vals["ai_base_url"], vals["ai_model"] = presets[choice]
        else:
            vals["ai_base_url"] = ask("Base URL", "https://api.openai.com/v1")
            vals["ai_model"] = ask("Model name", "gpt-4o-mini")
        vals["ai_model"] = ask("Model", vals["ai_model"])
    else:
        vals["ai_enabled"] = "false"
        vals["ai_base_url"] = "https://api.openai.com/v1"
        vals["ai_model"] = "gpt-4o-mini"
        ok("AI assistant disabled for now (everything else works without it)")

    # ── 7. Pterodactyl (OPTIONAL) ────────────────────────────────────
    print()
    print(c(BOLD, "  ── 7. Pterodactyl  (OPTIONAL — for Minecraft hosting) ──"))
    vals["ptero_url"] = ask("Pterodactyl panel URL", "",
                            hint="(optional — blank = Minecraft module stays off)")
    if vals["ptero_url"]:
        vals["ptero_key"] = ask("Pterodactyl App API key", required=True, secret=True)
    else:
        vals["ptero_key"] = ""

    return vals


def write_env(vals: dict) -> None:
    client_line = ""
    if vals.get("client_id"):
        client_line = f"DISCORD_CLIENT_ID={vals['client_id']}"
    content = ENV_TEMPLATE.format(
        version=VERSION,
        token=vals["token"],
        prefix=vals["prefix"],
        status=vals["status"],
        activity=vals["activity"],
        bot_name=vals["bot_name"],
        client_line=client_line,
        owner_ids=vals["owner_ids"],
        encryption_key=gen_fernet_key(),
        jwt_secret=gen_hex(32),
        node_port=vals["node_port"],
        node_api_key=vals["node_api_key"],
        node_secret=vals["node_secret"],
        ai_enabled=vals["ai_enabled"],
        ai_key=vals["ai_key"],
        ai_base_url=vals["ai_base_url"],
        ai_model=vals["ai_model"],
        ptero_url=vals["ptero_url"],
        ptero_key=vals["ptero_key"],
        dash_enabled=vals["dash_enabled"],
        dash_port=vals["dash_port"],
        dash_secret=vals["dash_secret"],
        dash_user=vals["dash_user"],
        dash_password=vals["dash_password"],
    )
    if ENV_FILE.exists():
        backup = ENV_FILE.with_name(ENV_FILE.name + ".bak")
        shutil.copy(ENV_FILE, backup)
        warn(f"Existing .env backed up → {backup.name}")
    ENV_FILE.write_text(content, encoding="utf-8")
    if os.name != "nt":
        os.chmod(ENV_FILE, 0o600)
    ok(f"Configuration written → {ENV_FILE}")


def print_summary(vals: dict) -> None:
    print()
    print(c(BOLD, "  ═══════════════════════════════════════════════════════"))
    print(c(GREEN, c(BOLD, f"  {vals['bot_name']} — configuration saved!")))
    print(c(BOLD, "  ═══════════════════════════════════════════════════════"))
    if vals.get("client_id"):
        print(f"  Invite link : https://discord.com/oauth2/authorize"
              f"?client_id={vals['client_id']}&permissions=8&scope=bot%20applications.commands")
    print(f"  Dashboard   : {vals['dash_enabled']}  (login: {vals['dash_user']} / {vals['dash_password']})")
    print(f"  AI assistant: {vals['ai_enabled']}")
    print(f"  Node API key: {vals['node_api_key']}")
    print()


def post_menu(vals: dict) -> None:
    while True:
        print(c(BOLD, "  What next?"))
        print("    1) Initialize database (creates database/arqon.db)")
        print("    2) Install as systemd services (Linux VPS, needs root)")
        print("    3) Link a NODE machine (run on the VPS host)")
        print("    4) Start the bot now (foreground)")
        print("    5) Done — exit")
        choice = ask("Choice", "5")
        if choice == "1":
            init_database()
            if seed_dashboard_admin(vals["dash_user"], vals["dash_password"]):
                ok(f"Dashboard admin seeded → {vals['dash_user']}")
        elif choice == "2":
            install_systemd()
        elif choice == "3":
            script = ROOT / "link_node.py"
            subprocess.run([sys.executable, str(script),
                            "--key", vals["node_api_key"], "--port", vals["node_port"]])
        elif choice == "4":
            info("Starting bot (Ctrl+C to stop)...")
            subprocess.run([sys.executable, "bot.py"], cwd=ROOT)
        else:
            ok("Setup complete. Start anytime with:  python bot.py")
            return


def main() -> int:
    p = argparse.ArgumentParser(
        prog="setup.py",
        description="Arqon Host VPS Bot — interactive setup wizard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="All fields can also be answered interactively. AI key is optional.",
    )
    p.add_argument("--token", help="Discord bot token")
    p.add_argument("--client-id", help="Discord application/client ID")
    p.add_argument("--owner-id", help="Owner Discord user ID(s), comma-separated")
    p.add_argument("--name", help=f"Custom bot name (default: {BOT_NAME_DEFAULT})")
    p.add_argument("--prefix", help="Command prefix (default: !)")
    p.add_argument("--ai-key", help="AI API key (optional; omit to disable AI)")
    p.add_argument("--dash-port", help="Dashboard port (default: 5000)")
    p.add_argument("--dash-password", help="Dashboard admin password (default: auto-generated)")
    p.add_argument("--node-port", help="Node agent port (default: 8080)")
    p.add_argument("--yes", action="store_true", help="Accept all defaults (token still required)")
    p.add_argument("--init-db", action="store_true", help="Only initialize the database and exit")
    args = p.parse_args()

    if args.init_db:
        init_database()
        return 0

    if not sys.stdin.isatty():
        # Piped / CI mode: require --token
        if not args.token or not args.owner_id:
            err("Non-interactive mode needs at least: --token and --owner-id")
            return 2

    if args.yes:
        if not args.token or not args.owner_id:
            err("--yes needs --token and --owner-id")
            return 2
        args.name = args.name or BOT_NAME_DEFAULT
        args.prefix = args.prefix or "!"
        args.dash_port = args.dash_port or "5000"
        args.node_port = args.node_port or "8080"

    vals = run_wizard(args)
    write_env(vals)
    print_summary(vals)
    if sys.stdin.isatty():
        post_menu(vals)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Setup cancelled — nothing was changed.")
        sys.exit(130)
