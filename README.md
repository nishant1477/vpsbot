# Arqon Host VPS Bot

**Arqon Host VPS Bot** is a production-ready Discord bot + web dashboard for running a
VPS / game-server hosting business: multi-node VPS deployment, **invite-earned free plans**,
Minecraft servers via Pterodactyl, ticket system, guardian anti-raid security, billing,
backups, firewall management, a built-in AI assistant and a fully interactive help center —
all behind a modern Aurora-glass web panel.

Bot name is **fully rebrandable** — one answer during setup renames everything
(embeds, dashboard, Discord status, AI persona) to your own brand.
Every message emoji is **custom-emoji ready** — bind your own Discord emojis with
`/editemoji`, no restart needed.

---

## ⚡ One-click install

### Linux (VPS / dedicated server)

**Install the PANEL (bot + dashboard):**

```bash
bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh)
```

**Install a NODE** (a second machine that hosts the actual VPS containers):

```bash
bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh) --node
```

### Windows

1. Download the repo (green **Code** button → *Download ZIP*, or `git clone https://github.com/nishant1477/vpsbot.git`)
2. Double-click **`install.cmd`** — it checks git + Python, installs everything and launches the setup wizard
3. Start the bot anytime with **`start-bot.cmd`**

---

## 🧙 Setup wizard

Run `python setup.py` (the one-click installers launch it automatically). It asks:

| Question | Required? | Notes |
|---|---|---|
| Discord bot token | ✅ yes | From [discord.com/developers](https://discord.com/developers) → Bot → Reset Token. Enable all Privileged Gateway Intents! |
| Application / Client ID | optional | Used to print your personal invite link |
| **Bot name** | ✅ default `Arqon Host VPS Bot` | **Rebrand the entire bot** — embeds, dashboard, AI persona |
| Command prefix / activity text | optional | Defaults `!` and `"<name> | !help"` |
| Owner Discord user ID(s) | ✅ yes | Right-click yourself → Copy User ID (Developer Mode on) |
| Dashboard settings | optional | Admin login auto-generated + shown once |
| Node agent port / API key | auto-generated | Copy the printed key onto every node machine |
| **AI API key** | ⬜ **OPTIONAL** | **Press Enter to skip** — bot works 100% without it. Add later in `.env` (`AI_API_KEY=...`) or rerun the wizard |
| Pterodactyl panel | optional | Only needed for Minecraft module |

The wizard then writes `.env`, initializes the database, seeds your dashboard admin
account and offers to install systemd services / link a node.

> 🔁 Already ran it before? No problem — `setup.py` backs up your old `.env` before writing.

---

## 🖥️ Linking a node (multi-node cluster)

A **cluster** = 1 panel machine (this bot) + N node machines (VPS hosts).

On each **node machine**:

```bash
# with the one-click installer:
bash <(curl -sL https://raw.githubusercontent.com/nishant1477/vpsbot/main/install.sh) --node

# or from an existing checkout:
python link_node.py
```

`link_node.py` asks for the node name + the node API key printed by `setup.py`,
detects which virtualization backends the machine supports (Docker / LXC / LXD / KVM),
can install missing packages, writes `node-agent/.env`, boots the agent for a
self-test and installs it as a systemd service.

Then register the node on the panel — dashboard **Nodes → Add Node**, or in Discord
type `!node-add` and fill in the modal (name, node IP, port, and the node API key
shown by `link_node.py`). Leave the key blank to let the panel generate one instead
(then copy the panel-generated key back onto the node's `node-agent/.env`).

That's it — new VPS orders are automatically scheduled to the least-loaded node.

---

## ✨ Features

- **Multi-admin system** 👥 — grant **unlimited bot admins** with `!addadmin @user` (levels: admin / staff), DB-backed, works in **every server**, survives restarts — no Discord roles needed
- **Guardian v2 security suite** 🛡️ — the enforcement layer:
  * **Anti-nuke** — mass bans / kicks / channel & role deletes / webhook wipes, with a configurable response (**strip roles / kick / ban**) and instant **owner DM alerts**
  * **Anti-spam with escalating ladder** — warn → 10m timeout → 1h timeout → kick → ban
  * **Dupe-spam detection** — same message 4x in 60s = 5m timeout
  * **Anti-raid** — mass-join detection and alerts
  * **Anti-mention** — mass ping protection
  * **Anti-invite** — Discord invite-link filter (toggle per server)
  * **Anti-bot** — unauthorised bots added by non-admins are auto-kicked
  * **Account-age gate** — kick brand-new accounts (`!guardian-age 3`)
  * **Warn system** — `!warn` / `!warnings` / `!resetwarns` with auto-punish at 3 / 5 / 6 warns
- **Security Suite v3 — 25 advanced protections** 🏰 — the intelligence + response layer on top of Guardian:

  | # | Protection | What it does |
  |---|------------|--------------|
  | 1 | Smart Anti-Nuke | Incidents + evidence on every mass ban/kick/channel/role/webhook attack |
  | 2 | Admin Abuse Detector | Rate-limits **all** audited destructive actions — staff included |
  | 3 | Permission Escalation Blocker | Auto-reverts dangerous role grants by unauthorised users |
  | 4 | Anti-Webhook Attack | Webhook creation bursts + webhook messages pinging/scamming |
  | 5 | Role Hierarchy Guard | Reverts edits to admin-permission roles |
  | 6 | Adaptive Anti-Raid | Burst joins → raid mode + auto-lockdown, thresholds tighten 40% for 24h |
  | 7 | Anti-Bot Raid | Mass bot additions detected and alerted |
  | 8 | Anti-Alt Detection | Account risk scoring (age, avatar, name, shared threat hashes) |
  | 9 | Anti-Impersonation | Staff-name similarity shield on join and nickname change |
  | 10 | Anti-Scam Intelligence | Scam patterns, IP loggers, known scam domains — auto-delete + timeout |
  | 11 | Emergency Lockdown | `/emergency` freezes every channel, auto-restore after N minutes |
  | 12 | Automatic Threat Containment | Verified breaches earn auto timeouts |
  | 13 | Smart Recovery | `/sec-snapshot` baseline + auto-recreation of deleted guarded roles |
  | 14 | Anti-Permission Drift | Periodic admin-list comparison against the approved baseline |
  | 15 | Panic Command | `/panic on` — thresholds halved, lockdown, containment armed |
  | 16 | Honeypot Detection | Decoy channels: post there = instant ban |
  | 17 | Staff Action Approval | Two-person rule for restores & threat imports |
  | 18 | Temporary Privileges | `/granttemp` — permissions that expire automatically |
  | 19 | Incident Timeline | `/incidents` — chronological, severity-coloured feed |
  | 20 | Tamper-Evident Logs | sha256 hash chain — `/verifylogs` proves records are intact |
  | 21 | Cross-Server Threat Sharing | Export / import / sync privacy-preserving indicator packs |
  | 22 | Security Self-Test | `/security-test` — permissions, chain, coverage report card |
  | 23 | Anti-Evasion | Repeat offenders auto-banned on rejoin (strike-based) |
  | 24 | Evidence Collection | Message links, content & audit data saved per incident |
  | 25 | Safe Mode | `/safe-mode on` — monitor everything, punish nothing |
- **Interactive settings console** ⚙️ — `/settings`: 23 protection toggles, 16 thresholds, log channels and VPS defaults edited **live from Discord** — no `.env`, no restart
- **Invite-earned VPS plans** 🚀 — `/plans` shows plan cards with Claim buttons, members pay with invites (e.g. *3 invites = 3GB RAM / 12GB SSD / 1 core*), invite leaderboard + progress bars, leave-penalty anti-farm
- **Plan template editor** 📝 — `/plan-edit` (field-by-field modals), `/plan-toggle`, `/plan-export` / `/plan-import` (JSON templates)
- **Zero-typing OS picker** — the plan flow uses a proper **select menu** for the operating system (no text boxes), then an **animated provisioning loader** with live progress bar until the credentials card drops
- **Custom emoji system** — 60+ emoji slots, bind your own Discord emojis via `/editemoji` or `EMOJI_*` env vars, applied instantly across every embed
- **Interactive help center** — `/help` opens a hero panel with a category dropdown (11 categories), pro tips and quick-action buttons
- **Multi-node VPS hosting** — Docker / LXC / LXD / KVM backends, least-loaded scheduling, live heartbeats
- **New Aurora-glass dashboard** — real login (PBKDF2), skeleton loaders, toast notifications, animated resource bars, live monitoring
- **AI assistant** *(optional)* — any OpenAI-compatible provider (OpenAI / Groq / Z.ai / Ollama), per-user history, cooldowns, admin toggle, playful writing style with a live "thinking" card
- **VPS lifecycle** — create / start / stop / restart / reinstall / delete with live provisioning status
- **Firewall management** — per-VPS iptables rules, validated CIDR/port/protocol
- **Backups** — real agent-side backup / restore with retention pruning
- **Minecraft** — Pterodactyl integration (Paper, Purpur, Fabric, Forge, …)
- **Billing** — wallets, invoices, packages
- **Tickets** — transcripts, categories, staff claiming
- **Monitoring** — CPU/RAM/disk alerts to Discord
- **23 command modules** — see `docs/COMMANDS.md`

---

## 📁 Repository layout

```
vpsbot/
├── bot.py               # Discord bot entry point (23 cogs, rotating presence)
├── setup.py             # ★ Interactive setup wizard (AI key optional, bot name editable)
├── link_node.py         # ★ Node-link wizard (run on VPS-host machines)
├── install.sh           # ★ Linux one-click installer
├── install.cmd          # ★ Windows one-click installer
├── start-bot.cmd        #   Windows launcher
├── update.sh            #   Updater (git pull + deps + migrate + restart)
├── uninstall.sh         #   Clean removal
├── config.py            # Pydantic settings (.env driven)
├── database.py          # Unified SQLAlchemy schema + auto-migrations
├── requirements.txt
├── cogs/                # Discord command modules
│   ├── security_suite.py #  ★ Security Suite v3 (25 protections)
│   ├── settings_ui.py   #   ★ interactive /settings console
│   ├── plans.py         #   ★ interactive plan store + template editor
│   ├── invites.py       #   ★ invite tracking engine
│   ├── guardian.py      #   ★ Guardian v2 enforcement layer
│   ├── help.py          #   ★ interactive help center
│   └── emojis.py        #   ★ custom emoji management
├── utils/
│   ├── security_core.py #   ★ hash-chain, incidents, approvals, indicators
│   └── …                #   node_client, validators, emoji registry, embeds
├── scripts/
│   └── test_security_suite.py # ★ validation suite (44 checks)
├── dashboard/           # Flask web panel (Aurora Glass UI)
├── node-agent/          # FastAPI agent that hosts the VPS containers
├── services/            # systemd unit files
└── docs/                # INSTALL / COMMANDS / API
```

## 🔧 Manual install (no scripts)

```bash
git clone https://github.com/nishant1477/vpsbot.git
cd vpsbot
python3 -m venv venv
venv/bin/pip install -r requirements.txt -r dashboard/requirements.txt
venv/bin/python setup.py      # interactive wizard
venv/bin/python bot.py        # start
```

## 🔐 Security notes

- `.env` is written with `0600` permissions and is **never** committed (gitignored)
- Encryption key, JWT secret and node secrets are auto-generated per install
- Change the dashboard admin password after first login (Settings → Password)
- Keep node API keys private — they grant container control on your nodes

## 🛡️ Security hardening v3.3.0 (red-team audited)

The full codebase was audited and attack-tested with an offline red-team
harness (`scripts/pentest_redteam.py` — **34/34 attacks repelled**,
report in `docs/PENTEST-REPORT.md`):

- **Node-agent RCE closed** — strict `_safe_uuid` on every route, XML-escaped
  libvirt domain generation, argv-only LXC restore (no `sh -c`), firewall
  delete validation, no plaintext root passwords retained in agent memory
- **Economy race-proof** — invite cost reserved atomically before deployment
  (double-spend impossible) with automatic refunds; wallet deposits use
  atomic increments; hostile plan templates (negative invites, type bombs,
  plan floods) are rejected field-by-field
- **Lockdown integrity** — claim-first reservation (concurrent lockdowns can
  no longer corrupt saved permissions) and a **working auto-unlock timer**
- **Privilege model tightened** — foreign guild owners are no longer
  auto-admins (opt back in with `GUILD_OWNER_IS_ADMIN=true`); banned accounts
  are blocked from every command by a global gate; node infrastructure
  details are staff-only; backups enforce tenant isolation
- **Tamper-evident chain hardened** — lock-protected appends (fork-proof
  under concurrency), capped threat-pack imports (500 items), privacy-safe
  exports (hashed IDs only)
- **Settings hardening** — attribute whitelists + sane minimum bounds on all
  thresholds (no more `-5` threshold self-sabotage)
- **Secrets hygiene** — no default dashboard password (random first-run
  password is generated and logged), login rate limiting, loud startup
  warnings on default encryption/JWT keys, `textContent` toast rendering
- **AI prompt-injection firewall** — user text wrapped as untrusted data,
  hardened system prompt, response size caps

## 📜 License

MIT
