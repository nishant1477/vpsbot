#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Arqon Host VPS Bot — Updater
#   Pulls the latest code, refreshes dependencies, restarts services.
# ═══════════════════════════════════════════════════════════════════
set -e

INSTALL_DIR="/opt/arqon-host"
[ -d "$INSTALL_DIR" ] || INSTALL_DIR="$(pwd)"

echo ""
echo "  ═══════════════════════════════════════════════"
echo "   Arqon Host — Updater"
echo "  ═══════════════════════════════════════════════"
echo ""

cd "$INSTALL_DIR"

if [ -d .git ]; then
    echo "  [1/4] Pulling latest source ..."
    git pull --ff-only origin main || echo "  (git pull skipped)"
else
    echo "  [1/4] Not a git repo — replace files manually with the new release."
fi

echo "  [2/4] Refreshing dependencies ..."
./venv/bin/pip install -q -r requirements.txt
[ -f dashboard/requirements.txt ] && ./venv/bin/pip install -q -r dashboard/requirements.txt

echo "  [3/4] Applying database migrations ..."
./venv/bin/python -c "import asyncio, database; asyncio.run(database.init_db())"

echo "  [4/4] Restarting services ..."
systemctl restart arqon-bot arqon-dashboard 2>/dev/null || \
    echo "  (systemd services not installed — start manually: ./venv/bin/python bot.py)"

echo ""
echo "  Update complete!"
echo ""
