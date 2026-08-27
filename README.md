# YTM Sync — YouTube Music Collection Synchronizer

**YTM Sync** is a local-first YouTube Music Upload Sync application. It keeps your local music collection synchronized with your private YouTube Music Uploads without modifying or deleting local files.

---

## 1. What YTM Sync Does

- **Recursive Music Library Scanner**: Scans your local folders (`.mp3`, `.flac`, `.m4a`, `.ogg`, `.wma`) and extracts embedded metadata with SHA-256 fingerprints.
- **Smart Metadata Normalization**: Strips remaster tags (`[Remastered]`, `(Deluxe Edition)`, `feat.`), formats track numbers, and tolerates duration deltas.
- **Deduplication & Matching Engine**: Compares local files against your cloud YouTube Music library using a tiered confidence model (Exact, Strong, Weak, Missing) so you never re-upload tracks.
- **Sequential Upload Queue with Recovery**: Uploads tracks one-by-one with exponential backoff retries. If the container restarts or network drops, it resumes where it left off without duplicating uploads.
- **Unified Web & Desktop UI**: Dark mode dashboard with Library view, Queue inspector, Sync History, and Guided Auth wizard.

---

## 2. Requirements

- **For Docker Deployment (Recommended)**:
  - Docker Engine 24.0+ and Docker Compose v2.0+
  - Host directory containing your music library (mounted strictly read-only)
- **For Native Desktop Execution**:
  - Python 3.12+
  - Flutter SDK 3.19+ (with desktop linux/macos/windows toolchains)

---

## 3. Docker Installation

```bash
# 1. Clone the repository
git clone https://github.com/example/ytmusic_sync.git
cd ytmusic_sync

# 2. Create your environment configuration
cp .env.example .env

# 3. Launch with Docker Compose
docker compose up -d
```

### Accessing the Application

Open your browser and navigate to:
```text
http://<YOUR_SERVER_IP>:8080
```
*(or `http://localhost:8080` if running locally)*

---

## 4. Configuration

All configuration is managed via environment variables in `.env` or passed to `docker compose`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | Host port where YTM Sync is exposed |
| `CONFIG_PATH` | `./config` | Host path storing database, logs, and auth credentials |
| `MUSIC_PATH` | `/mnt/music` | Host directory containing your primary music files |
| `DOWNLOADS_PATH` | `/mnt/downloads` | Host directory containing your secondary/downloaded music |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TZ` | `UTC` | Server timezone for timestamped logs and backups |

---

## 5. Music Mounts (Read-Only Safety)

YTM Sync **never modifies, tags, renames, or deletes your local files**.

In `docker-compose.yml`, all music volumes are kernel-enforced as read-only (`:ro`):

```yaml
volumes:
  - ./config:/config
  - /media/Music:/music:ro
  - /media/Downloads:/downloads:ro
```

Inside the Web UI under **Settings** $\rightarrow$ **Music Folders**, you can add `/music`, `/downloads`, or any subfolder.

---

## 6. YouTube Music Authentication Setup

Because Google OAuth credentials are not authorized by Google for personal music uploads, authentication is established using browser session cookies/headers:

1. Open [music.youtube.com](https://music.youtube.com) in Chrome/Firefox/Brave and sign in.
2. Press **F12** to open Developer Tools, then click the **Network** tab.
3. Click any request to `music.youtube.com` (such as `browse` or `player`).
4. Under **Request Headers**, copy all headers (or right-click $\rightarrow$ *Copy as cURL*).
5. In **YTM Sync** $\rightarrow$ **Settings**, paste into the headers box and click **Connect YouTube Music**.
6. Credentials are saved locally with restricted permissions (`0600`) at `/config/auth/headers_auth.json`.

---

## 7. Starting

```bash
docker compose up -d
```

To verify the container is running and healthy:
```bash
docker compose ps
curl -s http://localhost:8080/health
```

---

## 8. Stopping

```bash
docker compose down
```

YTM Sync catches `SIGTERM`, safely completes any active in-flight request, flushes all SQLite database transactions to disk, and exits cleanly.

---

## 9. Updating

To upgrade to the latest version while preserving all database records, authentication credentials, and sync history:

```bash
git pull
docker compose build --no-cache
docker compose up -d
```

---

## 10. Backup

Only the host `/config` directory needs to be backed up. The music directory is already mounted from your host system.

### Create a Quick Backup Archive
```bash
tar -czvf ytm_sync_backup_$(date +%Y%m%d).tar.gz ./config
```

### What is Preserved in `/config`:
- `/config/database/ytm_sync.db` — Track metadata, cloud matches, sync queue & history
- `/config/auth/headers_auth.json` — YouTube Music authentication
- `/config/backups/` — Periodic SQLite database snapshots

---

## 11. Troubleshooting

### Container Shows `(unhealthy)`
Check logs with:
```bash
docker compose logs -f ytm-sync
```
Confirm `curl` can reach the internal endpoint:
```bash
docker compose exec ytm-sync curl -f http://localhost:8080/health
```

### YouTube Music Shows "NOT CONNECTED"
- Your browser session may have expired. Re-copy fresh request headers from `music.youtube.com` into **Settings**.
- Confirm `/config/auth/headers_auth.json` exists and is readable by UID `1000`.

### Music Files Not Detected
- Verify that your host music path is mounted in `docker-compose.yml`.
- Verify folder permissions allow reading (e.g. `chmod -R a+rX /path/to/music`).
- In the Web UI **Settings**, confirm `/music` or `/downloads` is added to scanned paths.
