"""Arqon Bot API — read-only JSON API over the bot database (FastAPI).

Endpoints (all under /api):
    POST /api/auth/login        → JWT token (dashboard credentials)
    GET  /api/stats             → global counters
    GET  /api/vps               → VPS list
    GET  /api/vps/{id}          → VPS detail
    GET  /api/nodes             → node fleet
    GET  /api/tickets           → tickets
    GET  /api/monitoring        → per-node metrics
    GET  /api/billing           → billing summary

Run: uvicorn api:app --host 127.0.0.1 --port 8100
"""
import os
import sys
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import jwt as pyjwt

from config import settings

app = FastAPI(title="Arqon Bot API", version="2.0.0",
              description="Read-only JSON API for the Arqon hosting bot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_db_file = settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")
DB_PATH = _db_file if os.path.isabs(_db_file) else os.path.join(PROJECT_ROOT, _db_file.lstrip("./"))
security = HTTPBearer(auto_error=False)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH or "database/arqon.db")
    conn.row_factory = sqlite3.Row
    return conn


def _rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


class LoginRequest(BaseModel):
    username: str
    password: str


def verify_password(password: str, hashed: str) -> bool:
    import hashlib
    import secrets as _secrets
    try:
        if not hashed or "$" not in hashed:
            return False
        salt_hex, digest_hex = hashed.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000).hex()
        return _secrets.compare_digest(digest, digest_hex)
    except (ValueError, TypeError):
        return False


def get_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    if not credentials:
        raise HTTPException(401, "Missing bearer token")
    try:
        payload = pyjwt.decode(credentials.credentials, settings.JWT_SECRET,
                               algorithms=[settings.JWT_ALGORITHM])
        return payload
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")


_api_login_attempts = {}


@app.post("/api/auth/login")
def login(body: LoginRequest):
    import time as _time
    ip = body.username or "?"  # api callers are per-client, rate-limit by username+IP
    key = f"{ip}"
    now = _time.time()
    hits = [t for t in _api_login_attempts.get(key, []) if now - t < 300]
    _api_login_attempts[key] = hits
    if len(hits) >= 10:
        raise HTTPException(429, "Too many login attempts — wait 5 minutes")
    conn = db()
    row = conn.execute(
        "SELECT * FROM dashboard_users WHERE username = ? AND is_active = 1",
        (body.username,)).fetchone()
    conn.close()
    if not row or not verify_password(body.password, row["password_hash"]):
        _api_login_attempts.setdefault(key, []).append(now)
        raise HTTPException(401, "Invalid credentials")
    token = pyjwt.encode({
        "sub": row["username"],
        "role": row["role"],
        "exp": datetime.utcnow() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/stats")
def stats(user=Depends(get_token)):
    conn = db()
    out = {
        "total_vps": conn.execute("SELECT COUNT(*) c FROM vps_instances WHERE deleted_at IS NULL").fetchone()["c"],
        "running_vps": conn.execute("SELECT COUNT(*) c FROM vps_instances WHERE status='RUNNING' AND deleted_at IS NULL").fetchone()["c"],
        "total_nodes": conn.execute("SELECT COUNT(*) c FROM nodes").fetchone()["c"],
        "online_nodes": conn.execute("SELECT COUNT(*) c FROM nodes WHERE status='ONLINE'").fetchone()["c"],
        "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        "open_tickets": conn.execute("SELECT COUNT(*) c FROM tickets WHERE status IN ('OPEN','CLAIMED')").fetchone()["c"],
    }
    conn.close()
    return out


@app.get("/api/vps")
def vps_list(status_filter: Optional[str] = None, user=Depends(get_token)):
    conn = db()
    sql = ("SELECT v.id, v.uuid, v.name, v.hostname, v.ram, v.cpu, v.disk, v.os_template, "
           "v.status, v.ip_address, v.ssh_port, u.username owner, n.name node, v.created_at "
           "FROM vps_instances v LEFT JOIN users u ON u.id=v.owner_id "
           "LEFT JOIN nodes n ON n.id=v.node_id WHERE v.deleted_at IS NULL")
    params = []
    if status_filter:
        sql += " AND v.status = ?"
        params.append(status_filter.upper())
    sql += " ORDER BY v.created_at DESC LIMIT 200"
    rows = _rows(conn.execute(sql, params))
    conn.close()
    return {"vps": rows}


@app.get("/api/vps/{vps_id}")
def vps_detail(vps_id: int, user=Depends(get_token)):
    conn = db()
    row = conn.execute(
        "SELECT v.*, u.username owner, n.name node FROM vps_instances v "
        "LEFT JOIN users u ON u.id=v.owner_id LEFT JOIN nodes n ON n.id=v.node_id WHERE v.id = ?",
        (vps_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "VPS not found")
    data = dict(row)
    # never expose secrets
    data.pop("root_password", None)
    return data


@app.get("/api/nodes")
def nodes(user=Depends(get_token)):
    conn = db()
    rows = _rows(conn.execute(
        "SELECT id, name, host, port, location, status, latency, total_ram, used_ram, "
        "total_disk, used_disk, vps_count, agent_version, maintenance_mode, last_heartbeat "
        "FROM nodes ORDER BY name"))
    conn.close()
    return {"nodes": rows}


@app.get("/api/tickets")
def tickets(user=Depends(get_token)):
    conn = db()
    rows = _rows(conn.execute(
        "SELECT t.id, t.ticket_number, t.subject, t.category, t.status, t.created_at, "
        "u.username creator FROM tickets t LEFT JOIN users u ON u.id=t.creator_id "
        "ORDER BY t.created_at DESC LIMIT 100"))
    conn.close()
    return {"tickets": rows}


@app.get("/api/monitoring")
def monitoring(user=Depends(get_token)):
    conn = db()
    # Same safe column list as /api/nodes — SELECT * used to leak the node
    # agent api_key and jwt_secret to any authenticated caller.
    nodes = _rows(conn.execute(
        "SELECT id, name, host, port, location, status, latency, total_ram, used_ram, "
        "total_disk, used_disk, vps_count, agent_version, maintenance_mode, last_heartbeat "
        "FROM nodes"))
    conn.close()
    for n in nodes:
        n["ram_percent"] = round((n["used_ram"] / n["total_ram"]) * 100, 1) if n["total_ram"] else 0
        n["disk_percent"] = round((n["used_disk"] / n["total_disk"]) * 100, 1) if n["total_disk"] else 0
    return {"nodes": nodes}


@app.get("/api/billing")
def billing(user=Depends(get_token)):
    conn = db()
    try:
        wallets = _rows(conn.execute(
            "SELECT w.id, w.balance, w.currency, u.username FROM wallets w "
            "LEFT JOIN users u ON u.id=w.user_id LIMIT 100"))
        plans = _rows(conn.execute("SELECT * FROM plans WHERE is_active = 1"))
    except sqlite3.OperationalError:
        wallets, plans = [], []
    conn.close()
    return {"wallets": wallets, "plans": plans}


@app.get("/api/health")
def health():
    try:
        conn = db()
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "database": db_ok, "version": "2.0.0"}
