# Docker Deployment Guide — YTM Sync

This guide provides instructions for deploying **YTM Sync** as a lightweight, single-container production service.

---

## Architecture Overview

```text
Docker Host
├── .env (Configurable ports & mount paths)
│
└── YTM Sync Container (Non-root user `ytmsync`)
    ├── Web UI (Compiled Flutter Web client served at /)
    ├── REST API (/api/* & /health)
    ├── Sync & Comparison Engine
    ├── YT Music Integration
    │
    ├── /config (Read/Write Persistent Volume)
    │   ├── database/  ──► SQLite DB (ytm_sync.db)
    │   ├── auth/      ──► YouTube Music headers (headers_auth.json, 0600 permissions)
    │   ├── logs/      ──► Rotating log files (ytm_sync.log)
    │   └── backups/   ──► Point-in-time database backups
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
Edit `.env` to set your host music directory paths and preferred port:
```env
PORT=8080
CONFIG_PATH=./config
MUSIC_PATH=/mnt/music
DOWNLOADS_PATH=/mnt/downloads
```

### 2. Start the Service
```bash
docker compose up -d
```

### 3. Verify Health & Status
```bash
docker compose ps
docker compose logs -f ytm-sync
```

Access the Web UI at:
```text
http://YOUR_SERVER_IP:8080
```

---

## Persistent Storage (`/config`)

All state is preserved across container recreations, image updates, and restarts under `/config`:
- **Database**: `/config/database/ytm_sync.db`
- **Auth Credentials**: `/config/auth/headers_auth.json` (secured with `0600` POSIX permissions)
- **Rotating Logs**: `/config/logs/ytm_sync.log`
- **Database Snapshots**: `/config/backups/ytm_sync_backup_<TIMESTAMP>.db`

### Backup Procedure
To backup your entire application state, simply archive the host `CONFIG_PATH` directory:
```bash
tar -czvf ytm_sync_backup_$(date +%F).tar.gz ./config
```

---

## Security & Permissions

- **Non-Root Execution**: Runs as unprivileged user `ytmsync` (`UID=1000, GID=1000`).
- **Read-Only Music Mounts**: Music directories are mounted with `:ro` flags. The container cannot modify or delete audio files on your host.
- **No Docker Socket**: Does not require `/var/run/docker.sock`.
- **Credential Protection**: Auth files are never included in Docker images or committed to Git.

---

## Traefik Reverse Proxy (Optional)

To route through an existing Traefik reverse proxy, uncomment the Traefik labels in `docker-compose.yml` and set `TRAEFIK_HOST` in `.env`:
```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.ytmsync.rule=Host(`ytmsync.example.com`)"
  - "traefik.http.routers.ytmsync.entrypoints=websecure"
  - "traefik.http.routers.ytmsync.tls.certresolver=letsencrypt"
  - "traefik.http.services.ytmsync.loadbalancer.server.port=8080"
```

---

## Backup & Disaster Recovery Guide

### What Must Be Backed Up
Only the host directory mapped to `/config` (default `./config`) needs to be backed up.

It contains:
- **`database/ytm_sync.db`**: Local tracks, YouTube Music entity IDs, match relationships, upload queue, and full sync history.
- **`auth/headers_auth.json`**: YouTube Music session authentication.
- **`backups/`**: Automatic SQLite database snapshots.
- **`logs/ytm_sync.log`**: Historical application logs.

> [!NOTE]
> The music files themselves are **NOT** part of the container backup because they already live on your host or NAS and are mounted strictly read-only.

### Creating a Backup
```bash
# Safely snapshot the config directory
tar -czvf ytm_sync_backup_$(date +%Y%m%d_%H%M%S).tar.gz ./config
```

### Restoring from Backup
```bash
# 1. Stop the container
docker compose down

# 2. Extract the archive into place
tar -xzvf ytm_sync_backup_YYYYMMDD_HHMMSS.tar.gz

# 3. Start the container
docker compose up -d
```
All library scans, matching rules, credentials, and upload histories will be completely restored.
