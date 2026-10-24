# Arqon Host VPS Bot — Installation Guide

## Requirements

### Panel machine (runs the Discord bot + dashboard)
- Ubuntu 22.04+ / Debian 12+ recommended (Windows supported for the bot core)
- Python 3.10+ (3.12 ideal)
- 1GB+ RAM
- A Discord bot token — [Developer Portal](https://discord.com/developers/applications)

### Node machines (host the customer VPS containers)
- Ubuntu 22.04+ / Debian 12+
- Root access
- At least ONE of: Docker / LXC / LXD / KVM (the wizard detects and can install them)
- Open inbound port `8080/tcp` (or your chosen agent port) so the panel can reach the node

---

## Option A — One-click install (recommended)

### Linux — panel (bot + dashboard)

```bash
bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh)
```

### Linux — node (VPS host machine)

```bash
bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh) --node
```

### Windows

Double-click **`install.cmd`** inside the downloaded repo — it checks git + Python,
installs dependencies and launches the setup wizard. Use **`start-bot.cmd`** to run the bot.

---

## Option B — Setup wizard only (existing checkout)

```bash
git clone https://github.com/nishant1477/vpsbot.git
cd vpsbot
python3 -m venv venv
venv/bin/pip install -r requirements.txt -r dashboard/requirements.txt
venv/bin/python setup.py
venv/bin/python bot.py
```

The wizard asks for your Discord token, owner IDs, **custom bot name**, dashboard
login, and **optionally** an AI API key (press Enter to skip — you can add it later
in `.env`). It writes `.env`, initializes the database, seeds the dashboard admin
and offers systemd installation + node linking.

---

## Option C — Fully manual

1. `cp .env.example .env` and edit every value
2. Generate secrets:
   ```bash
   # ENCRYPTION_KEY
   python3 -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
   # JWT_SECRET / node secrets
   python3 -c "import secrets;print(secrets.token_hex(32))"
   ```
3. Init the database: `python3 -c "import asyncio, database; asyncio.run(database.init_db())"`
4. Start: `python3 bot.py` — dashboard: `cd dashboard && gunicorn -b 0.0.0.0:5000 app:app`

---

## Multi-node setup

1. **Panel:** run `python setup.py` — it prints the **node API key** (also in `.env` as `NODE_AGENT_API_KEY`)
2. **Each node machine:** run
   ```bash
   bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh) --node
   ```
   or `python link_node.py` from a checkout. Paste the node API key when asked.
3. **Register** the node on the panel:
   - Dashboard → **Nodes → Add Node** (name, node IP, port, key), or
   - Discord: type `!node-add` and fill in the modal
     (Node Name, Host = node IP, Agent Port = 8080, API Key from `link_node.py`)
4. Verify with `!nodes` (Discord) or the dashboard **Nodes** page.

---

## systemd services (Linux)

The wizard (option 2) installs these automatically. Manual alternative:

```bash
sudo cp services/arqon-bot.service services/arqon-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now arqon-bot arqon-dashboard
```

Node machines use `services/arqon-node-agent.service` (installed by `link_node.py`).

## Logs & maintenance

| Task | Command |
|---|---|
| Bot logs | `journalctl -u arqon-bot -f` |
| Dashboard logs | `journalctl -u arqon-dashboard -f` |
| Node agent logs | `journalctl -u arqon-node-agent -f` |
| Update everything | `bash update.sh` |
| Clean removal | `bash uninstall.sh` |

## Troubleshooting

- **Bot offline / token error** → re-check `DISCORD_TOKEN`, ensure Privileged Gateway Intents are all enabled in the Developer Portal
- **Dashboard shows "Database not found"** → start the bot once (it creates `database/arqon.db`), or run `python setup.py --init-db`
- **Node shows offline** → `curl -H "X-API-Key: <key>" http://<node-ip>:8080/health` from the panel machine; check the firewall allows the port
- **AI features disabled** → that's the optional AI key; add `AI_API_KEY=...` to `.env` and restart
