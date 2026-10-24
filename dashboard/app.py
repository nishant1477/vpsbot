"""Arqon Dashboard — Flask web UI (v2, Aurora Glass redesign).

Reads the same SQLite database as the bot, so no API bridge is required.
Auth: DashboardUser accounts with salted PBKDF2 hashes; the first account is
seeded from DASHBOARD_USER / DASHBOARD_PASSWORD env vars (default admin / arqon-admin).
"""
import os
import sys
import sqlite3
import secrets
import hashlib
import functools
from datetime import datetime, timedelta

# allow running from dashboard/ directory (bot config lives one level up)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort, g,
)
import jwt as pyjwt

from config import settings

app = Flask(__name__)
app.config["SECRET_KEY"] = settings.DASHBOARD_SECRET
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.jinja_env.globals["now"] = datetime.utcnow
app.jinja_env.globals["bot_name"] = settings.BOT_NAME

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_file = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
DB_PATH = _db_file if os.path.isabs(_db_file) else os.path.join(PROJECT_ROOT, _db_file.lstrip("./"))


# ── password hashing (mirrors utils.helpers; no heavy imports) ────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000).hex()
    return f"{salt.hex()}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        if not hashed or "$" not in hashed:
            return False
        salt_hex, digest_hex = hashed.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000).hex()
        return secrets.compare_digest(digest, digest_hex)
    except (ValueError, TypeError):
        return False


# ── database helpers ──────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH or "database/arqon.db")
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def q(sql: str, params=()) -> list:
    return get_db().execute(sql, params).fetchall()


def q1(sql: str, params=()):
    return get_db().execute(sql, params).fetchone()


def ensure_admin_user():
    """Seed the first dashboard admin.

    Hardened: the old default password ('arqon-admin') was publicly known and
    auto-reseeded on every login attempt — a full panel takeover for any
    operator who never set DASHBOARD_PASSWORD. Now: if DASHBOARD_PASSWORD is
    unset, a strong random password is generated once and logged (owner must
    read it from the server logs on first boot).
    """
    try:
        if q1("SELECT id FROM dashboard_users LIMIT 1"):
            return
        username = os.getenv("DASHBOARD_USER", "admin")
        env_password = os.getenv("DASHBOARD_PASSWORD", "").strip()
        generated = False
        if not env_password:
            import secrets as _secrets
            env_password = _secrets.token_urlsafe(12)
            generated = True
        get_db().execute(
            "INSERT INTO dashboard_users (username, password_hash, display_name, role, is_active, created_at) "
            "VALUES (?, ?, ?, 'admin', 1, ?)",
            (username, hash_password(env_password), "Administrator", datetime.utcnow()),
        )
        get_db().commit()
        if generated:
            app.logger.warning(
                "=" * 60)
            app.logger.warning(
                "DASHBOARD_PASSWORD was not set — generated first-login password "
                "for '%s': %s", username, env_password)
            app.logger.warning(
                "Set DASHBOARD_PASSWORD in .env to choose your own.")
            app.logger.warning("=" * 60)
        else:
            app.logger.info(f"Seeded dashboard user '{username}' from DASHBOARD_PASSWORD")
    except sqlite3.OperationalError:
        # table missing until the bot has run once — ignore
        pass


# ── auth ──────────────────────────────────────────────────────────────

# naive but effective login rate limiter: 8 failed attempts / 5 min per IP
_login_attempts = {}


def _login_blocked(ip: str) -> bool:
    import time as _time
    now = _time.time()
    hits = [t for t in _login_attempts.get(ip, []) if now - t < 300]
    _login_attempts[ip] = hits
    return len(hits) >= 8


def _login_fail(ip: str) -> None:
    import time as _time
    _login_attempts.setdefault(ip, []).append(_time.time())


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # Seed the first admin once per process (random password when env unset).
        ensure_admin_user()
        client_ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                     or request.remote_addr or "?")
        if _login_blocked(client_ip):
            flash("Too many failed attempts — wait 5 minutes and try again.", "error")
            return render_template("login.html"), 429
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        try:
            row = q1("SELECT * FROM dashboard_users WHERE username = ? AND is_active = 1", (username,))
        except sqlite3.OperationalError:
            row = None
        if row and verify_password(password, row["password_hash"]):
            session.permanent = True
            session["user"] = {"id": row["id"], "username": row["username"],
                               "role": row["role"], "display": row["display_name"] or row["username"]}
            get_db().execute("UPDATE dashboard_users SET last_login = ? WHERE id = ?",
                             (datetime.utcnow(), row["id"]))
            get_db().commit()
            flash("Welcome back!", "success")
            # Only follow relative, same-site paths — an attacker-supplied
            # ?next=https://evil.com must not turn login into an open redirect.
            next_url = request.args.get("next") or ""
            if not next_url.startswith("/") or next_url.startswith("//") or "\\" in next_url:
                next_url = url_for("index")
            return redirect(next_url)
        _login_fail(client_ip)
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── template helpers ──────────────────────────────────────────────────

def pretty_status(value):
    return (value or "").replace("_", " ").title()


def status_class(value):
    v = (value or "").lower()
    if v in ("running", "online", "completed", "open", "paid", "active"):
        return "ok"
    if v in ("stopped", "offline", "failed", "closed", "overdue"):
        return "bad"
    if v in ("creating", "pending", "claimed", "restarting", "reinstalling", "starting", "stopping", "backing_up"):
        return "warn"
    if v in ("maintenance",):
        return "info"
    if v in ("error", "suspended", "banned", "deleted"):
        return "bad"
    return "muted"


def human_bytes(num):
    try:
        num = float(num)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} PB"


def ts(dt):
    if not dt:
        return "—"
    try:
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.split(".")[0])
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(dt)


app.jinja_env.filters["status_class"] = status_class
app.jinja_env.filters["pretty_status"] = pretty_status
app.jinja_env.filters["human_bytes"] = human_bytes
app.jinja_env.filters["ts"] = ts


def db_ready() -> bool:
    """sqlite3.connect() creates an empty DB file where SELECT 1 succeeds —
    check the actual schema instead so a fresh clone gets the friendly hint."""
    try:
        return q1("SELECT name FROM sqlite_master WHERE type='table' AND name='vps_instances'") is not None
    except sqlite3.OperationalError:
        return False


# ── routes ────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/dashboard")
@login_required
def index():
    if not db_ready():
        return render_template("error.html", message="Database not found — start the bot once to create it.")

    stats = {
        "total_vps": q1("SELECT COUNT(*) c FROM vps_instances WHERE deleted_at IS NULL")["c"],
        "running_vps": q1("SELECT COUNT(*) c FROM vps_instances WHERE deleted_at IS NULL AND status='RUNNING'")["c"],
        "total_nodes": q1("SELECT COUNT(*) c FROM nodes")["c"],
        "online_nodes": q1("SELECT COUNT(*) c FROM nodes WHERE status='ONLINE'")["c"],
        "users": q1("SELECT COUNT(*) c FROM users")["c"],
        "open_tickets": q1("SELECT COUNT(*) c FROM tickets WHERE status IN ('OPEN','CLAIMED')")["c"],
    }
    recent_vps = q(
        "SELECT v.*, u.username owner_name, n.name node_name FROM vps_instances v "
        "LEFT JOIN users u ON u.id = v.owner_id LEFT JOIN nodes n ON n.id = v.node_id "
        "WHERE v.deleted_at IS NULL ORDER BY v.created_at DESC LIMIT 8")
    nodes = q("SELECT * FROM nodes ORDER BY created_at DESC LIMIT 6")
    recent_tickets = q(
        "SELECT t.*, u.username creator_name FROM tickets t LEFT JOIN users u ON u.id = t.creator_id "
        "ORDER BY t.created_at DESC LIMIT 6")
    return render_template("dashboard.html", stats=stats, recent_vps=recent_vps,
                           nodes=nodes, recent_tickets=recent_tickets)


@app.route("/vps")
@login_required
def vps_list():
    status_filter = request.args.get("status", "")
    sql = ("SELECT v.*, u.username owner_name, u.discord_id, n.name node_name "
           "FROM vps_instances v LEFT JOIN users u ON u.id = v.owner_id "
           "LEFT JOIN nodes n ON n.id = v.node_id WHERE v.deleted_at IS NULL")
    params = []
    if status_filter:
        sql += " AND v.status = ?"
        params.append(status_filter.upper())
    sql += " ORDER BY v.created_at DESC LIMIT 100"
    vps_list = q(sql, params)
    return render_template("vps.html", vps_list=vps_list, status_filter=status_filter)


@app.route("/vps/create", methods=["GET", "POST"])
@login_required
def vps_create():
    if request.method == "POST":
        flash("VPS creation should be done in Discord with !create (wizard handles node scheduling).", "info")
        return redirect(url_for("vps_list"))
    nodes = q("SELECT * FROM nodes WHERE is_active = 1")
    return render_template("vps_create.html", nodes=nodes)


@app.route("/vps/<int:vps_id>")
@login_required
def vps_detail(vps_id):
    vps = q1(
        "SELECT v.*, u.username owner_name, u.discord_id, n.name node_name, n.host node_host "
        "FROM vps_instances v LEFT JOIN users u ON u.id = v.owner_id "
        "LEFT JOIN nodes n ON n.id = v.node_id WHERE v.id = ?", (vps_id,))
    if not vps:
        abort(404)
    backups = q("SELECT * FROM backups WHERE vps_instance_id = ? ORDER BY created_at DESC LIMIT 10", (vps_id,))
    return render_template("vps_detail.html", vps=vps, backups=backups)


@app.route("/nodes")
@login_required
def nodes():
    node_list = q("SELECT * FROM nodes ORDER BY is_active DESC, created_at DESC")
    return render_template("nodes.html", nodes=node_list)


@app.route("/tickets")
@login_required
def tickets():
    ticket_list = q(
        "SELECT t.*, u.username creator_name FROM tickets t "
        "LEFT JOIN users u ON u.id = t.creator_id ORDER BY t.created_at DESC LIMIT 100")
    return render_template("tickets.html", tickets=ticket_list)


@app.route("/monitoring")
@login_required
def monitoring():
    node_list = q("SELECT * FROM nodes")
    for n in node_list:
        n = dict(n)
    stats = [dict(n) for n in node_list]
    return render_template("monitoring.html", nodes=stats)


@app.route("/billing")
@login_required
def billing():
    try:
        wallets = q(
            "SELECT w.*, u.username FROM wallets w LEFT JOIN users u ON u.id = w.user_id "
            "ORDER BY w.balance DESC LIMIT 50")
        transactions = q(
            "SELECT t.*, u.username FROM transactions t LEFT JOIN wallets w ON w.id = t.wallet_id "
            "LEFT JOIN users u ON u.id = w.user_id ORDER BY t.created_at DESC LIMIT 30")
        invoices = q(
            "SELECT i.*, u.username FROM invoices i LEFT JOIN users u ON u.id = i.user_id "
            "ORDER BY i.created_at DESC LIMIT 30")
        plans = q("SELECT * FROM plans WHERE is_active = 1 ORDER BY sort_order")
    except sqlite3.OperationalError:
        wallets, transactions, invoices, plans = [], [], [], []
    total_balance = sum(w["balance"] or 0 for w in wallets) / 100
    return render_template("billing.html", wallets=wallets, transactions=transactions,
                           invoices=invoices, plans=plans, total_balance=total_balance)


@app.route("/settings")
@login_required
def settings_view():
    user = session.get("user")
    cfg = {
        "bot_name": settings.BOT_NAME, "prefix": settings.BOT_PREFIX,
        "ai_enabled": settings.AI_ENABLED, "ai_model": settings.AI_MODEL,
        "dashboard_enabled": settings.DASHBOARD_ENABLED,
        "guardian": settings.GUARDIAN_ENABLED, "backups": settings.BACKUP_ENABLED,
        "node_timeout": settings.NODE_TIMEOUT,
    }
    try:
        dashboard_users = q("SELECT id, username, role, last_login, created_at FROM dashboard_users ORDER BY id")
    except sqlite3.OperationalError:
        dashboard_users = []
    return render_template("settings.html", cfg=cfg, dashboard_users=dashboard_users)


@app.route("/settings/password", methods=["POST"])
@login_required
def change_password():
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    if len(new) < 8:
        flash("New password must be at least 8 characters.", "error")
        return redirect(url_for("settings_view"))
    row = q1("SELECT * FROM dashboard_users WHERE id = ?", (session["user"]["id"],))
    if not row or not verify_password(current, row["password_hash"]):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("settings_view"))
    get_db().execute("UPDATE dashboard_users SET password_hash = ? WHERE id = ?",
                     (hash_password(new), session["user"]["id"]))
    get_db().commit()
    flash("Password updated.", "success")
    return redirect(url_for("settings_view"))


# ── errors ────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", message="Page not found."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", message="Something went wrong on our side."), 500


if __name__ == "__main__":
    with app.app_context():
        ensure_admin_user()
    app.run(host=settings.DASHBOARD_HOST, port=settings.DASHBOARD_PORT,
            debug=os.getenv("FLASK_DEBUG", "false").lower() == "true")
