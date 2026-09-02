Absolutely. Based on the final audit, I’d make this a **completion plan**, not another giant security rewrite. The goal is to take the current `main` branch and move it through **release hardening → deployment → real-world validation → v1 release**.

# YTM Sync — Final Release Completion Plan

**Repository:** `jakej985-rgb/ytmusic_sync`
**Target:** Production-ready v1 release
**Current state:** Security implementation substantially complete; final hardening and deployment validation remaining.

---

# Phase 0 — Freeze the Current State

### Goal

Create a known-good checkpoint before making any more changes.

### Tasks

#### 0.1 Verify current branch

```bash
git checkout main
git pull
git status
```

Expected:

```text
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
```

#### 0.2 Record current commit

Current audited commit:

```text
459a317daf769b48526295bf81463e0bf4832a7f
```

Do not rewrite history.

#### 0.3 Create release candidate tag later

Don't tag yet.

We want:

```text
main
  ↓
release hardening
  ↓
RC
  ↓
production validation
  ↓
v1.0.0
```

---

# Phase 1 — Fix Reverse-Proxy Trust Configuration

## Priority: HIGH

The application currently supports:

```text
FORWARDED_ALLOW_IPS
```

but defaults it to:

```text
*
```

and Docker Compose doesn't expose the setting.

### 1.1 Add configuration to `.env.example`

Add:

```env
# Reverse proxy addresses trusted for X-Forwarded-* headers.
# Set this to the IP/network of your trusted reverse proxy.
FORWARDED_ALLOW_IPS=
```

Don't blindly choose an IP yet.

---

### 1.2 Add it to Docker Compose

Add:

```yaml
- FORWARDED_ALLOW_IPS=${FORWARDED_ALLOW_IPS:-}
```

The application should have sensible behavior when empty rather than silently trusting everybody.

---

### 1.3 Decide the actual production value

Because your intended architecture is:

```text
Cloudflare
    ↓
Traefik
    ↓
YTM Sync
```

determine what source IP YTM Sync actually sees from Traefik.

On the server:

```bash
docker inspect traefik
```

and:

```bash
docker network inspect <your-traefik-network>
```

The important question is:

> What IP/network is directly connecting to YTM Sync?

Use that as the trusted proxy source.

### 1.4 Test forwarded headers

Verify:

* normal request
* request through Traefik
* request through Cloudflare
* direct request
* forged `X-Forwarded-For`
* forged `X-Forwarded-Proto`

The application should only trust forwarded information from the configured proxy.

---

# Phase 2 — Authentication Release Hardening

## Priority: HIGH

The Bearer authentication implementation is already in place.

Now verify the **complete lifecycle**.

### 2.1 Fresh installation

Delete only a test installation's config:

```bash
rm -rf ./config
docker compose up -d
```

Verify:

```bash
ls -l ./config/auth/
```

Expected:

```text
api_key.txt
```

Permissions:

```text
-rw------- 
```

---

### 2.2 Verify generated key

Do not expose it in logs.

Retrieve it locally:

```bash
cat ./config/auth/api_key.txt
```

Then:

```bash
curl -i http://localhost:8080/api/status
```

Expected:

```text
401 Unauthorized
```

Then:

```bash
curl -i \
  -H "Authorization: Bearer YOUR_KEY" \
  http://localhost:8080/api/status
```

Expected:

```text
200
```

---

### 2.3 Test bad authentication

Test all of these:

```text
No Authorization header
Authorization: Basic ...
Authorization: Bearer
Authorization: Bearer wrong-key
Authorization: bearer wrong-key
Authorization: Bearer <correct-key>extra
```

Only the exact valid key should work.

---

### 2.4 Restart persistence

```bash
docker compose restart
```

Verify the same key still works.

Then:

```bash
docker compose down
docker compose up -d
```

Verify again.

**Important:** `down/up` must not regenerate the key because `/config` persists.

---

# Phase 3 — API Surface Audit

## Priority: HIGH

The current middleware protects `/api/*`.

Now verify there isn't an API-like endpoint hiding outside `/api`.

### 3.1 Enumerate routes

From the server:

```bash
docker compose exec ytm-sync python -c \
'import sys; sys.path.insert(0,"/app"); from ytm_service.main import app; print("\n".join(f"{r.methods} {r.path}" for r in app.routes))'
```

Create a complete route list.

Classify every route:

```text
PUBLIC
AUTHENTICATED
STATIC
HEALTH
```

### 3.2 Required public routes

Ideally only:

```text
/health
/
/static frontend assets
```

Everything that performs application operations should require authentication.

### 3.3 Verify docs

Production should return:

```text
/docs          404
/redoc         404
/openapi.json  404
```

unless explicitly enabled.

---

# Phase 4 — Filesystem Security Final Audit

## Priority: HIGH

This was one of the original major vulnerabilities.

The centralized validator is now present in `security.py`.

Now audit **every filesystem operation**, not just the ones previously fixed.

### 4.1 Search for filesystem APIs

Run:

```bash
grep -RInE \
'Path\(|open\(|mkdir\(|makedirs\(|rglob\(|glob\(|unlink\(|remove\(|rename\(|replace\(|copy2\(|move\(' \
backend/ytm_service
```

For every result ask:

> Can an API-controlled value reach this operation?

If yes:

```text
API input
   ↓
validate_fs_path()
   ↓
filesystem operation
```

---

### 4.2 Check these specifically

Audit:

* scanner paths
* destination directories
* downloads
* playlist downloads
* metadata replacement
* artwork
* database backups
* queue staging
* local file matching
* filesystem browser
* upload paths
* generated filenames

---

### 4.3 Traversal test matrix

Test:

```text
../
../../
/etc/passwd
/music/../
/downloads/../
/music/foo/../../etc
```

And encoded versions:

```text
%2e%2e
%2F
```

Also test:

```text
null bytes
empty path
relative path
absolute path
nonexistent path
symlink
symlink ancestor
```

---

# Phase 5 — Filesystem Root Policy

## Priority: HIGH

Current Docker defaults are:

```text
/music
/downloads
```

and Compose mounts both read-only.

### 5.1 Verify the container cannot write music

Inside container:

```bash
docker compose exec ytm-sync sh
```

Then attempt a test write:

```bash
touch /music/test-write
```

Expected:

```text
Read-only file system
```

Same for:

```bash
touch /downloads/test-write
```

---

### 5.2 Verify `/config` is writable

```bash
touch /config/test-write
rm /config/test-write
```

Must succeed.

---

### 5.3 Verify application cannot escape roots

Test:

```text
/config
/music
/downloads
```

against:

```text
/
 /etc
 /tmp
 /proc
 /root
 /app
```

The API filesystem browser should never expose these unless explicitly configured as an allowed root.

---

# Phase 6 — SSRF / External Network Audit

## Priority: HIGH

The URL validator is now significantly hardened. It checks scheme, credentials, ports, hostname, DNS resolution, and private/internal IP classes.

Now find **every external network consumer**.

### 6.1 Search

```bash
grep -RInE \
'https?://|requests\.|httpx\.|urllib|urlopen|aiohttp|socket' \
backend/ytm_service
```

For each network request determine:

```text
Is URL user-controlled?
        ↓
YES → validate first
NO  → document why trusted
```

---

### 6.2 YouTube URLs

Only allow:

```text
youtube.com
www.youtube.com
music.youtube.com
m.youtube.com
youtu.be
```

The current validator explicitly defines those hosts.

Test:

```text
youtube.com
music.youtube.com
youtu.be
google.com
localhost
127.0.0.1
192.168.x.x
10.x.x.x
172.16.x.x
169.254.x.x
[::1]
```

---

### 6.3 DNS rebinding

This is important enough to test conceptually.

The application validates DNS resolution before connecting, but the connection itself could theoretically resolve differently later.

For v1, document this as:

```text
DNS resolution is checked before external requests.
```

Do not build another giant networking system unless testing demonstrates a real issue.

---

# Phase 7 — Secret / Credential Audit

## Priority: HIGH

### 7.1 Search repository

Run:

```bash
git grep -nEi \
'api[_-]?key|password|secret|token|cookie|authorization|bearer'
```

Review every result.

You should find:

```text
configuration references
test values
documentation
```

but never real credentials.

---

### 7.2 Search Git history

Because the repository is public, check history too:

```bash
git log --all --oneline -- .env
```

and:

```bash
git log --all --oneline -- headers_auth.json
```

Also:

```bash
git log --all -S"YTM_SYNC_API_KEY"
```

If real credentials were ever committed, simply deleting the current file is **not enough**.

They must be considered compromised and rotated.

---

### 7.3 Verify `.gitignore`

Current `.gitignore` correctly excludes:

```text
config/
.env
headers_auth.json
*.token
*.key
*.db
*.log
```

Keep this.

---

# Phase 8 — Flutter Security Audit

## Priority: MEDIUM/HIGH

The Flutter client currently stores the API key in `SharedPreferences`.

For **Flutter Web**, this is ultimately browser-side storage and therefore not a true secret vault.

That's acceptable for your architecture because:

> Anyone who can use the UI needs the API credential.

But document the threat model.

### 8.1 Verify API key isn't compiled into the bundle

Search:

```bash
grep -RIn "YTM_SYNC_API_KEY" backend/web_dist app/build/web
```

There should be no server-side secret embedded into the release bundle.

---

### 8.2 Verify Bearer only

Search:

```bash
grep -RInE 'X-API-Key|X_API_KEY' app/lib backend
```

There should be no legacy authentication path.

---

### 8.3 Unauthorized behavior

When API returns:

```text
401
```

Flutter should:

1. clear invalid key/session state
2. show authentication UI
3. avoid infinite request loops

---

# Phase 9 — CI/CD Release Gate

## Priority: HIGH

Your Docker workflow now does the right general sequence: Flutter analyze/test/build, backend tests, then Docker build/push.

### 9.1 Require these checks

Before release:

```text
Flutter analyze       PASS
Flutter tests         PASS
Flutter web build     PASS
Python tests          PASS
Docker build          PASS
```

### 9.2 Make sure tests run before push

The workflow already places testing before the Docker push.

Keep that.

---

### 9.3 Verify exact commit

Do not rely only on:

> "The agent says CI passed."

Verify the GitHub Actions run corresponding to the actual release commit.

The current GitHub status API did not give me a completed status for `459a317`, so this is one thing I would explicitly verify before tagging.

---

# Phase 10 — Docker Production Test

## Priority: HIGH

Build exactly what users will run.

```bash
docker compose build --no-cache
```

Then:

```bash
docker compose up -d
```

Check:

```bash
docker compose ps
```

Expected:

```text
ytm-sync   Up (healthy)
```

---

### 10.1 Inspect security configuration

```bash
docker inspect ytm-sync
```

Verify:

```text
User = ytmsync
NoNewPrivileges = true
CapDrop = ALL
```

---

### 10.2 Verify process user

```bash
docker compose exec ytm-sync id
```

Expected:

```text
uid=1000(ytmsync)
gid=1000(ytmsync)
```

---

### 10.3 Verify mounts

```bash
docker inspect ytm-sync \
  --format '{{json .Mounts}}'
```

Confirm:

```text
/music       RW=false
/downloads   RW=false
/config      RW=true
```

---

# Phase 11 — Functional Regression Test

## Priority: CRITICAL

Security is only half the release.

Run the actual application.

### Test A — Initial startup

```text
Container starts
        ↓
Database initializes
        ↓
API responds
        ↓
UI loads
```

---

### Test B — YouTube Music authentication

```text
Paste headers
      ↓
Connect
      ↓
Connection succeeds
      ↓
Restart
      ↓
Authentication remains stored
```

---

### Test C — Library scan

Use a test music directory containing:

```text
MP3
FLAC
M4A
OGG
WMA
```

Verify metadata extraction.

---

### Test D — Matching

Verify:

```text
Exact match
Strong match
Weak match
Missing
```

and make sure weak/missing tracks aren't incorrectly uploaded.

Your recent commits specifically changed this behavior toward requiring verified title/artist/album matches. That's good—regression-test it.

---

### Test E — Upload

Test:

```text
one track
multiple tracks
failed upload
retry
successful upload
duplicate upload prevention
```

---

### Test F — Queue recovery

This is especially important because queue recovery is a core feature.

Start an upload.

Then:

```bash
docker compose restart
```

Verify:

```text
queue survives
job isn't duplicated
job resumes
status becomes correct
```

---

### Test G — Playlist sync

Test:

```text
playlist import
playlist inspection
missing track detection
download
metadata verification
upload
```

---

### Test H — Metadata replacement

Verify:

```text
existing file
replacement download
metadata update
verification
final placement
```

and make sure no unexpected files are created outside the configured music/download roots.

---

# Phase 12 — Traefik Deployment Test

## Priority: CRITICAL for your setup

Deploy:

```text
Cloudflare
    ↓
Traefik
    ↓
YTM Sync
```

### Test 12.1 — HTTP/HTTPS

Ensure users don't reach the backend directly.

### Test 12.2 — Browser

Open:

```text
https://YOUR-YTM-SYNC-DOMAIN
```

Verify UI loads.

### Test 12.3 — API

Verify:

```text
GET /api/status
```

without token:

```text
401
```

with token:

```text
200
```

### Test 12.4 — CORS

Allowed domain:

```text
Access-Control-Allow-Origin: your-domain
```

Unknown domain:

```text
NO access-control-allow-origin
```

### Test 12.5 — Forwarded headers

Verify scheme/IP information is correct when passing:

```text
Cloudflare → Traefik → YTM Sync
```

---

# Phase 13 — Cloudflare Test

## Priority: HIGH

Test through the actual Cloudflare endpoint.

Verify:

```text
HTTPS
API authentication
CORS
WebSocket/live updates if used
large requests
playlist operations
long-running downloads
```

Pay special attention to timeout behavior.

A playlist/download operation that works locally but dies behind Cloudflare/Traefik is a production blocker.

---

# Phase 14 — Backup / Restore Test

## Priority: HIGH

Don't just test backup creation.

Test restoration.

### Backup

```bash
tar -czvf ytm_sync_backup_$(date +%Y%m%d).tar.gz ./config
```

The README already identifies `/config` as the important persistent data directory.

### Restore test

Create a clean test installation.

Restore:

```text
database
authentication
history
configuration
```

Start YTM Sync.

Verify:

```text
database intact
queue intact
YTM authentication intact
API key intact
settings intact
```

This turns "we have backups" into **"we know backups work."**

---

# Phase 15 — Documentation Completion

## Priority: MEDIUM

Update README to clearly document:

### Installation

```text
clone
.env
docker compose
```

### API authentication

Explain:

```text
YTM Sync automatically generates an API key
```

and where it is stored.

### Reverse proxy

Add:

```text
Cloudflare
   ↓
Traefik
   ↓
YTM Sync
```

instructions.

### Environment variables

Document:

```text
PORT
CONFIG_PATH
MUSIC_PATH
DOWNLOADS_PATH
LOG_LEVEL
TZ
YTM_SYNC_API_KEY
ALLOWED_ORIGINS
FORWARDED_ALLOW_IPS
```

The `.env.example` already contains most of the important configuration.

### Security model

Add a short section:

```text
API requires Bearer authentication.
Music/download mounts are read-only in Docker.
Filesystem access is restricted to configured roots.
External URLs are restricted and validated.
```

---

# Phase 16 — Release Candidate

Once everything above passes:

### 16.1 Version bump

Set:

```text
1.0.0
```

where the application currently defines its version.

### 16.2 Build

```bash
flutter build web --release
```

Then synchronize:

```bash
rsync -av --delete app/build/web/ backend/web_dist/
```

### 16.3 Run all tests

```bash
flutter analyze
flutter test
```

Backend:

```bash
pytest
```

Docker:

```bash
docker compose build
docker compose up -d
```

---

# Phase 17 — Release Candidate Checklist

Don't tag until every box is checked.

```text
[x] main clean
[x] latest commit verified
[x] API authentication tested
[x] invalid authentication tested
[x] API route enumeration completed
[x] filesystem traversal tested
[x] symlink traversal tested
[x] SSRF tested
[x] YouTube URL validation tested
[x] no secrets in repository
[x] no secrets in Flutter bundle
[x] Docker runs as UID 1000
[x] music mount read-only
[x] downloads mount read-only
[x] /config writable
[x] docs disabled
[x] CORS tested
[x] forwarded proxy configuration tested
[x] Flutter analyze passes
[x] Flutter tests pass
[x] backend tests pass
[x] Docker build passes
[x] fresh install works
[x] restart works
[x] queue recovery works
[x] YTM authentication works
[x] scanning works
[x] matching works
[x] uploading works
[x] playlist sync works
[x] metadata replacement works
[x] backup works
[x] restore tested
[x] Traefik works
[x] Cloudflare works
[x] direct WAN access blocked
[x] README updated
[x] .env.example updated
```

---

# Phase 18 — Tag v1.0.0

Only after the complete checklist passes:

```bash
git checkout main
git pull
git tag -a v1.0.0 -m "YTM Sync v1.0.0"
git push origin v1.0.0
```

Your existing workflow is configured to build/publish tagged releases as well as `main`.

---

# Phase 19 — Production Deployment

Use the released image rather than building random working-tree versions.

Your Compose currently uses:

```text
ghcr.io/jakej985-rgb/ytmusic_sync:latest
```

For your **actual server**, I'd eventually prefer a versioned image:

```text
ghcr.io/jakej985-rgb/ytmusic_sync:1.0.0
```

rather than `latest`.

That gives you:

```text
v1.0.0
   ↓
known image
   ↓
production
```

instead of:

```text
latest
   ↓
whatever was most recently pushed
```

You can still keep `latest` for convenience.

---

# Phase 20 — Post-Release Monitoring

For the first few days, monitor:

```bash
docker compose logs -f ytm-sync
```

Watch specifically for:

```text
ERROR
Traceback
permission denied
401
403
queue failures
database errors
yt-dlp failures
filesystem validation errors
```

Don't automatically loosen security when a path gets rejected.

If something legitimately needs access:

```text
identify exact operation
        ↓
identify exact root
        ↓
make narrow change
        ↓
add regression test
```

---

# Recommended Agent Implementation Order

If you're going to hand this to your coding agent, **don't give it all 20 phases at once as one giant "rewrite."**

Give it these batches:

### Agent Task 1 — Configuration

> Implement Phase 1 and update `.env.example` and README. Do not modify unrelated functionality. Add tests for forwarded-header configuration.

### Agent Task 2 — Security Audit

> Perform the Phase 4–7 filesystem, network, authentication, and secret audit. Fix only concrete issues found. Add regression tests for every fix.

### Agent Task 3 — CI

> Verify and harden Phase 9 CI. Ensure Flutter analyze/test/build, backend pytest, and Docker build all run before image publishing. Do not change application functionality.

### Agent Task 4 — Documentation

> Complete Phase 15 documentation. Document API authentication, generated API key retrieval, CORS, reverse proxy configuration, environment variables, Docker security, and backup/restore.

### Agent Task 5 — Release QA

> Implement automated regression tests covering the Phase 11 security and functional matrix. Do not redesign application architecture.

Then **you** perform the actual:

```text
Docker deployment
        ↓
Traefik
        ↓
Cloudflare
        ↓
real YouTube Music account
        ↓
real music library
        ↓
backup/restore
```

---

## The important part

**Don't let the agent turn this into another month-long security project.**

The current repository has already made the major security transition. The latest security changes are present in `main`, including the path validation and proxy configuration work.

The remaining job is:

**Harden → Test → Deploy → Verify → Tag → Ship.**

I would consider **Phase 12–14 the real release gate** for your particular setup. Passing unit/security tests is great, but the final proof is that **YTM Sync works through your actual Traefik + Cloudflare deployment with your actual YT Music authentication and music library without breaking the security boundaries.**
