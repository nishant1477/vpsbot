#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Arqon Host VPS Bot — One-Click Installer (Linux)
# ═══════════════════════════════════════════════════════════════════
#
#  FULL INSTALL (bot + dashboard) — run from anywhere:
#      bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh)
#
#  NODE INSTALL (a VPS-hosting machine that joins your cluster):
#      bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh) --node
#
#  Or clone first, then run inside the repo:
#      git clone https://github.com/nishant1477/vpsbot.git && cd vpsbot
#      sudo bash install.sh
#
#  What it does:
#    • installs system dependencies (git, python3, venv)
#    • clones / updates the repository into the install directory
#    • creates a virtualenv + installs Python dependencies
#    • launches the interactive setup wizard  (setup.py)
#      — or the node-link wizard (link_node.py) with --node
# ═══════════════════════════════════════════════════════════════════

set -e

REPO_URL="https://github.com/nishant1477/vpsbot.git"
INSTALL_DIR="/opt/arqon-host"
MODE="panel"

for arg in "$@"; do
    case "$arg" in
        --node)  MODE="node" ;;
        --help|-h)
            echo "Usage: bash install.sh [--node]"
            echo "  (default) install the Arqon Host panel (bot + dashboard)"
            echo "  --node    install this machine as a hosting NODE"
            exit 0 ;;
    esac
done

if [ "$MODE" = "node" ]; then
    INSTALL_DIR="/opt/arqon-node"
fi

G='\033[0;92m'; Y='\033[0;93m'; C='\033[0;96m'; R='\033[0;91m'; B='\033[1m'; N='\033[0m'
info()  { echo -e "  ${C}[i]${N} $1"; }
okmsg() { echo -e "  ${G}[+]${N} $1"; }
warn()  { echo -e "  ${Y}[!]${N} $1"; }
die()   { echo -e "  ${R}[x]${N} $1"; exit 1; }

echo ""
echo -e "${B}  ╔══════════════════════════════════════════════════════╗${N}"
if [ "$MODE" = "node" ]; then
echo -e "${B}  ║   A R Q O N   H O S T  —  N O D E   I N S T A L L     ║${N}"
else
echo -e "${B}  ║   A R Q O N   H O S T  —  P A N E L   I N S T A L L   ║${N}"
fi
echo -e "${B}  ╚══════════════════════════════════════════════════════╝${N}"
echo ""

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 && SUDO="sudo" || die "Not root and no sudo available. Run as root."
    warn "Not root — using sudo for privileged steps."
fi

# ── [1/6] system deps ────────────────────────────────────────────────
echo -e "${B}  [1/6] System dependencies${N}"
if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -qq || true
    $SUDO apt-get install -y -qq git curl python3 python3-venv python3-pip ca-certificates >/dev/null 2>&1 || \
        $SUDO apt-get install -y git curl python3 python3-venv python3-pip || true
elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y git curl python3 python3-pip || true
elif command -v yum >/dev/null 2>&1; then
    $SUDO yum install -y git curl python3 python3-pip || true
elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -Sy --noconfirm git curl python python-pip || true
else
    warn "Unknown package manager — make sure git + python3 + venv are installed."
fi
command -v git >/dev/null 2>&1    || die "git is required but not installed."
command -v python3 >/dev/null 2>&1 || die "python3 is required but not installed."
python3 -c "import venv" 2>/dev/null || die "python3-venv is missing (Debian/Ubuntu: sudo apt install python3-venv)."
okmsg "git + $(python3 --version) ready"

# ── [2/6] get the code ───────────────────────────────────────────────
echo -e "${B}  [2/6] Source code${N}"
if [ -f "./bot.py" ] && [ -f "./setup.py" ]; then
    INSTALL_DIR="$(pwd)"
    info "Running inside a repo checkout → installing in place: $INSTALL_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
    info "Existing install found → pulling latest ..."
    git -C "$INSTALL_DIR" pull --ff-only origin main || warn "git pull skipped"
else
    info "Cloning $REPO_URL → $INSTALL_DIR"
    $SUDO git clone --depth 1 "$REPO_URL" "$INSTALL_DIR" || die "git clone failed — check your network."
    $SUDO chown -R "$(whoami)" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# ── [3/6] virtualenv ─────────────────────────────────────────────────
echo -e "${B}  [3/6] Python environment${N}"
if [ "$MODE" = "node" ]; then
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip -q
    ./venv/bin/pip install -q -r node-agent/requirements.txt
    okmsg "Virtualenv ready (node-agent deps)"
else
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip -q
    ./venv/bin/pip install -q -r requirements.txt
    ./venv/bin/pip install -q -r dashboard/requirements.txt
    okmsg "Virtualenv ready (bot + dashboard deps)"
fi

# ── [4/6] firewall ports ─────────────────────────────────────────────
echo -e "${B}  [4/6] Firewall${N}"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    if [ "$MODE" = "node" ]; then
        $SUDO ufw allow 8080/tcp 2>/dev/null || true
        okmsg "Opened port 8080/tcp for the node agent"
    else
        $SUDO ufw allow 5000/tcp 2>/dev/null || true
        okmsg "Opened port 5000/tcp for the dashboard"
    fi
else
    info "ufw not active — nothing to open (open ports on your VPS provider panel too)."
fi

# ── [5/6] wizard ─────────────────────────────────────────────────────
echo -e "${B}  [5/6] Configuration wizard${N}"
if [ "$MODE" = "node" ]; then
    ./venv/bin/python link_node.py
else
    ./venv/bin/python setup.py
fi

# ── [6/6] done ───────────────────────────────────────────────────────
echo -e "${B}  [6/6] Done!${N}"
echo ""
if [ "$MODE" = "node" ]; then
    okmsg "Node install finished in: $INSTALL_DIR"
    info "Start manually:   cd $INSTALL_DIR && ./venv/bin/python node-agent/agent.py"
    info "Systemd service:  offered by link_node.py (run it again if skipped)"
else
    okmsg "Panel install finished in: $INSTALL_DIR"
    info "Start manually:   cd $INSTALL_DIR && ./venv/bin/python bot.py"
    info "Dashboard:        cd $INSTALL_DIR/dashboard && ../venv/bin/gunicorn -b 0.0.0.0:5000 app:app"
    info "Service install:  sudo ./venv/bin/python setup.py  →  choose option 2"
fi
echo ""
