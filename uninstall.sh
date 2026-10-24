#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  Arqon Host VPS Bot — Uninstaller
#   Removes services, files and the service user. Does NOT touch
#   any customer VPS containers running on node machines.
# ═══════════════════════════════════════════════════════════════════
set -e

echo ""
echo "  ═══════════════════════════════════════════════"
echo "   Arqon Host — Uninstaller"
echo "  ═══════════════════════════════════════════════"
echo ""
echo "  WARNING: this removes the bot, dashboard, node agent"
echo "  and all their data (database, logs, backups)."
echo ""
read -p "  Type 'uninstall' to confirm: " confirm
[ "$confirm" = "uninstall" ] || { echo "  Aborted."; exit 1; }

echo "  [1/4] Stopping + disabling services ..."
systemctl stop arqon-bot arqon-dashboard arqon-node-agent 2>/dev/null || true
systemctl disable arqon-bot arqon-dashboard arqon-node-agent 2>/dev/null || true
rm -f /etc/systemd/system/arqon-bot.service \
      /etc/systemd/system/arqon-dashboard.service \
      /etc/systemd/system/arqon-node-agent.service
systemctl daemon-reload 2>/dev/null || true

echo "  [2/4] Removing install directories ..."
rm -rf /opt/arqon-host /opt/arqon-node

echo "  [3/4] Removing data directories ..."
rm -rf /var/lib/arqon /etc/arqon

echo "  [4/4] Removing service user ..."
userdel -r arqon 2>/dev/null || true

echo ""
echo "  Arqon Host fully removed."
echo ""
