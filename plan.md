Yep. Since the **app itself is already completed**, this plan should tell the agent to **Dockerize the existing working application without changing its behavior**.

# Dockerization Plan — YTM Sync

## Goal

Convert the completed YTM Sync application into a production-ready Docker deployment.

The final result should be:

```text
Docker Host
│
└── YTM Sync
    ├── Web UI
    ├── API
    ├── Sync Engine
    ├── YT Music integration
    ├── SQLite/database
    ├── Authentication
    ├── Logs
    └── Configuration
          │
          ├── /music       READ ONLY
          └── /downloads   READ ONLY
```

The Docker container must preserve **all existing functionality**.

---

# Phase 1 — Inspect Before Changing Anything

The agent must first inspect the existing project.

Determine:

* Language/framework
* Application entry point
* Frontend architecture
* Backend architecture
* Python dependencies
* Dart/Flutter dependencies
* Database implementation
* Configuration system
* Authentication implementation
* Current filesystem paths
* Current logging
* Current ports
* Existing tests
* Existing build scripts
* Existing environment variables

### Important

Do **not** rewrite working code simply to make it fit Docker.

Docker should wrap the existing application.

Before making changes, run the application outside Docker and verify the current version works.

Create a baseline:

```text
Application currently works:
YES / NO

Tests:
PASS / FAIL

Build:
PASS / FAIL
```

If something is already broken, document it before Dockerization.

---

# Phase 2 — Define Container Architecture

Determine whether the completed app can run as:

### Preferred

**One container**

```text
ytm-sync
├── Web frontend
├── API
├── Sync engine
├── YT Music integration
└── Database
```

Use one container if the current architecture allows it cleanly.

Do **not** introduce unnecessary microservices.

If the existing application genuinely requires separate frontend/backend processes, use:

```text
ytm-sync
ytm-sync-api
```

but only when necessary.

The goal is **simple deployment**.

---

# Phase 3 — Dockerfile

Create a production Dockerfile.

Requirements:

* Use a small appropriate base image
* Pin important dependency versions
* Install only runtime dependencies in the final image
* Don't include development junk
* Don't include local databases
* Don't include authentication credentials
* Don't include user's music
* Don't run as root unless technically unavoidable
* Use a dedicated application user
* Set a proper working directory
* Configure environment variables
* Provide a proper application entrypoint

Use a multi-stage build if the frontend requires compilation.

Example architecture:

```text
Build stage
    ↓
Compile frontend
    ↓
Install backend dependencies
    ↓
Production stage
    ↓
Copy only required artifacts
```

---

# Phase 4 — Docker Compose

Create:

```text
docker-compose.yml
```

The default deployment should require only:

```bash
docker compose up -d
```

The service should include:

```yaml
restart: unless-stopped
```

unless there is a specific reason not to.

---

# Phase 5 — Persistent Configuration

This is critical.

Anything that must survive a container recreation must live outside the container filesystem.

Use:

```text
/config
```

for persistent application state.

Structure:

```text
/config
├── database/
├── logs/
├── auth/
├── cache/
└── settings/
```

The exact structure should follow the existing application's implementation.

Docker:

```yaml
volumes:
  - ./config:/config
```

or preferably a configurable host path.

---

# Phase 6 — Database

If the existing app uses SQLite:

Store it under:

```text
/config/database/
```

Never:

```text
/app/database.db
```

because that disappears when the container is recreated.

Test:

```text
Start container
    ↓
Create data
    ↓
Stop container
    ↓
Delete container
    ↓
Start new container
    ↓
Data still exists
```

This test is mandatory.

---

# Phase 7 — YT Music Authentication

Authentication must survive container updates.

The agent must identify exactly how the completed app stores YT Music authentication.

Move that state to:

```text
/config/auth/
```

or whatever secure persistent location the application requires.

### Never

* Put credentials in the Dockerfile
* Put credentials in Git
* Put credentials in `docker-compose.yml`
* Put credentials directly into the image
* Commit authentication files

Add appropriate `.gitignore` entries.

Example:

```text
config/
.env
*.json
*.key
*.token
```

The agent should inspect the actual authentication implementation before deciding which files to ignore.

---

# Phase 8 — Music Mounts

The container needs access to local music.

Use configurable mounts:

```text
/music
/downloads
```

Both should be **read-only**.

Example:

```yaml
volumes:
  - ${MUSIC_PATH}:/music:ro
  - ${DOWNLOADS_PATH}:/downloads:ro
  - ${CONFIG_PATH}:/config
```

Environment variables:

```text
MUSIC_PATH=/mnt/music
DOWNLOADS_PATH=/mnt/downloads
CONFIG_PATH=./config
```

The application should never need write permission to these directories.

---

# Phase 9 — Multiple Music Locations

If the existing application supports multiple folders, preserve that functionality.

For example:

```text
/music
/music2
/downloads
/downloads2
```

Docker Compose should allow users to add additional mounts.

Don't hard-code the host paths.

---

# Phase 10 — Filesystem Permissions

The agent must test permissions.

The application user inside Docker needs:

```text
READ
```

access to:

```text
/music
/downloads
```

and:

```text
READ + WRITE
```

to:

```text
/config
```

It should **not** need write access to music.

Test:

```text
Read music → works
Scan music → works
Read metadata → works
Write music → denied
Write config → works
Write logs → works
```

---

# Phase 11 — Environment Configuration

Create:

```text
.env.example
```

Document every supported variable.

Potential configuration:

```text
TZ=America/Denver

PORT=8080

MUSIC_PATH=/path/to/music
DOWNLOADS_PATH=/path/to/downloads
CONFIG_PATH=./config

LOG_LEVEL=INFO
```

Only include variables that the application actually supports.

Don't invent configuration merely for the example.

---

# Phase 12 — Health Check

Implement a health endpoint if one doesn't already exist.

For example:

```text
GET /health
```

Expected:

```json
{
  "status": "healthy"
}
```

Docker should use it:

```yaml
healthcheck:
  ...
```

The health check should verify that the application itself is functioning.

It should **not** require YouTube Music to be online.

Otherwise an external YTM outage would incorrectly make the entire container unhealthy.

---

# Phase 13 — Startup

The container should start automatically.

Startup sequence:

```text
Container starts
      ↓
Load configuration
      ↓
Create required /config directories
      ↓
Validate database
      ↓
Load authentication
      ↓
Start application
      ↓
Health check
      ↓
READY
```

If authentication isn't configured, the application should still start.

For example:

```text
Application: HEALTHY
YouTube Music: NOT CONNECTED
```

rather than crashing.

---

# Phase 14 — Graceful Shutdown

Docker may send:

```text
SIGTERM
```

The application must handle shutdown correctly.

If an upload is currently running:

```text
SIGTERM
   ↓
Stop accepting new uploads
   ↓
Safely stop current operation if possible
   ↓
Save state
   ↓
Close database
   ↓
Exit
```

Do not corrupt the database.

Do not leave the queue in an impossible state.

---

# Phase 15 — Upload Queue Persistence

This is especially important for this application.

Suppose:

```text
100 songs
```

are queued.

Container restarts after:

```text
37 uploaded
```

The application should resume intelligently.

After restart:

```text
37 → already completed
38 → resume/retry
39–100 → still pending
```

It should **not start uploading the first 37 again**.

---

# Phase 16 — Logging

Logs should be available through:

```bash
docker logs ytm-sync
```

but persistent application logs should also be stored under:

```text
/config/logs
```

Don't log:

* passwords
* authentication headers
* tokens
* cookies
* private credentials

---

# Phase 17 — Docker Networking

The application should listen on:

```text
0.0.0.0
```

inside the container.

Do not bind only to:

```text
127.0.0.1
```

inside the container, or Docker port forwarding won't work properly.

Default example:

```text
8080
```

Host:

```text
http://SERVER_IP:8080
```

---

# Phase 18 — Traefik Compatibility

Since this application is intended to run on a Docker server, make it **Traefik-compatible**, but don't make Traefik mandatory.

The Compose file should optionally support:

```text
Traefik
   ↓
YTM Sync
```

Example eventual URL:

```text
ytmsync.example.com
```

The agent should:

* Add appropriate labels
* Keep the internal application port configurable
* Avoid exposing unnecessary ports when Traefik is used
* Document both direct-port and Traefik deployment

Don't hard-code your personal domain.

---

# Phase 19 — Security

The container should follow least privilege.

Implement where practical:

```text
non-root user
read-only music mounts
minimal Linux capabilities
no privileged mode
no host PID
no host filesystem access
no Docker socket
```

**Do not mount `/var/run/docker.sock`.**

The application has no reason to control Docker.

If the application can run with:

```yaml
read_only: true
```

for its root filesystem, consider doing so while providing writable mounts for `/config` and required temporary directories.

---

# Phase 20 — Temporary Storage

If uploads require temporary files:

```text
/tmp
```

or:

```text
/data/tmp
```

should be handled explicitly.

Don't accidentally write temporary files into:

```text
/music
```

or:

```text
/downloads
```

---

# Phase 21 — Docker Build Test

Build from a completely clean environment:

```bash
docker compose build --no-cache
```

Then:

```bash
docker compose up -d
```

Verify:

```bash
docker compose ps
```

The container must be:

```text
running
healthy
```

---

# Phase 22 — Functional Testing

Test the entire application inside Docker.

### Test 1 — UI

Open:

```text
http://SERVER:PORT
```

Verify UI loads.

### Test 2 — Music scan

Mount a test music directory.

Verify:

```text
Files detected
Metadata detected
Database populated
```

### Test 3 — YT Music authentication

Authenticate.

Verify:

```text
Connected
```

### Test 4 — Existing uploads

Retrieve YT Music uploads.

Verify they appear.

### Test 5 — Matching

Create:

```text
Already uploaded
Not uploaded
Ambiguous
```

Verify correct statuses.

### Test 6 — Upload

Upload one test song.

Verify:

```text
Queued
Uploading
Completed
Verified
```

### Test 7 — Restart

While/after syncing:

```bash
docker restart ytm-sync
```

Verify state survives.

### Test 8 — Recreate

```bash
docker compose down
docker compose up -d
```

Verify everything remains.

### Test 9 — Update

Replace the image with a newer build.

Verify:

```text
Database survives
Authentication survives
Settings survive
History survives
```

---

# Phase 23 — Failure Testing

The agent must deliberately test failures.

### Network disappears

Expected:

```text
Upload → Failed/Retry
Application remains running
```

### YTM authentication expires

Expected:

```text
Application remains running
Sync pauses
User is informed
```

### File disappears

Expected:

```text
Song → Failed/Missing
Other songs continue
```

### Bad audio file

Expected:

```text
File → Failed
Other songs continue
```

### Container restart

Expected:

```text
Queue preserved
Database preserved
No duplicate uploads
```

---

# Phase 24 — Documentation

Create/update:

```text
README.md
DOCKER.md
.env.example
```

README should explain:

```text
What YTM Sync does
Requirements
Docker installation
Configuration
Music mounts
YT Music authentication
Starting
Stopping
Updating
Backup
Troubleshooting
```

Example:

```bash
git clone ...
cd ytm-sync

cp .env.example .env

docker compose up -d
```

Then explain how to access it.

---

# Phase 25 — Backup

Document what needs to be backed up.

At minimum:

```text
/config
```

because it contains:

* Database
* Settings
* Sync history
* Authentication/configuration
* Application state

The music itself should **not** be part of the Docker backup.

It's already mounted from the host.

---

# Phase 26 — Image Publishing

Once local Docker deployment works, optionally prepare the project for a registry.

Example:

```text
ghcr.io/<user>/ytm-sync
```

The agent should **not publish anything automatically** unless explicitly configured to do so.

Create:

```text
.github/workflows/docker.yml
```

Eventually:

```text
git push
   ↓
GitHub Actions
   ↓
Docker build
   ↓
Tests
   ↓
Container image
   ↓
GHCR
```

Tags:

```text
latest
v1.0.0
commit SHA
```

---

# Phase 27 — Final Project Structure

The target should be approximately:

```text
ytm-sync/
│
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env.example
├── README.md
├── DOCKER.md
│
├── src/
│   └── existing application
│
├── tests/
│
├── scripts/
│
└── .github/
    └── workflows/
        └── docker.yml
```

Don't force the existing source tree into this exact structure if doing so would damage the project.

---

# Definition of Done

The agent should only consider Dockerization complete when this entire sequence works:

```text
docker compose up -d
        ↓
Container starts
        ↓
Health = healthy
        ↓
Open web UI
        ↓
Configure YT Music
        ↓
Configure music directory
        ↓
Scan music
        ↓
Retrieve YTM uploads
        ↓
Compare
        ↓
Select missing music
        ↓
Upload
        ↓
Verify
        ↓
Record result
        ↓
Restart container
        ↓
Everything remains
        ↓
Run sync again
        ↓
Already uploaded songs are skipped
```

### Most important rule for the agent

**Do not rewrite the working application. Dockerize it.**

If a change is required because the existing application assumes a local filesystem, hard-coded path, localhost binding, or non-persistent database location, make the **smallest architectural change necessary**, document it, and add a test for it.

The finished project should be something you can eventually deploy on your Docker server with essentially:

```bash
docker compose up -d
```

and have the application survive **container restarts, image updates, server reboots, and normal sync failures** without losing its state.
