I audited the **current `main` branch** of `jakej985-rgb/ytmusic_sync` plus the ZIP you uploaded. I looked at the backend, Flutter app structure, Docker setup, CI, tests, persistence, auth, filesystem handling, upload/download paths, and recent commits.

[YTM Sync repository](https://github.com/jakej985-rgb/ytmusic_sync?utm_source=chatgpt.com)

## Overall verdict

**The project is fairly well along functionally, but I would NOT expose the current container directly to the Internet yet.**

### Scorecard

| Area                 |  Rating | Verdict                                     |
| -------------------- | ------: | ------------------------------------------- |
| Architecture         | 🟢 8/10 | Good separation of services                 |
| Docker               | 🟢 8/10 | Good foundation                             |
| Data persistence     | 🟢 8/10 | SQLite/config design is solid               |
| Music safety         | 🟢 8/10 | Read-only mounts are a good choice          |
| Matching logic       | 🟢 8/10 | Recent changes improved safety              |
| Test coverage        | 🟡 7/10 | Lots of tests, but CI/test setup needs work |
| CI/CD                | 🟡 7/10 | Good pipeline, some weaknesses              |
| Security             | 🔴 4/10 | **Biggest problem**                         |
| Production readiness | 🟡 6/10 | Good homelab app, not Internet-safe yet     |

---

# 🔴 1. Biggest issue: API has effectively NO authentication

This is the most important finding.

The backend exposes destructive/privileged operations such as:

* delete YouTube Music uploads
* batch delete uploads
* upload local songs
* start scans
* modify metadata
* change configured folders
* start playlist downloads
* start playlist synchronization
* modify queue state
* configure YouTube Music authentication

But I don't see an API authentication/authorization layer protecting those endpoints.

The CORS configuration is also:

```python
allow_origins=["*"],
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"],
```

That is far too permissive for an application that can manipulate someone's YouTube Music library.

### Why this matters

Your current architecture is basically:

```text
Internet
   ↓
Cloudflare / Traefik
   ↓
YTM Sync
   ↓
YouTube Music account
```

If someone gets access to the YTM Sync HTTP interface, they potentially get access to operations against the authenticated YT Music account.

**For your setup, this is the #1 thing I'd fix.**

### Recommended

At minimum:

```text
Reverse proxy authentication
        +
YTM Sync API authentication
```

And preferably:

* local-only mode by default
* API token/session authentication
* authentication required for `/api/*`
* `/health` can remain public
* restrictive CORS
* CSRF protection if browser cookie auth is used

---

# 🔴 2. `/api/fs/browse` exposes the container filesystem

This endpoint lets the client browse arbitrary paths:

```text
/api/fs/browse?path=...
```

It starts at `/music` normally, but the API accepts an arbitrary filesystem path.

The code only excludes:

```text
/proc
/sys
/dev
/run
```

It does **not** restrict browsing to:

```text
/music
/downloads
```

or another allowed-root list.

That means the UI/API can potentially browse:

```text
/config
/app
/etc
/root
/tmp
...
```

The application is running as `ytmsync`, so permissions limit what it can see, but this is still unnecessary exposure.

### Better

Use an allowlist:

```text
/music
/downloads
```

and make sure:

```text
requested_path.resolve()
```

remains underneath one of those roots.

---

# 🔴 3. User-supplied `destination_dir` is not constrained

Several operations accept:

```python
destination_dir
```

and turn it directly into:

```python
Path(destination_dir)
```

For example:

```python
destination_dir=Path(req.destination_dir)
```

The download system can then write files there.

That's particularly important because `/config` is writable.

A malicious request shouldn't be able to tell the service:

> "Download this track into `/config/something`."

This becomes much more serious because there is currently no API authentication.

### Fix

Define explicit writable download roots, e.g.:

```text
/music
/downloads
/config/staging
```

and reject everything else.

---

# 🟠 4. You have a confusing contradiction around read-only music

Docker correctly mounts:

```yaml
- ${MUSIC_PATH:-/mnt/music}:/music:ro
- ${DOWNLOADS_PATH:-/mnt/downloads}:/downloads:ro
```

That's excellent.

But `/api/songs/{file_id}/metadata` attempts:

```python
write_metadata_tags(...)
```

against the actual music file.

The code catches the failure and continues.

So in Docker:

```text
UI says:
"Update metadata"

        ↓

Database changes

        ↓

Attempt to modify file

        ↓

Permission denied because :ro

        ↓

Warning logged

        ↓

API returns success
```

That's a UX/data-integrity problem.

The application can therefore report a metadata change that **didn't actually happen to the file**.

### Decide on one model

Either:

**A. Truly read-only**

Then metadata edits should update only YTM Sync's database and clearly say:

> "Local file is read-only; tags were not modified."

or:

**B. Writable music**

Remove `:ro` and deliberately support tag modification.

Given your project philosophy, I strongly prefer **A**.

---

# 🟢 5. Your recent matching changes were good

The last several commits show the project moving in the right direction.

The recent history includes:

* duplicate checking
* queue improvements
* metadata matching changes
* strict artist/title/album matching
* deleted-upload cleanup
* better YT-DLP client handling

The most important conceptual improvement is the move away from:

> "This looks close enough, download it."

toward:

> "If we cannot confidently identify artist + title + album, don't download it; put it in Needs Help."

That's exactly the safer behavior for a music synchronization application.

---

# 🟢 6. Docker design is actually pretty good

I like several things here.

### Non-root container

You create:

```text
ytmsync UID 1000 / GID 1000
```

and run the application as that user.

Good.

### Dropped capabilities

Compose has:

```yaml
cap_drop:
  - ALL
```

and only adds:

```yaml
CHOWN
SETUID
SETGID
```

That's a good security posture.

### No Docker socket

Excellent.

You don't need:

```text
/var/run/docker.sock
```

which avoids a huge class of container escape/control problems.

### Read-only music

Also good.

---

# 🟡 7. Docker image isn't completely reproducible

Your requirements use ranges:

```text
fastapi>=...
uvicorn>=...
ytmusicapi>=...
yt-dlp>=...
```

This means:

```text
Build today
≠
Build three months from now
```

because dependencies can change.

For an application you're deploying through GHCR, I'd pin versions.

For example:

```text
fastapi==...
uvicorn[standard]==...
ytmusicapi==...
...
```

Then Dependabot can intentionally update them.

You already have Dependabot configured, so pinning would make the update process much safer.

---

# 🟡 8. Docker builds the Flutter UI from a precompiled directory

The Dockerfile does:

```dockerfile
COPY backend/web_dist/ ./web_dist/
```

It does **not** build Flutter.

That means the repository effectively has two things that must stay synchronized:

```text
app/
    Flutter source

backend/web_dist/
    compiled Flutter application
```

This is a maintenance trap.

Someone can change:

```text
app/lib/...
```

and forget to regenerate:

```text
backend/web_dist/
```

Then Docker happily ships the old UI.

### Better CI flow

Have CI:

```text
Flutter source
      ↓
flutter build web
      ↓
backend/web_dist
      ↓
Docker image
```

Or build the Flutter app directly in a multi-stage Dockerfile.

---

# 🟡 9. Test suite exists, but I couldn't actually execute it in this environment

This is important.

I first ran:

```bash
python -m pytest backend/tests -q
```

and got:

```text
ModuleNotFoundError: No module named 'ytm_service'
```

That's because the repo expects:

```bash
PYTHONPATH=backend pytest backend/tests/
```

which your GitHub Actions workflow correctly uses.

I then tried the correct invocation, but the audit environment doesn't have the Python dependencies installed, and external package installation isn't available here.

So I **couldn't honestly claim that the test suite passes**.

Your CI does use:

```bash
pip install -r backend/requirements.txt
PYTHONPATH=./backend pytest backend/tests/ -v
```

which is the correct basic approach.

There are a lot of backend tests, though, which is a good sign.

---

# 🟡 10. CI doesn't appear to test Flutter

Your Docker workflow tests Python:

```text
pip install
pytest
```

but I don't see equivalent:

```text
flutter pub get
flutter analyze
flutter test
flutter build web
```

That leaves a major part of the application outside CI validation.

I'd add a Flutter job.

At minimum:

```bash
cd app
flutter pub get
flutter analyze
flutter test
flutter build web
```

Then Docker should consume the freshly generated web build.

---

# 🟡 11. Versioning is duplicated

I noticed version information in multiple places:

```text
backend/ytm_service/__init__.py
backend/ytm_service/main.py
app/pubspec.yaml
backend/web_dist/version.json
```

Your version-bump script attempts to synchronize these.

That's workable, but fragile.

The bigger concern is that Docker extracts the version from:

```python
__version__
```

while FastAPI has its own:

```python
version="0.0.1-beta"
```

Those can drift.

I'd establish one source of truth.

---

# 🟠 12. Batch operations need limits

For example:

```python
file_ids: list[int]
```

and:

```python
for fid in req.file_ids:
```

There isn't an obvious request-level maximum.

Likewise batch YouTube deletion can potentially process a huge list.

For a local application this isn't catastrophic, but once exposed through a proxy it becomes a resource/abuse problem.

I'd cap things like:

```text
batch upload: 500
batch delete: 100
```

or whatever makes sense.

---

# 🟠 13. Background jobs aren't durable enough for true crash recovery

The README describes:

> sequential upload queue with recovery

and the DB does persist job state, which is good.

But some work is launched through in-process:

```python
asyncio.create_task(...)
```

and FastAPI:

```python
BackgroundTasks
```

If the container dies while that task is executing, the Python task itself disappears.

The database can tell you:

```text
job = uploading
```

but the actual worker is gone.

You need startup reconciliation such as:

```text
STARTUP
 ↓
Find jobs stuck in UPLOADING
 ↓
Determine whether they are recoverable
 ↓
Reset to QUEUED / FAILED
 ↓
Resume
```

That would make the "recovery" claim much stronger.

---

# 🟢 14. SQLite design looks reasonable for this application

You're using:

```text
aiosqlite
```

and transactions/commits are explicitly handled.

Foreign keys also use:

```sql
ON DELETE CASCADE
```

in the schema.

That's a good fit for a single-container local application.

I would not move this to Postgres just because it sounds more "production."

For YTM Sync's intended use:

```text
one application
one database
one user/family
```

SQLite is perfectly reasonable.

---

# 🟢 15. The authentication credential storage is handled reasonably

The code explicitly sets:

```text
0600
```

on the YouTube Music authentication file.

That's good.

And the Docker setup keeps it under:

```text
/config/auth/
```

rather than baking credentials into the image.

That's exactly what you want.

**But this makes the missing API authentication even more important.**

An unauthenticated API shouldn't be sitting next to an authenticated YouTube Music session.

---

# 🟠 16. Playlist import accepts arbitrary URLs

You have:

```text
/api/ytm/playlists/import-url
```

and feed the URL into `yt-dlp`.

This is useful functionality, but it needs input validation.

Ideally accept only:

```text
youtube.com
music.youtube.com
youtu.be
```

and supported playlist URL forms.

Otherwise you're effectively giving an HTTP endpoint the ability to make outbound requests based on user-supplied URLs.

Even if `yt-dlp` handles the URL safely, it's better to explicitly enforce the intended protocol/domain.

---

# 🟡 17. README has an actual installation mistake

The README currently shows:

```bash
git clone https://github.com/example/ytmusic_sync.git
```

That should obviously be your real repository.

It should be:

```bash
git clone https://github.com/jakej985-rgb/ytmusic_sync.git
```

This is small, but it makes the official deployment instructions look unfinished.

---

# The architecture I'd aim for

Your current architecture is roughly:

```text
                 ┌─────────────────┐
                 │   Flutter UI    │
                 └────────┬────────┘
                          │
                          ▼
                 ┌─────────────────┐
                 │    FastAPI      │
                 │                 │
                 │ main.py         │
                 │ queue           │
                 │ matcher         │
                 │ scanner         │
                 └───────┬─────────┘
                         │
          ┌──────────────┼───────────────┐
          ▼              ▼               ▼
       SQLite        YTMusic API       Files
```

I'd evolve it toward:

```text
                 ┌──────────────────┐
                 │    Cloudflare    │
                 └────────┬─────────┘
                          │
                    Authentication
                          │
                          ▼
                 ┌──────────────────┐
                 │     Traefik      │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │     FastAPI      │
                 │                  │
                 │ API Auth         │
                 │ Rate Limits      │
                 │ Validation       │
                 └────────┬─────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
     SQLite          Job/Queue          YTMusic
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                     Filesystem
                  /music :ro
               /downloads :ro
```

---

# Priority list

If this were my repo, I'd work in this order:

### 🔴 P0 — Do before exposing externally

1. **Add API authentication**
2. **Restrict filesystem browsing**
3. **Restrict `destination_dir`**
4. **Restrict playlist/import URLs**
5. **Fix CORS**
6. **Protect all upload/delete/auth endpoints**

### 🟠 P1 — Before calling it production-ready

7. Startup recovery for interrupted jobs
8. Add batch request limits
9. Make read-only metadata behavior explicit
10. Add Flutter CI
11. Build Flutter web during CI rather than relying on committed `web_dist`
12. Pin Python dependencies

### 🟡 P2 — Cleanup

13. Fix README clone URL
14. Consolidate version source
15. Improve API error handling
16. Add more integration tests
17. Add health/readiness distinction

---

## One thing I especially like

The direction of the last few commits is **very good**.

You recently changed the system from potentially making a questionable metadata guess to:

```text
Can't confidently identify it
        ↓
DON'T DOWNLOAD
        ↓
Needs Help
        ↓
User resolves it
        ↓
Download
        ↓
Tag
        ↓
Upload
```

That is the right philosophy for this app.

## Bottom line

**I would call the current repo "good homelab beta, not Internet-safe production."**

The core application architecture is actually in pretty decent shape. The **security boundary around the FastAPI service is the major hole**, not the matching engine or Docker design.

And one especially important point for your setup: **if you're planning to put this behind your existing Traefik + Cloudflare setup, do the authentication/authorization work before exposing the YTM Sync route.** A reverse proxy alone doesn't make those destructive API endpoints safe.

If you want, the next logical step is for me to do a **P0 security audit file-by-file and identify exactly which endpoints need protection and what code should change**, rather than just giving you the high-level findings.
