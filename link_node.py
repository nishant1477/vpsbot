#!/usr/bin/env python3
"""
════════════════════════════════════════════════════════════════════
  Arqon Host — Node Link Wizard
════════════════════════════════════════════════════════════════════

Run this ON THE NODE MACHINE (the server that will actually host
customer VPS containers). It:

  1. Detects which virtualization backends this machine supports
     (Docker / LXC / LXD / KVM)
  2. Optionally installs the required system packages
  3. Writes node-agent/.env with your node identity + API key
  4. Self-tests the agent, then (optionally) installs it as a
     systemd service
  5. Prints the exact command to register the node on your panel

Usage (interactive):   python link_node.py
Usage (automated):     python link_node.py --key <NODE_AGENT_API_KEY> \
                           --panel http://panel-ip:5000 --yes
"""

import argparse
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
AGENT_DIR = ROOT / "node-agent"
AGENT_ENV = AGENT_DIR / ".env"

VERSION = "3.0.0"

BANNER = r"""
    ╔══════════════════════════════════════════════════════════╗
    ║      A R Q O N   H O S T  —  N O D E   L I N K           ║
    ║        Attach this machine to your hosting cluster        ║
    ╚══════════════════════════════════════════════════════════╝
"""

BOLD, DIM = "1", "2"
GREEN, YELLOW, RED, CYAN, MAGENTA = "92", "93", "91", "96", "95"


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def info(m): print(c(CYAN, f"  [i] {m}"))
def ok(m): print(c(GREEN, f"  [+] {m}"))
def warn(m): print(c(YELLOW, f"  [!] {m}"))
def err(m): print(c(RED, f"  [x] {m}"))


def ask(prompt: str, default: str = "", required: bool = False, hint: str = "") -> str:
    tail = f" {c(DIM, f'[{default}]')}" if default else ""
    hint_txt = f"  {c(DIM, hint)}" if hint else ""
    while True:
        try:
            v = input(c(MAGENTA, f"  ? {prompt}{tail}{hint_txt}: ")).strip()
        except EOFError:
            v = default
        if not v:
            if default:
                return default
            if required:
                err("This field is required.")
                continue
            return ""
        return v


def ask_bool(prompt: str, default: bool = True) -> bool:
    tail = " [Y/n]" if default else " [y/N]"
    try:
        v = input(c(MAGENTA, f"  ? {prompt}{tail}: ")).strip().lower()
    except EOFError:
        return default
    if not v:
        return default
    return v in ("y", "yes")


# ──────────────────────────────────────────────────────────────────────
#  Virtualization detection (lightweight, no imports from node-agent)
# ──────────────────────────────────────────────────────────────────────

def which(binary: str) -> bool:
    return shutil.which(binary) is not None


def run_ok(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def detect_backends() -> dict:
    """Probe what this machine can actually run."""
    out = {"docker": False, "lxc": False, "lxd": False, "kvm": False}
    if which("docker") and run_ok(["docker", "info"]):
        out["docker"] = True
    if which("lxc-create"):
        out["lxc"] = True
    if (which("lxd") or which("lxc")) and run_ok(["lxc", "version"]):
        out["lxd"] = True
    if (which("qemu-system-x86_64") or which("virsh")) and Path("/dev/kvm").exists():
        out["kvm"] = True
    return out


BACKEND_PACKAGES = {
    "docker": ["docker.io"],
    "lxc": ["lxc"],
    "lxd": ["lxd"],
    "kvm": ["qemu-kvm", "libvirt-daemon-system"],
}


def install_backend_packages(backends: list[str]) -> None:
    if not backends:
        return
    if not which("apt-get"):
        warn("Non-apt system — install these manually: " + " ".join(
            BACKEND_PACKAGES[b] for b in backends))
        return
    pkgs: list[str] = []
    for b in backends:
        pkgs += BACKEND_PACKAGES[b]
    info(f"Installing system packages: {', '.join(pkgs)} (this can take a while)")
    subprocess.run(["apt-get", "update"], check=False)
    for p in pkgs:
        subprocess.run(["apt-get", "install", "-y", p], check=False)
    for svc in ("docker", "libvirtd"):
        subprocess.run(["systemctl", "enable", "--now", svc], capture_output=True)


# ──────────────────────────────────────────────────────────────────────
#  Networking helpers
# ──────────────────────────────────────────────────────────────────────

def detect_public_ip() -> str:
    """Best-effort public IP; falls back to the primary local IP."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                ip = r.read().decode().strip()
                if ip:
                    return ip
        except Exception:
            continue
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


# ──────────────────────────────────────────────────────────────────────
#  Steps
# ──────────────────────────────────────────────────────────────────────

def step_detect() -> dict:
    info("Detecting virtualization support ...")
    backends = detect_backends()
    labels = {"docker": "Docker", "lxc": "LXC (unprivileged)", "lxd": "LXD (full)",
              "kvm": "KVM (VMs)"}
    for key, label in labels.items():
        mark = c(GREEN, "✔ supported") if backends[key] else c(RED, "✘ not found")
        print(f"      {label:<18} {mark}")
    if not any(backends.values()):
        warn("No virtualization backend detected yet — this machine cannot host VPS containers.")
        if ask_bool("Install the missing system packages now?", True):
            install_backend_packages(["docker", "lxc", "lxd", "kvm"])
            backends = detect_backends()
    return backends


def step_install_deps(backends: dict) -> None:
    missing = [b for b, ok_ in backends.items() if not ok_]
    if missing and ask_bool("Try installing system packages for: " + ", ".join(missing) + "?", False):
        install_backend_packages(missing)


AGENT_ENV_TEMPLATE = """# ═══════════════════════════════════════════════════════════════════
# Arqon Host Node Agent — {node_name}
# Generated by link_node.py v{version}
# ═══════════════════════════════════════════════════════════════════
NODE_NAME={node_name}
NODE_LOCATION={node_location}
NODE_AGENT_HOST=0.0.0.0
NODE_AGENT_PORT={port}
NODE_AGENT_API_KEY={api_key}
NODE_AGENT_SECRET={secret}
NODE_AGENT_DEBUG=false

DOCKER_ENABLED={docker}
LXC_ENABLED={lxc}
LXD_ENABLED={lxd}
KVM_ENABLED={kvm}

NETWORK_BRIDGE=arqonbr0
STORAGE_POOL=default
MAX_CONTAINERS=100
MAX_VMS=10
VPS_BASE_PATH=/var/lib/arqon/vps
BACKUP_PATH=/var/lib/arqon/backups
TEMPLATE_PATH=/var/lib/arqon/templates
MONITOR_INTERVAL=30
FIREWALL_ENABLED=true
FIREWALL_BACKEND=iptables
"""


def step_write_env(cfg: dict) -> None:
    if AGENT_ENV.exists():
        backup = AGENT_ENV.with_name(AGENT_ENV.name + ".bak")
        shutil.copy(AGENT_ENV, backup)
        warn(f"Existing node-agent/.env backed up → {backup.name}")
    AGENT_ENV.write_text(AGENT_ENV_TEMPLATE.format(version=VERSION, **cfg),
                         encoding="utf-8")
    os.chmod(AGENT_ENV, 0o600)
    ok(f"Node config written → node-agent/.env")


def step_selftest(cfg: dict) -> bool:
    """Boot the agent briefly and hit /health to prove it works."""
    info("Self-testing node agent (boots it for ~8 seconds) ...")
    if not port_open(int(cfg["port"])):
        err(f"Port {cfg['port']} is already in use by another process.")
        return False
    python = sys.executable
    if (AGENT_DIR / "venv" / "bin" / "python").exists():
        python = str(AGENT_DIR / "venv" / "bin" / "python")
    proc = None
    try:
        proc = subprocess.Popen(
            [python, "agent.py"],
            cwd=AGENT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time
        deadline = time.time() + 15
        url = f"http://127.0.0.1:{cfg['port']}/api/v1/health"
        while time.time() < deadline:
            try:
                # same auth contract as the bot's NodeClient: Bearer token
                req = urllib.request.Request(url, headers={
                    "Authorization": f"Bearer {cfg['api_key']}"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    body = r.read().decode()
                    if r.status == 200 and "stats" in body:
                        ok(f"Agent answered at {url} — node agent is healthy")
                        return True
            except Exception:
                time.sleep(1)
        warn("Agent did not answer /api/v1/health within 15s (check: cd node-agent && python agent.py)")
        return False
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def step_systemd(cfg: dict) -> None:
    if sys.platform != "linux":
        err("systemd is Linux-only. Start manually:  cd node-agent && python agent.py")
        return
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        err("Run as root for systemd:  sudo python link_node.py")
        return
    unit = f"""[Unit]
Description=Arqon Host Node Agent ({cfg['node_name']})
After=network.target docker.service libvirtd.service

[Service]
Type=simple
User=root
WorkingDirectory={AGENT_DIR}
ExecStart={sys.executable} {AGENT_DIR / 'agent.py'}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    path = Path("/etc/systemd/system/arqon-node-agent.service")
    path.write_text(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "enable", "--now", "arqon-node-agent"], check=False)
    ok("Node agent installed + started as arqon-node-agent.service")
    info("Logs:  journalctl -u arqon-node-agent -f")


def print_panel_steps(cfg: dict) -> None:
    print()
    print(c(BOLD, "  ═══════════════════════════════════════════════════════"))
    print(c(GREEN, c(BOLD, f"  Node '{cfg['node_name']}' is linked and running!")))
    print(c(BOLD, "  ═══════════════════════════════════════════════════════"))
    print()
    print(f"  Register this node on your PANEL machine (pick one):")
    print()
    print(f"   • Dashboard → Nodes → Add Node:")
    print(f"       Name: {cfg['node_name']}")
    print(f"       Host: {cfg['public_ip']}   Port: {cfg['port']}")
    print(f"       Key : {cfg['api_key']}")
    print()
    print(f"   • Or in Discord (admin account): type {c(CYAN, '!node-add')}")
    print(f"       Node Name : {cfg['node_name']}")
    print(f"       Host      : {cfg['public_ip']}")
    print(f"       Agent Port: {cfg['port']}")
    print(f"       API Key   : {cfg['api_key']}")
    print()
    info(f"Make sure port {cfg['port']} is open (firewall/security group):")
    info(f"      sudo ufw allow {cfg['port']}/tcp")
    print()


# ──────────────────────────────────────────────────────────────────────
#  Main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(
        prog="link_node.py",
        description="Arqon Host — link this machine as a hosting node",
    )
    p.add_argument("--key", help="Node API key (must match the panel's NODE_AGENT_API_KEY)")
    p.add_argument("--name", help="Node name (default: hostname)")
    p.add_argument("--location", help="Node location label, e.g. US-East")
    p.add_argument("--port", help="Agent port (default: 8080)")
    p.add_argument("--panel", help="Panel URL (optional, just for the summary printout)")
    p.add_argument("--skip-deps", action="store_true", help="Don't offer system package installs")
    p.add_argument("--skip-test", action="store_true", help="Skip the agent self-test")
    p.add_argument("--yes", action="store_true", help="Accept all defaults (needs --key)")
    args = p.parse_args()

    print(BANNER)

    if not (AGENT_DIR / "agent.py").exists():
        err("node-agent/ folder not found next to link_node.py — run this from the repo root.")
        return 1

    hostname = platform.node() or "node-1"
    cfg = {}

    print(c(BOLD, "  ── 1. Node identity ────────────────────────────────────"))
    info("This machine will host customer VPS containers for your cluster.")
    cfg["node_name"] = args.name or (hostname if args.yes else ask(
        "Node name", hostname, hint="(shows up in !nodes and the dashboard)"))
    cfg["node_location"] = args.location or ("auto" if args.yes else ask(
        "Node location", "auto", hint="(e.g. US-East, EU-West, Asia)"))

    print()
    print(c(BOLD, "  ── 2. Virtualization backends ──────────────────────────"))
    backends = step_detect()
    if not args.skip_deps:
        step_install_deps(backends)

    print()
    print(c(BOLD, "  ── 3. Agent settings ───────────────────────────────────"))
    cfg["port"] = args.port or ("8080" if args.yes else ask(
        "Agent port", "8080", hint="(must be reachable FROM the panel machine)"))
    if args.key:
        cfg["api_key"] = args.key
    else:
        cfg["api_key"] = ask(
            "Node API key", required=True,
            hint="(copy NODE_AGENT_API_KEY from the panel's .env — setup.py printed it)")
        if not cfg["api_key"]:
            err("The API key must match the panel's NODE_AGENT_API_KEY, or the panel will reject this node.")
            return 2
    cfg["secret"] = secrets.token_hex(24)

    print()
    print(c(BOLD, "  ── 4. Writing config ───────────────────────────────────"))
    cfg["docker"] = "true" if backends.get("docker") else "false"
    cfg["lxc"] = "true" if backends.get("lxc") else "false"
    cfg["lxd"] = "true" if backends.get("lxd") else "false"
    cfg["kvm"] = "true" if backends.get("kvm") else "false"
    step_write_env(cfg)

    print()
    print(c(BOLD, "  ── 5. Self-test ────────────────────────────────────────"))
    if not args.skip_test:
        step_selftest(cfg)
    else:
        warn("Self-test skipped (--skip-test)")

    print()
    print(c(BOLD, "  ── 6. Service install ──────────────────────────────────"))
    if args.yes or ask_bool("Install the node agent as a systemd service?", True):
        step_systemd(cfg)

    cfg["public_ip"] = detect_public_ip()
    print_panel_steps(cfg)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        warn("Node link cancelled.")
        sys.exit(130)
