Absolutely. Based on the second audit, I’d make this the **final security-hardening plan** rather than starting another large redesign.

## YTM Sync — Remaining Work Plan

### Goal

Get the repo from:

> **“P0 security mostly implemented”**

to:

> **“Security controls verified, tested, and ready for controlled external deployment.”**

---

# Phase 1 — Complete the filesystem security boundary

**Priority: 🔴 Critical**

The biggest remaining uncertainty is whether **every endpoint that accepts a filesystem path actually goes through `validate_fs_path()`**.

### 1.1 Audit every path input

Search the entire backend for:

```text
Path(
Path.resolve(
os.path
open(
shutil
destination_dir
folder
music_folder
file_path
directory
```

Create a list of every endpoint/function that can:

* read a filesystem path
* write a filesystem path
* create directories
* delete files
* move files
* browse directories
* scan directories

### 1.2 Centralize validation

Everything should use:

```python
validate_fs_path(...)
```

from:

```text
backend/ytm_service/security.py
```

No endpoint should implement its own path-security logic.

### 1.3 Define explicit root permissions

Separate roots by purpose.

For example:

```text
/music
/downloads
```

Then determine whether each operation needs:

```text
READ
WRITE
CREATE
DELETE
```

Don't simply say:

> "This path is inside an approved root, therefore everything is allowed."

### 1.4 Symlink testing

Test:

```text
/music/legit
/music/link-to-config
/music/link-to-etc
/downloads/link-to-config
```

and verify the API cannot escape the approved root.

### 1.5 Path traversal testing

Test:

```text
..
../config
/music/../config
/music/../../etc
/downloads/../config
```

Also test URL-encoded traversal if paths can arrive through query parameters.

---

# Phase 2 — Audit every API endpoint

**Priority: 🔴 Critical**

Don't just verify the authentication middleware exists.

Build an endpoint inventory.

For every:

```text
/api/*
```

record:

| Endpoint | Method | Auth | Reads files | Writes files | External API | Destructive |
| -------- | ------ | ---- | ----------- | ------------ | ------------ | ----------- |

Then verify **every API endpoint requires authentication**.

### Expected rule

```text
/health       → public
/api/*        → authenticated
everything else → static frontend
```

### Test automatically

For every protected endpoint:

```text
No credentials → 401
Wrong credentials → 401
Valid credentials → normal response
```

This prevents a future developer from accidentally adding:

```python
@app.post("/api/new-dangerous-operation")
```

without authentication.

---

# Phase 3 — Remove unnecessary authentication complexity

**Priority: 🟠 High**

Currently the security layer supports:

```text
Authorization: Bearer TOKEN
```

and:

```text
X-API-Key: TOKEN
```

I'd make Bearer the official method.

### Recommended

```http
Authorization: Bearer <YTM_SYNC_API_KEY>
```

Then remove `X-API-Key` unless the Flutter application genuinely needs it.

This gives you one authentication path to maintain and test.

---

# Phase 4 — Production configuration hardening

**Priority: 🟠 High**

Review:

```text
.env.example
docker-compose.yml
Dockerfile
DOCKER.md
README.md
```

Make sure secure behavior is the default.

### Required configuration

Document:

```env
YTM_SYNC_API_KEY=
ALLOWED_ORIGINS=
ALLOWED_FS_ROOTS=
```

### API key behavior

Startup should:

1. Use `YTM_SYNC_API_KEY` if supplied.
2. Otherwise load `/config/auth/api_key.txt`.
3. Otherwise generate one.
4. Never print it.
5. Never put it into the Flutter build.
6. Store it with `0600`.

The current implementation already does most of this.

---

# Phase 5 — Deal with the emergency auth bypass

**Priority: 🟠 High**

Current code has:

```text
YTM_SYNC_INSECURE_DISABLE_AUTH
```

I don't want that becoming a normal deployment setting.

### Preferred solution

Remove it entirely.

For tests:

```text
override authentication dependency
```

or use a test-specific environment/configuration.

If you absolutely need the switch for development, make it:

```text
development/test only
```

and make production refuse to start with it enabled.

This prevents:

```env
YTM_SYNC_INSECURE_DISABLE_AUTH=true
```

from accidentally making a public installation completely unauthenticated.

---

# Phase 6 — Finish SSRF protection

**Priority: 🟠 High**

The current URL validator is already substantially better. It checks allowed YouTube domains and resolves DNS, rejecting private/loopback/link-local/etc. addresses.

Now verify that **every external URL flow actually calls it**.

Search for:

```text
yt-dlp
extract_playlist_info
requests
httpx
aiohttp
urllib
urlopen
```

Every user-controlled URL must go through the security layer first.

### Test

Reject:

```text
file://
ftp://
http://localhost
http://127.0.0.1
http://10.x.x.x
http://172.16.x.x
http://192.168.x.x
http://169.254.x.x
http://[::1]
```

Reject:

```text
evil.com
youtube.evil.com
evil-youtube.com
```

Allow legitimate:

```text
youtube.com
www.youtube.com
music.youtube.com
m.youtube.com
youtu.be
```

---

# Phase 7 — API documentation exposure

**Priority: 🟡 Medium**

Determine whether these are accessible:

```text
/docs
/redoc
/openapi.json
```

For an externally exposed installation, I'd disable them by default.

For development:

```env
YTM_SYNC_ENABLE_DOCS=true
```

or simply make them available only in development.

---

# Phase 8 — Flutter security integration

**Priority: 🟠 High**

Now that the backend requires authentication, verify the Flutter app.

Audit:

```text
app/lib/services/api_service.dart
```

Every request should consistently attach:

```http
Authorization: Bearer <token>
```

### Handle

```text
401
```

with a proper:

> Authentication required / API key invalid

screen/dialog rather than dozens of random API errors.

### Important

The API key must **not** be hard-coded.

It must not appear in:

```text
main.dart
.env committed to repo
web_dist/
GitHub Actions artifacts
```

---

# Phase 9 — Add the security regression suite

**Priority: 🔴 Critical**

This is what turns the changes from:

> "We think it's secure."

into:

> "We continuously verify these security boundaries."

Create something like:

```text
backend/tests/test_security.py
```

### Authentication

Test:

```text
missing key
wrong key
empty key
malformed Bearer
valid Bearer
X-API-Key if retained
health without auth
static assets without auth
```

### Filesystem

Test:

```text
allowed root
nested allowed path
..
absolute escape
symlink escape
nonexistent path
null byte
outside root
```

### URL

Test:

```text
valid YouTube
valid YT Music
invalid domain
invalid scheme
embedded credentials
bad port
localhost
private IP
loopback
link-local
IPv6 loopback
```

### Endpoint coverage

Create a test that enumerates protected routes and verifies:

```text
unauthenticated → 401
```

This is particularly valuable.

---

# Phase 10 — Test the Docker security boundary

**Priority: 🔴 Critical**

Run the actual container.

Verify:

```text
/config
    writable

/music
    read-only

/downloads
    read-only
```

Then verify the API cannot use its filesystem browser to reach:

```text
/config
/etc
/root
/tmp
```

Also verify the container user can't simply bypass the API protections.

---

# Phase 11 — Flutter CI

**Priority: 🟠 High**

Add CI steps:

```bash
cd app
flutter pub get
flutter analyze
flutter test
flutter build web
```

Then ideally:

```text
Flutter build
      ↓
backend/web_dist
      ↓
Docker build
```

rather than relying on someone remembering to manually update `web_dist`.

---

# Phase 12 — Dependency and image hardening

**Priority: 🟡 Medium**

Pin Python dependencies.

Instead of:

```text
fastapi>=...
yt-dlp>=...
```

use controlled versions.

Then Dependabot can update them deliberately.

Also consider pinning the Docker base image rather than relying on a moving tag.

---

# Phase 13 — Final integration test

**Priority: 🔴 Critical**

Before calling this release-ready:

```text
Fresh Docker volume
        ↓
docker compose up
        ↓
API generates key
        ↓
Flutter connects
        ↓
Configure YT Music
        ↓
Scan
        ↓
Match
        ↓
Download
        ↓
Upload
        ↓
Delete
        ↓
Restart container
        ↓
Verify DB/auth/queue recovery
```

Then specifically test:

```text
No API key
Wrong API key
Correct API key
Path traversal
Symlink traversal
Bad URL
Valid URL
Container restart
```

---

# Final release gates

I would not call the security work complete until all of these are true:

### 🔐 Security

* [x] All `/api/*` protected
* [x] `/health` public
* [x] CORS restricted
* [x] API key never logged
* [x] API key stored `0600`
* [x] No production auth bypass
* [x] All filesystem inputs validated
* [x] Symlink escape blocked
* [x] Path traversal blocked
* [x] All external URLs validated
* [x] SSRF tests pass
* [x] `/docs` handled appropriately

### 📱 Flutter

* [x] API key configuration works
* [x] Bearer header attached everywhere
* [x] 401 handled cleanly
* [x] No hard-coded secret
* [x] Web build succeeds
* [x] Flutter tests pass
* [x] Flutter analyzer passes

### 🐳 Docker

* [x] `/music` remains read-only
* [x] `/downloads` remains read-only
* [x] `/config` persists
* [x] API key survives restart
* [x] database survives restart
* [x] queue recovery works
* [x] non-root user maintained
* [x] capabilities remain dropped

### 🧪 CI

* [x] Backend tests pass
* [x] Security tests pass
* [x] Flutter tests pass
* [x] Flutter analyze passes
* [x] Flutter build passes
* [x] Docker build passes
