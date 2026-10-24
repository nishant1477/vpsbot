# Arqon Host VPS Bot - API Documentation

## Node Agent API

Base URL: `http://<node-host>:8080/api/v1`

All endpoints require `Authorization: Bearer <NODE_AGENT_API_KEY>` header.

### Health Check
```
GET /health
```
Response:
```json
{"status": "healthy", "service": "arqon-node-agent"}
```

### System Statistics
```
GET /stats
```
Response:
```json
{
  "cpu_percent": 45.2,
  "memory_total": 17179869184,
  "memory_used": 8589934592,
  "memory_available": 8589934592,
  "disk_total": 107374182400,
  "disk_used": 53687091200,
  "disk_free": 53687091200,
  "network_rx": 1048576,
  "network_tx": 2097152,
  "load_avg": [1.5, 1.2, 1.0],
  "uptime": 1700000000,
  "vps_count": 5,
  "virtualization": ["docker", "lxc_lxd_supported", "lxc_lxd_unsupported"]
}
```

### VPS Management

#### List VPS
```
GET /vps
```
Response: `{"instances": [{"uuid": "...", "status": "running", "image": "...", "backend": "DockerManager"}]}`

#### Create VPS (queued in background — poll Get VPS until status != creating)
```
POST /vps/create
```
Body:
```json
{
  "uuid": "abc-123",
  "name": "my-vps",
  "hostname": "vps.example.com",
  "virtualization": "docker|lxd_supported|lxd_unsupported|kvm",
  "os_template": "ubuntu-22.04",
  "ram": 1024,
  "cpu": 1,
  "disk": 10,
  "ssh_port": 22,
  "root_password": "securepassword"
}
```
Response: `{"status": "creating", "uuid": "abc-123", "message": "queued"}`

#### Get VPS Status
```
GET /vps/{uuid}
```
Response: `{"status": "running", "ip_address": "10.0.0.5"}`

#### Delete VPS (also clears this VPS's firewall rules)
```
DELETE /vps/{uuid}
```
Response: `{"status": "deleted", "uuid": "abc-123"}`

#### Start/Stop/Restart VPS
```
POST /vps/{uuid}/start
POST /vps/{uuid}/stop
POST /vps/{uuid}/restart
```

#### Reinstall VPS (also clears firewall rules)
```
POST /vps/{uuid}/reinstall
```
Body: `{}` or `{"os_template": "debian-12"}`

#### Backup VPS (real filesystem backup on the node)
```
POST /vps/{uuid}/backup
```
Response: `{"status": "completed", "file_path": "/var/lib/arqon/backups/...", "file_size": 123456}`
Supported backends: LXC (rootfs tar.gz), Docker (container export), KVM (qcow2 convert), LXD (`lxc export`).

#### Restore VPS
```
POST /vps/{uuid}/restore
```
Body: `{"file_path": "/var/lib/arqon/backups/<uuid>/<file>"}` (must live under the node BACKUP_PATH)

#### Execute Command
```
POST /vps/execute
```
Body: `{"uuid": "abc-123", "command": "apt update"}`
Response: `{"output": "..."}`

#### Get VPS Stats
```
GET /vps/{uuid}/stats
```
Response:
```json
{
  "cpu_percent": 25.5,
  "memory_used": 536870912,
  "memory_limit": 1073741824,
  "running": true
}
```

### Firewall Management

#### Allow Rule
```
POST /firewall/allow
```
Body:
```json
{
  "uuid": "abc-123",
  "port": 80,
  "protocol": "tcp",
  "action": "allow",
  "source": "0.0.0.0/0"
}
```

#### Deny Rule
```
POST /firewall/deny
```
Body: (same as allow)

#### List Rules
```
GET /firewall/rules?uuid=abc-123
```
Response:
```json
{
  "rules": [
    {"port": 22, "protocol": "tcp", "action": "allow", "source": "0.0.0.0/0"},
    {"port": 80, "protocol": "tcp", "action": "allow", "source": "192.168.1.0/24"}
  ]
}
```

## Bot Internal API (Future Dashboard)

### Authentication
- JWT tokens via `/auth/login`
- API keys for service-to-service

### Endpoints (Planned)

#### VPS
- `GET /api/vps` - List VPS
- `POST /api/vps` - Create VPS
- `GET /api/vps/{id}` - Get VPS
- `DELETE /api/vps/{id}` - Delete VPS
- `POST /api/vps/{id}/action` - Start/stop/restart

#### Nodes
- `GET /api/nodes` - List nodes
- `GET /api/nodes/{id}` - Node details
- `GET /api/nodes/{id}/stats` - Real-time stats

#### Tickets
- `GET /api/tickets` - List tickets
- `POST /api/tickets` - Create ticket
- `GET /api/tickets/{id}` - Ticket details
- `POST /api/tickets/{id}/close` - Close ticket

#### Minecraft
- `GET /api/minecraft` - List servers
- `POST /api/minecraft` - Create server
- `GET /api/minecraft/{id}` - Server details

#### Monitoring
- `GET /api/monitoring/nodes` - Node health
- `GET /api/monitoring/alerts` - Active alerts
- `GET /api/monitoring/history` - Historical data

## WebSocket (Future)

Real-time updates for:
- Node status changes
- VPS state changes
- Ticket updates
- Monitoring alerts

Connection: `ws://<host>:<port>/ws?token=<jwt>`