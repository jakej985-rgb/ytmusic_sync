# Red Music Locker — Multi-User Cloud Music Locker & Synchronizer

**Red Music Locker** is a self-hosted, multi-user music synchronization service and cloud locker manager. It keeps local music collections and playlists synchronized with private YouTube Music lockers and accounts without altering or deleting local audio files.

---

## 1. What Red Music Locker Does

- **Multi-User Role-Based Isolation**: Secure multi-tenant architecture supporting Administrators and Standard Users with server-side tenant isolation across databases, credentials, playlists, and scan jobs.
- **Recursive Music Library Scanner**: Scans local directory trees (`.mp3`, `.flac`, `.m4a`, `.ogg`, `.wma`) and extracts embedded ID3/Vorbis/FLAC metadata with SHA-256 fingerprints.
- **Smart Normalization Engine**: Strips noisy remaster/edition tags (`[Remastered]`, `(Deluxe Edition)`, `feat.`), formats track numbers, and tolerates duration deltas.
- **Deduplication & Matching**: Compares local files against YouTube Music cloud uploads using a confidence model (Exact, Strong, Weak, Missing) to prevent redundant uploads.
- **Sequential Queue & Resilient Recovery**: Uploads tracks one-by-one with exponential backoff retries. Resumes seamlessly across restarts or transient network dropouts.
- **Companion Browser Extension**: 1-click seamless authorization for desktop browsers without manual DevTools copying.
- **Reverse Proxy & Traefik Ready**: First-class support for HTTPS termination, `X-Forwarded-*` client IP resolution, CORS policy management, and sliding-window rate limiting.

---

## 2. Architecture & Multi-User Security

```text
Host / Reverse Proxy (Traefik / Cloudflare)
       │ (HTTPS / Bearer Auth / X-Forwarded-For)
       ▼
Red Music Locker Container (UID 1000 ytmsync, Read-Only App)
├── REST API & Static Flutter Web UI (Port 8080)
├── Auth Service & Sliding Window Rate Limiter (HTTP 429)
├── Playlist Watcher & Reconciliation Engine
│
├── /config (Persistent Storage Volume)
│   ├── database/ytm_sync.db    ──► Multi-tenant SQLite (user_id partitioned)
│   ├── users/<user_id>/        ──► Per-user AES-GCM encrypted headers & settings
│   ├── backups/                ──► Point-in-time database snapshots
│   └── logs/ytm_sync.log       ──► Sanitized, user-aware masked logs
│
├── /music (Read-Only Host Mount :ro)
└── /downloads (Read-Only Host Mount :ro)
```

### Security Invariants
- **Strict Tenant Partitioning**: Every database query, playlist operation, upload, and sync job derives identity strictly from the validated Bearer session (`current_user.id`), ignoring client-supplied IDs in payloads.
- **Least Privilege Execution**: Unprivileged container user `ytmsync` (`uid=1000, gid=1000`), dropped Linux capabilities (`cap_drop: [ALL]`), and read-only music mounts prevent data loss.
- **Credential Encryption**: Per-user YouTube Music session headers are encrypted at rest using AES-GCM before saving to `/config/users/<user_id>/auth/`.
- **Sliding-Window Rate Limiting**: Protects sensitive authentication and sync endpoints against brute-force and resource abuse with HTTP 429 `Retry-After`.

---

## 3. Quick Start with Docker

### 1. Configuration
Copy the sample environment file and adjust your storage paths:
```bash
cp .env.example .env
```

Example `.env` configuration:
```env
# External HTTP Port for Web UI & API
PORT=6969

# Persistent Application Configuration Path (SQLite database, auth headers, logs)
CONFIG_PATH=/mnt/config

# Host Music Directories (Mounted strictly Read-Only into container)
MUSIC_PATH=/mnt/music
DOWNLOADS_PATH=/mnt/downloads

# Timezone & Logging
TZ=America/Denver
LOG_LEVEL=INFO

# Allowed CORS Origins (for reverse proxy access)
ALLOWED_ORIGINS=https://ytmsync.example.com,http://localhost:6969
```

### 2. Launch
```bash
docker compose -f redmusiclocker.yml --env-file .env up -d
```

Access the Web UI at `http://<SERVER_IP>:6969` (or `http://localhost:6969`).

---

## 4. User Onboarding & Workflows

### Standard User Flow
1. **Login**: Navigate to the Web UI. Log in with your credentials or the bootstrapped administrator account.
2. **Connect YouTube Music**:
   - **Option A (Automated CLI Linker — Recommended)**: If signed into YouTube Music in your desktop browser:
     ```bash
     python3 scripts/auto_link_ytm.py
     ```
     This automatically captures authorization from your active browser session and connects your account in 1 second without needing manual DevTools or extension installation.
   - **Option B (Browser Helper Extension)**:
     - **Firefox**: Install the **Red Music Locker — Account Linker** directly from Firefox Add-ons (Marketplace).
     - **Chrome / Chromium / Brave / Edge**: Open `chrome://extensions` $\rightarrow$ enable **Developer mode** $\rightarrow$ click **Load unpacked** $\rightarrow$ select `ytmusic_sync_helper_ext`.
     - Once installed, open **Settings** $\rightarrow$ click **Connect YouTube Music** to trigger 1-click automatic linking.
   - **Option C (Manual Headers / Headless)**:
     - Open **Settings** $\rightarrow$ expand **Advanced / Developer Authentication**.
     - Paste raw HTTP headers (`Cookie: ...` and `Authorization: SAPISIDHASH ...`) or an exported cURL command from your browser.
     - Click **Save Manual Headers** to validate and encrypt credentials.
3. **Scan Music Folders**:
   - Add your music folder path (e.g., `/music` or `/downloads`).
   - Click **Scan Library** to fingerprint local tracks.
4. **Synchronize & Replicate**:
   - Review match statuses in the **Library** view.
   - Queue missing tracks for upload or enable automated playlist replication.

---

## 5. User Management & Administration

Administrators have access to user management under the **Admin Panel**:
- **Create Users**: Provision standard or administrator accounts with strong passwords.
- **Role Assignment**: Promote or demote user roles.
- **Safe Deletion**: Deleting an account requires explicit confirmation (`confirm=true`). Deleting an account purges that user's encrypted configuration, playlists, and sync jobs from the database.
- **Last-Admin Safeguard**: The system strictly prevents deleting or demoting the final remaining administrator, preventing accidental lockouts.

### Safe Disconnect vs Permanent Account Deletion
| Action | Database Records | Local File Storage | Cloud Uploads | Auth Session |
| :--- | :--- | :--- | :--- | :--- |
| **Disconnect YTM** | **Preserved** (Tracks, jobs, history kept) | **Preserved** | Untouched | Revoked & Removed |
| **Delete User** | **Purged** (Cascaded deletion across tables) | **Purged** (`/config/users/<id>`) | Untouched | Revoked & Removed |

---

## 6. Family Mode & Multi-Account Operations

Family Mode provides secure, opt-in music synchronization and playlist replication across family members while maintaining strict multi-account privacy and credential isolation.

### Architectural Invariants
1. **Requester vs. Destination Separation**:
   `requested_by_user + destination_ytm_account + authorization -> operation`
   The user requesting an upload and the user receiving the track into their personal YouTube Music library are distinct.
2. **Credential & Client Isolation**: Every upload or playlist operation runs exclusively against the destination user's isolated credentials and client instance. Credentials and auth tokens are never reused or shared between accounts.
3. **Zero Implicit Sharing**: Joining a family group grants zero implicit permissions. Each member explicitly controls:
   - `show_account_in_family`: Show/hide account connection status on the family dashboard.
   - `allow_family_uploads`: Allow family members to upload tracks to their account.
   - `allow_family_playlists`: Allow family members to replicate playlists to their account.
   - `allow_family_sync`: Include their account in one-click family bulk sync.
4. **Independent Duplicate Detection**: Duplicate checking is strictly isolated to each destination account. If Track A is already uploaded to Dad's locker but not Mom's, Mom receives the upload without skipping.
5. **Safe Lifecycle Operations**:
   - **Leaving a Family**: A member can leave anytime without losing their local files, uploads, or sync history. An owner cannot leave without first transferring ownership or deleting the family.
   - **Deleting a Family**: Deleting a family purges only family memberships and invitations. Individual users and their YTM accounts remain completely intact.

### Family Mode REST Endpoints
| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/families` | Create a new family group (caller becomes `OWNER`). |
| `GET` | `/api/families` | List families the caller belongs to. |
| `GET` | `/api/families/{id}/dashboard` | Aggregated dashboard respecting member privacy controls. |
| `POST` | `/api/families/{id}/invitations` | Generate a 32-character token invitation with expiration. |
| `POST` | `/api/invitations/{token}/accept` | Join a family group using an invitation token. |
| `PATCH` | `/api/families/{id}/members/{user_id}/permissions` | Update personal family privacy toggles. |
| `POST` | `/api/uploads/destinations` | Upload tracks to one or multiple permitted destination accounts. |
| `GET` | `/api/tracks/{file_id}/destinations-status` | Inspect duplicate/uploaded status per permitted account. |
| `GET` | `/api/families/{id}/queue` | View family queue grouped by track and destinations. |
| `GET` | `/api/families/{id}/history` | Combined family upload history identifying requester and destination. |
| `POST` | `/api/families/{id}/sync` | Trigger fault-tolerant sync across all permitted family accounts. |
| `POST` | `/api/families/{id}/playlists/multi` | Replicate playlists across multiple family accounts. |

---

## 7. Migration from Single-User (Phase V)

When upgrading an existing single-user installation:
1. On first startup, the migration worker detects legacy unencrypted `/config/auth/headers_auth.json`.
2. A safety backup is created at `/config/auth/headers_auth.json.migrated_backup`.
3. Credentials are migrated into the default administrator's secure directory (`/config/users/<admin_id>/auth/headers_auth.json`) and encrypted.
4. The connection is validated against YouTube Music.
5. The legacy file is renamed to `headers_auth.json.migrated` to prevent re-processing.

---

## 8. Reverse Proxy & Traefik Configuration

When deploying behind Traefik, Cloudflare Tunnel, or Nginx:

```text
Client (Browser / Extension)
       │ (HTTPS)
       ▼
Traefik / Nginx Reverse Proxy
       │ (HTTP, Forwarded Headers)
       ▼
Red Music Locker (Port 8080 internal)
```

### Traefik Compose Labels Example
```yaml
services:
  red-music-locker:
    image: ghcr.io/jakej985-rgb/red_music_locker:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.musiclocker.rule=Host(`musiclocker.example.com`)"
      - "traefik.http.routers.musiclocker.entrypoints=websecure"
      - "traefik.http.routers.musiclocker.tls.certresolver=letsencrypt"
      - "traefik.http.services.musiclocker.loadbalancer.server.port=8080"
```

### Environment Configuration
- Set `FORWARDED_ALLOW_IPS=172.16.0.0/12,10.0.0.0/8,127.0.0.1` so Uvicorn trusts client IP headers (`X-Forwarded-For`) for accurate rate limiting.
- Set `ALLOWED_ORIGINS=https://musiclocker.example.com` to enable cross-origin browser extension requests.

---

## 9. Backup & Disaster Recovery

### What is Stored in `/config`:
- `/config/database/ytm_sync.db` — Relational database (music files, matches, playlist replicas, sync jobs)
- `/config/users/<user_id>/` — Per-user encrypted authentication credentials and custom settings
- `/config/backups/` — Automatic database snapshots
- `/config/logs/` — Sanitized log files

### Creating a Full Backup
```bash
tar -czvf ytm_sync_backup_$(date +%Y%m%d_%H%M%S).tar.gz -C /mnt/config .
```

### Restoring from Backup
```bash
# 1. Stop container
docker compose -f ytsync.yml down

# 2. Extract into config path
tar -xzvf ytm_sync_backup_YYYYMMDD_HHMMSS.tar.gz -C /mnt/config/

# 3. Start container
docker compose -f ytsync.yml --env-file .env up -d
```

---

## 10. Development & Running Tests

### Backend Tests (Pytest)
```bash
# Run full test suite (243 unit, boundary, multi-user, and family mode tests)
pytest backend/tests -v
```

### Flutter Frontend Tests
```bash
cd app
flutter test
```
