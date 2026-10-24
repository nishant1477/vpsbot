# Arqon Host VPS Bot — Command Reference

All commands work as both prefix (`!`) and slash commands.

## Help Center

| Command | Description | Permission |
|---------|-------------|------------|
| `/help [category]` | **Interactive help menu** — category dropdown + quick buttons (Home / All Commands / Support) | Everyone |

Categories: Getting Started • VPS • Plans & Invites • Billing • Nodes • Security • Minecraft • Tickets • AI • Utility • Admin

## Plans & Invites (invite-earned hosting)

| Command | Description | Permission |
|---------|-------------|------------|
| `/plans` | **Interactive plan browser** — plan cards with [Claim] buttons → OS **dropdown** (no typing!) → animated deployment loader | Everyone |
| `/invites [@user]` | Invite count + progress bar toward the next plan unlock | Everyone |
| `/inviteleaderboard` | Top inviters of the server | Everyone |
| `/grantinvites <@user> <n> [reason]` | Grant/remove bonus invites (e.g. YouTube referrals) | Admin |
| `/resetinvites <@user>` | Wipe a member's invite stats | Admin |
| `/plan-add <name> <ram> <disk> <cpu> <invites>` | Create a custom plan | Admin |
| `/plan-remove <name>` | Delete a plan | Admin |
| `/plan-list` | All plans incl. inactive | Admin |
| `/plan-seed` | Load default invite tiers (Starter → Legend) | Admin |
| `/plan-edit <name>` | **Field-by-field template editor** — invites / RAM / cores / disk / price via modals | Admin |
| `/plan-toggle <name>` | Show / hide a plan in `/plans` | Admin |
| `/plan-export` | Download every plan as a JSON template | Admin |
| `/plan-import` | Upload a plan template JSON (creates + updates) | Admin |

**How the flow works:** `/plans` → pick a plan button → eligibility auto-checked from your invites
→ choose the OS from a select menu → confirm → live animated provisioning loader → credentials card.
Invite counts decrease when a member leaves, so "join & leave" farming doesn't pay.
Default tiers: **Starter** (1 invite → 1GB/8GB/1c) • **Basic** (2 → 2GB/10GB/1c) •
**Pro** (3 → 3GB/12GB/1c) • **Ultra** (5 → 4GB/20GB/2c) • **Legend** (8 → 8GB/40GB/4c).

## Custom Emojis

| Command | Description | Permission |
|---------|-------------|------------|
| `/emojilist` | Every emoji slot + current value | Everyone |
| `/editemoji <name> <emoji>` | Bind a custom Discord emoji to a slot (instant, no restart) | Admin |
| `/editemoji <name> reset` | Back to the unicode default | Admin |

Also configurable via `.env`: `EMOJI_SUCCESS=<:arqon_check:123…>`, `EMOJI_VPS=<a:arqon_server:123…>`, etc.
Overrides persist to `data/emojis.json`.

## General

| Command | Description | Permission |
|---------|-------------|------------|
| `!ping` | Check bot latency | Everyone |
| `!uuid` | Generate a UUID | Everyone |
| `!password [length]` | Generate a secure password | Everyone |
| `!apikey` | Generate an API key | Everyone |
| `!serverinfo` | Show server info | Everyone |
| `!whois [@user]` | Show Discord-level user info | Everyone |
| `!avatar [@user]` | Get user avatar | Everyone |
| `!calculate <ram> <cpu> <disk> [count]` | Calculate resources | VPS User |

## AI Assistant

| Command | Description | Permission |
|---------|-------------|------------|
| `!ai <question>` | Chat with the Arqon AI assistant | Everyone (cooldown) |
| `!ai-reset` | Clear your AI conversation history | Everyone |
| `!ai-status` | Show AI configuration status | Everyone |
| `!ai-toggle` | Enable/disable the AI assistant | Admin |

Configure via `.env`: `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` (any OpenAI-compatible provider).

## VPS Management

| Command | Description | Permission |
|---------|-------------|------------|
| `!create` | Interactive VPS creation wizard (auto node scheduling) | VPS User |
| `!myvps` | List your VPS instances | Everyone |
| `!vps <id>` | VPS details + action buttons | Owner / Staff |
| `!reinstall <id>` | Reinstall OS (modal confirm) | Owner / Staff |
| `!listvps [node_id]` | List all VPS | Staff |
| `!ostemplates` | List available OS templates | Everyone |

### VPS actions (buttons on `!vps`)
Start • Stop • Restart • Credentials (reveal root password) • Delete

## Nodes (Multi-Node Fleet)

| Command | Description | Permission |
|---------|-------------|------------|
| `!node-add` | Register a node (modal: name/host/port/location/api-key) | Admin |
| `!nodes` | List all nodes with live stats | Everyone |
| `!node <id>` | Node details + action buttons | Everyone |
| `!node-refresh` | Health-check + stats-sync every node | Staff |
| `!node-ping` | Latency table for all agents | Everyone |

### Node actions (buttons on `!node`)
Test Connection (with virtualization auto-detect) • Refresh Stats • Maintenance toggle • Delete

## Monitoring

| Command | Description | Permission |
|---------|-------------|------------|
| `!status` | Quick fleet status | Everyone |
| `!stats` | Detailed live node metrics | Everyone |
| `!monitor <channel>` | Set the alert channel | Admin |

## Tickets

| Command | Description | Permission |
|---------|-------------|------------|
| `!ticket [channel]` | Post the ticket panel | Admin |
| `!setup-tickets <category> <transcript>` | Configure category + transcripts | Admin |
| `!close` | Close current ticket (with transcript) | Creator / Staff |
| `!add-user <@user>` | Add user to ticket | Staff |
| `!remove-user <@user>` | Remove user from ticket | Staff |
| `!ticket-stats` | Ticket statistics | Everyone |

## Guardian (Security) — v2

| Command | Description | Permission |
|---------|-------------|------------|
| `!guardian` | Security dashboard + live toggle buttons (anti-raid / spam / mention / nuke / invite) | Staff |
| `!guardian-punish <strip\|kick\|ban>` | Choose the anti-nuke response mode (default: strip all roles) | Admin |
| `!guardian-age <days>` | Kick Discord accounts younger than N days on join (0 = off) | Admin |
| `!antiinvite <True/False>` | Block Discord invite links in chat | Admin |
| `!warn <@user> [reason]` | Warn a member — auto-timeout at 3, auto-kick at 5, auto-ban at 6 | Staff |
| `!warnings [@user]` | Show a member's warning record | Staff |
| `!resetwarns <@user>` | Clear all warnings for a member | Admin |
| `!mute <@user> <minutes> [reason]` | Timeout a member | Staff |
| `!unmute <@user>` | Remove a member's timeout | Staff |
| `!whitelist add/remove/list [@user]` | Manage whitelist | Admin |
| `!guardian-logs [limit]` | Recent security events | Staff |

**Guardian v2 protections (all automatic):** anti-raid (mass joins) • anti-spam with **escalating
ladder** (warn → 10m timeout → 1h timeout → kick → ban) • dupe-spam (same message 4x/60s) •
anti-mention • anti-invite link filter • anti-bot (unauthorised bots kicked) • account-age gate •
**anti-nuke** (mass bans/kicks/channel & role deletes/webhook wipes) with configurable response
**strip / kick / ban** + instant **owner DM alerts**.

## Security Suite v3 — 25 Advanced Protections

Guardian **enforces**; the Suite adds **intelligence, evidence and response**. Every event lands in
a tamper-evident hash chain (`/verifylogs`) and the incident timeline (`/incidents`).

| Command | Description | Permission |
|---------|-------------|------------|
| `/security` | **Control center** — 25 protections with live toggle buttons | Staff |
| `/security-test` | Self-test: bot permissions, DB, hash chain, coverage — scored /100 | Staff |
| `/incidents [n]` | Incident timeline (severity-coloured) | Staff |
| `/evidence <incident_id>` | Collected evidence bundles (message links, audit data) | Staff |
| `/verifylogs` | Verify the sha256 hash chain — proves no record was tampered | Staff |
| `/honeypots` | List armed decoy channels + hit counts | Staff |
| `/strikes <@user>` | Show a member's abuse strikes (auto-ban at 3) | Staff |
| `/temp-privs [@user]` | Active temporary privileges with countdowns | Staff |
| `/approvals` | Pending two-person decisions | Staff |
| `/panic <on\|off>` | **Maximum defence** — thresholds halved + lockdown + containment armed | Admin |
| `/safe-mode <on\|off>` | Monitor-only mode: detect everything, punish nothing | Admin |
| `/emergency [minutes]` | Instantly freeze every channel (auto-restore) | Admin |
| `/unlockall` | Lift the emergency lockdown | Admin |
| `/sec-snapshot` | Save a recovery baseline of roles + admins | Admin |
| `/sec-restore` | Restore the baseline (two-person approval) | Admin |
| `/honeypot <channel>` | Arm a decoy channel — posting there = instant ban | Admin |
| `/threatshare export\|import\|channel\|status` | Cross-server threat intel packs | Admin |
| `/strikes-clear <@user>` | Clear a member's strikes | Admin |
| `/granttemp <@user> <security\|moderation\|admin\|restore> <30m\|2h\|3d>` | Grant an expiring permission | Admin |
| `/revoketemp <id>` | Revoke a temporary privilege early | Admin |

**Automatic protections (no commands needed):** smart anti-nuke intel · admin abuse detector ·
permission escalation blocker (auto-revert) · anti-webhook attack · role hierarchy guard ·
**adaptive** anti-raid with auto-lockdown · anti-bot raid · alt risk scoring · anti-impersonation ·
scam intelligence (auto-delete + shared domain blocklist) · threat containment · smart recovery ·
permission drift watch · anti-evasion auto-ban · evidence collection.

## Bot Settings Console

| Command | Description | Permission |
|---------|-------------|------------|
| `/settings` | **Interactive console** — 23 protection toggles, 16 thresholds, log channels, VPS defaults. Everything saves instantly, no restart | Admin |

Sections: 🛡️ Security toggles (Guardian + Suite) • 🎚️ Thresholds (raid, spam, nuke, alt, warns…) •
📋 Logs & Channels (security log, tickets) • 🖥️ VPS & Ticket defaults (RAM/cores/disk, cooldowns,
anti-nuke response mode).

## Firewall

| Command | Description | Permission |
|---------|-------------|------------|
| `!fw-allow <vps> <port> [tcp/udp] [cidr]` | Allow a port | VPS User |
| `!fw-deny <vps> <port> [tcp/udp] [cidr]` | Deny a port | VPS User |
| `!fw-remove <vps> <port>` | Remove a rule | VPS User |
| `!fw-list <vps>` | List rules | VPS User |

## Ports / Backups / Billing

| Command | Description | Permission |
|---------|-------------|------------|
| `!port-add <node> <port> [proto]` | Add port allocation | Admin |
| `!port-list [node]` | List allocations | Staff |
| `!port-free <node>` | Find free ports | Staff |
| `!backup <vps_id>` | Manual backup | Owner / Staff |
| `!backups <vps_id>` | List backups | Owner / Staff |
| `!restore <backup_id>` | Restore a backup | Staff |
| `!vps-backup-toggle <vps_id>` | Toggle auto-backups | Owner / Staff |
| `!balance` | Wallet balance | Everyone |
| `!deposit` | Add funds (demo) | Everyone |
| `!transactions` | Recent transactions | Everyone |
| `!createinvoice <@user> <amount> [desc]` | Create invoice | Admin |

> `/plans` moved to the **Plans & Invites** section above — it's now fully interactive.

## Admin

| Command | Description | Permission |
|---------|-------------|------------|
| `!addadmin <@user> [admin\|staff] [reason]` | **Give ANY user bot admin** — no Discord roles needed, works in every server, survives restarts | Owner |
| `!removeadmin <@user>` | Revoke a user's bot admin | Owner |
| `!admins` | List the full admin team (owners + admins + staff) | Admin |
| `!setup` | Setup checklist | Owner |
| `!config` | View guild configuration | Admin |
| `!set-role <type> @role` | Bind role (admin/staff/customer/vps/premium) | Admin |
| `!announce <channel> <title> <message>` | Announcement | Admin |
| `!lockdown <True/False>` | Server lockdown | Admin |
| `!reload [ext]` | Reload extensions | Owner |
| `!sync [guild_only]` | Sync slash commands | Owner |
| `!sysstats` | Global system statistics | Admin |
| `!logs <type> [lines]` | View log files | Admin |
| `!audit-logs [limit]` | View audit trail | Admin |

### Multi-Admin System

The bot supports **unlimited admins** via two complementary systems:

1. **Bot admins** (`!addadmin`) — global, DB-backed (`bot_admins` table). Perfect for giving
   multiple trusted users full admin access without touching Discord roles. Two levels:
   `admin` (full admin commands) and `staff` (moderation commands like warn/mute).
2. **Role bindings** (`!set-role admin @role`) — per-server, Discord-role based.

Permission priority: **Owner → Bot admin → Guild owner → Role-bound admin/staff → …**
