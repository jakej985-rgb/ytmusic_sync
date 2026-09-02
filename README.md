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
git clone https://github.com/jakej985-rgb/ytmusic_sync.git
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

## 4. Configuration & Environment Variables

All configuration is managed via environment variables in `.env` or passed to `docker compose`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | Host port where YTM Sync is exposed |
| `CONFIG_PATH` | `./config` | Host path storing database, logs, and auth credentials |
| `MUSIC_PATH` | `/mnt/music` | Host directory containing your primary music files |
| `DOWNLOADS_PATH` | `/mnt/downloads` | Host directory containing your secondary/downloaded music |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TZ` | `UTC` | Server timezone for timestamped logs and backups |
| `YTM_SYNC_API_KEY` | *(auto-generated)* | Explicit API key to enforce for Bearer authentication (optional) |
| `ALLOWED_ORIGINS` | *(localhost)* | Comma-separated CORS allowed origins (e.g. `https://ytmsync.example.com`) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Reverse proxy IPs/subnets trusted for `X-Forwarded-*` headers |

---

## 5. Security Model & API Authentication

YTM Sync is engineered with security-by-default principles:

### API Bearer Authentication
- On first startup, YTM Sync automatically provisions a cryptographically strong 32-byte API key.
- The key is securely stored at `/config/auth/api_key.txt` with restricted `0600` permissions.
- To use the Web UI or connect external tools, authenticate using `Authorization: Bearer <API_KEY>`.
- The Web UI automatically prompts for the API key if not yet provided or if a `401 Unauthorized` is encountered.
- OpenAPI docs (`/docs`, `/redoc`, `/openapi.json`) are disabled by default in production.

### Principle of Least Privilege
- **Container Isolation**: Runs as non-root user `ytmsync` (`uid=1000`, `gid=1000`).
- **Capability Lockdown**: Drops all Linux capabilities (`cap_drop: [ALL]`) with `no-new-privileges: true`.
- **Read-Only Music Safety**: Music library directories are kernel-mounted read-only (`:ro`). YTM Sync cannot alter or delete your files.
- **Filesystem Confinement**: Path validation restricts all browsing and file operations strictly to approved roots (`/music`, `/downloads`).
- **SSRF Defense**: External network consumers validate hostnames and DNS resolutions, blocking private IP ranges, loopbacks, and cloud metadata endpoints.

---

## 6. Reverse Proxy Setup (Traefik / Cloudflare / Nginx)

When deploying behind a reverse proxy (e.g., Cloudflare Tunnel, Traefik, or Nginx):

```text
User / Browser
      ↓
Cloudflare (HTTPS)
      ↓
Traefik / Nginx (Reverse Proxy)
      ↓
YTM Sync (Port 8080)
```

1. **Configure Trusted Proxy IPs**: In `.env`, set `FORWARDED_ALLOW_IPS` to your reverse proxy IP or Docker bridge subnet (e.g. `172.16.0.0/12,10.0.0.0/8`).
2. **Configure CORS**: Set `ALLOWED_ORIGINS` to your external domain:
   ```bash
   ALLOWED_ORIGINS=https://ytmsync.example.com
   ```
3. **Traefik Labels**: If using Traefik, uncomment the labels section in `docker-compose.yml` and set `TRAEFIK_HOST=ytmsync.example.com` in `.env`.

---

## 7. Music Mounts (Read-Only Safety)

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

## 8. YouTube Music Authentication Setup

Because Google OAuth credentials are not authorized by Google for personal music uploads, authentication is established using browser session cookies/headers:

1. Open [music.youtube.com](https://music.youtube.com) in Chrome/Firefox/Brave and sign in.
2. Press **F12** to open Developer Tools, then click the **Network** tab.
3. Click any request to `music.youtube.com` (such as `browse` or `player`).
4. Under **Request Headers**, copy all headers (or right-click $\rightarrow$ *Copy as cURL*).
5. In **YTM Sync** $\rightarrow$ **Settings**, paste into the headers box and click **Connect YouTube Music**.
6. Credentials are saved locally with restricted permissions (`0600`) at `/config/auth/headers_auth.json`.

---

## 9. Starting

```bash
docker compose up -d
```

To verify the container is running and healthy:
```bash
docker compose ps
curl -s http://localhost:8080/health
```

---

## 10. Stopping

```bash
docker compose down
```

YTM Sync catches `SIGTERM`, safely completes any active in-flight request, flushes all SQLite database transactions to disk, and exits cleanly.

---

## 11. Updating

To upgrade to the latest version while preserving all database records, authentication credentials, and sync history:

```bash
git pull
docker compose build --no-cache
docker compose up -d
```

---

## 12. Backup

Only the host `/config` directory needs to be backed up. The music directory is already mounted from your host system.

### Create a Quick Backup Archive
```bash
tar -czvf ytm_sync_backup_$(date +%Y%m%d).tar.gz ./config
```

### What is Preserved in `/config`:
- `/config/database/ytm_sync.db` — Track metadata, cloud matches, sync queue & history
- `/config/auth/api_key.txt` — Secure API key
- `/config/auth/headers_auth.json` — YouTube Music authentication headers
- `/config/backups/` — Periodic SQLite database snapshots

---

## 13. Restoration

To restore your configuration and state on a new server or fresh install:

1. Extract your backup archive into your project directory:
   ```bash
   tar -xzvf ytm_sync_backup_YYYYMMDD.tar.gz -C ./
   ```
2. Confirm permissions are readable/writable:
   ```bash
   chmod -R u=rwX,g=rX,o= ./config
   ```
3. Start the container:
   ```bash
   docker compose up -d
   ```
4. Verify all components restored:
   - Database records, sync queue, and matches will be immediately active.
   - The same API key in `config/auth/api_key.txt` will continue to authenticate your clients.
   - YouTube Music connection will remain intact without needing re-authentication.

---

## 14. Troubleshooting

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
