# Docker Deployment Guide — YTM Sync

This guide covers deploying **YTM Sync** as a production container service.

---

## Architecture Overview

```text
Docker Host
├── .env (Configuration, ports, and storage mounts)
├── ytsync.yml (Compose specification)
│
└── YTM Sync Container (Unprivileged user `ytmsync`, UID 1000)
    ├── Web UI (Compiled Flutter Web client served at /)
    ├── REST API (/api/* & /health)
    ├── Sliding Window Rate Limiter
    ├── Playlist Watcher & Reconciliation Engine
    │
    ├── /config (Persistent Volume Mounted Read/Write)
    │   ├── database/  ──► SQLite DB (ytm_sync.db)
    │   ├── users/     ──► Per-user AES-GCM encrypted headers and settings
    │   ├── backups/   ──► Point-in-time database snapshots
    │   └── logs/      ──► Sanitized rotating log files
    │
    ├── /music         ──► Host Music Directory (Read-Only :ro)
    └── /downloads     ──► Host Downloads Directory (Read-Only :ro)
```

---

## Quick Start

### 1. Configure Environment
```bash
cp .env.example .env
```
Set your host storage paths and port:
```env
PORT=6969
CONFIG_PATH=/mnt/config
MUSIC_PATH=/mnt/music
DOWNLOADS_PATH=/mnt/downloads
TZ=America/Denver
LOG_LEVEL=INFO
```

### 2. Start the Service
```bash
docker compose -f ytsync.yml --env-file .env up -d
```

### 3. Verify Health & Status
```bash
docker compose -f ytsync.yml ps
docker compose -f ytsync.yml logs -f ytm-sync
```

Access the Web UI at:
```text
http://<YOUR_SERVER_IP>:6969
```

---

## Persistent Storage & File Layout

All state is preserved under the mounted `/config` directory:
- **Database**: `/config/database/ytm_sync.db` — Multi-tenant SQLite database with Foreign Key cascades.
- **User Configurations**: `/config/users/<user_id>/auth/headers_auth.json` — Per-user AES-GCM encrypted session headers.
- **Snapshots**: `/config/backups/ytm_sync_backup_<TIMESTAMP>.db`
- **Logs**: `/config/logs/ytm_sync.log`

### Backup Procedure
To backup your application state, snapshot the host `CONFIG_PATH`:
```bash
tar -czvf ytm_sync_backup_$(date +%F).tar.gz /mnt/config
```

### Restore Procedure
```bash
docker compose -f ytsync.yml down
tar -xzvf ytm_sync_backup_YYYY-MM-DD.tar.gz -C /mnt/config/
docker compose -f ytsync.yml --env-file .env up -d
```

---

## Security Invariants

- **Non-Root Execution**: Runs as unprivileged user `ytmsync` (`UID=1000, GID=1000`).
- **Read-Only Music Mounts**: Music directories are mounted with `:ro` flags. The container cannot modify or delete audio files on your host.
- **Capability Dropping**: Drops all Linux capabilities (`cap_drop: [ALL]`) with `no-new-privileges: true`.
- **Credential Protection**: Per-user YouTube Music session headers are encrypted at rest with AES-GCM.
- **Rate Limiting**: Integrated sliding-window rate limiter prevents brute-force attempts.
