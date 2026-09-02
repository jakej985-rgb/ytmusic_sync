Yes. After looking directly at the repo, I would treat this as a **data-loss bug**, not just a downloader bug.

The current `downloader.py` explicitly builds three possible sources for an upload: the YouTube watch URL, the Music watch URL, and finally a `ytsearch1:` fallback query. That fallback is exactly the kind of behavior that can turn “download my upload” into “download a similarly named public song.”

Your current project also explicitly says local files are not supposed to be modified or deleted, so replacing an artist's local recording with a different recording violates the intended safety model.

# YTM Sync — Upload Integrity & No-Replacement Plan

## Priority: CRITICAL

The goal is stronger than “make the matching better.”

The goal is:

> **It must be impossible for an Upload Locker item to silently become a catalog track or overwrite a local file with unverified audio.**

---

## Phase 1 — Kill the dangerous fallback

### 1.1 Remove `ytsearch1:` from upload downloads

Current behavior effectively permits:

```text
Upload ID
   ↓
youtube.com
   ↓ fail
music.youtube.com
   ↓ fail
ytsearch1:"Artist - Title"
   ↓
some matching public video
```

That last step must never exist for an Upload Locker download.

For:

```python
download_ytm_upload(...)
```

the only legal source should be the **specific upload identity**.

### 1.2 Separate upload downloading from generic YouTube downloading

Create two explicit paths:

```text
download_upload(upload_record)
download_catalog_track(catalog_record)
```

Never:

```text
download_track(...)
```

with ambiguous source semantics.

An upload should carry something like:

```text
source_type = "ytm_upload"
source_id = <YouTube video ID>
```

A catalog track should carry:

```text
source_type = "catalog"
source_id = <catalog/video ID>
```

The downloader should reject a request when the source type is missing or contradictory.

---

# Phase 2 — Make upload identity authoritative

## 2.1 Store the original upload identity

Every Upload Locker record should permanently retain:

```text
upload_video_id
upload_url
source_type
```

Example:

```text
source_type: ytm_upload
upload_video_id: tAXJ0semc4E
upload_url: https://www.youtube.com/watch?v=tAXJ0semc4E
```

The title/artist/album should **never be used as the source identifier**.

Metadata is for matching/display.

The video ID is for identity.

---

## 2.2 Never substitute based on metadata

These are insufficient to prove identity:

```text
Artist
Title
Album
Duration
Filename
Normalized filename
MusicBrainz match
YouTube search result
```

For example:

```text
Local:
Big 8 - Track Name

Upload:
Big 8 - Track Name

Public YouTube:
Big 8 - Track Name
```

Those can all be completely different recordings.

Therefore:

> A metadata match can identify a candidate, but it can never authorize a replacement.

---

# Phase 3 — Fail closed on private uploads

Your current log is actually the situation we want to handle safely:

```text
Private video.
If the owner ... has granted you access, please sign in.
```

The correct behavior should be:

```text
UPLOAD DOWNLOAD FAILED
Reason: private upload unavailable/authentication failure

→ mark download FAILED
→ preserve local file
→ do not search YouTube
→ do not download alternate source
→ do not replace anything
```

Not:

```text
private upload failed
     ↓
try another YouTube source
```

---

# Phase 4 — Add a hard source-integrity gate

Before any downloaded file is allowed anywhere near the destination:

```text
Download
   ↓
Verify
   ↓
PASS → continue
FAIL → delete staging file
```

The verification should check the downloaded video's identity.

At minimum:

```text
expected video ID
actual extracted video ID
```

They must match.

Conceptually:

```python
if actual_video_id != expected_video_id:
    raise DownloadIntegrityError(...)
```

A catalog result such as:

```text
dQw4w9WgXcQ
```

must never be accepted for an upload expecting:

```text
tAXJ0semc4E
```

---

# Phase 5 — Never download directly to the user's music file

This is extremely important because you said it **already replaced local artist files**.

The pipeline needs to become:

```text
YouTube
   ↓
/staging/ytm_<upload_id>.tmp
   ↓
integrity validation
   ↓
metadata validation
   ↓
optional audio fingerprint validation
   ↓
SAFE VERIFIED FILE
   ↓
commit operation
   ↓
destination
```

Never:

```text
YouTube
   ↓
/music/Artist/Song.mp3
```

---

# Phase 6 — Make replacement impossible by default

I would change the write policy to:

### Existing local file + unverified download

```text
BLOCK
```

### Existing local file + verified exact upload

Still:

```text
BLOCK by default
```

unless the application has an explicit replacement operation.

That gives you:

```text
NEW FILE
    ↓
allowed

EXISTING FILE
    ↓
never automatically replace
```

This is much safer for your local artist recordings.

---

# Phase 7 — Add a three-way decision

Instead of simply:

```text
MATCH / NO MATCH
```

use:

```text
SAFE
REVIEW
BLOCKED
```

### SAFE

Exact upload identity verified.

```text
expected ID == actual ID
```

### REVIEW

Metadata looks compatible but identity cannot be cryptographically/authoritatively established.

Example:

```text
same artist
same title
same duration
different/unknown source ID
```

Do not automatically download/replace.

### BLOCKED

Examples:

```text
private upload inaccessible
wrong video ID
ytsearch fallback
source type unknown
download metadata mismatch
destination already exists
audio validation failed
```

---

# Phase 8 — Add audio fingerprint protection

This is the second layer that I strongly recommend because of what happened to your local artists.

Even if the YouTube ID is correct, compare the downloaded audio against the expected upload metadata/audio characteristics.

Use:

```text
duration
codec
sample rate
channels
bitrate
```

and ideally an acoustic fingerprint such as Chromaprint/AcoustID-style fingerprinting where practical.

The goal isn't to prove two encodings are byte-identical.

The goal is to catch:

```text
Expected:
Big 8 - My Track
3:47

Downloaded:
Big 8 - My Track
4:01
```

or an entirely different recording hidden behind similar metadata.

---

# Phase 9 — Protect the original file

Before any replacement operation:

```text
existing file
     ↓
SHA-256
     ↓
database/history
```

Store:

```text
original_path
original_sha256
original_size
original_mtime
replacement_timestamp
replacement_source_id
```

Then even an authorized replacement has an audit trail.

But again, my recommended default is:

> **Don't automatically replace existing local files at all.**

---

# Phase 10 — Database model changes

I would add explicit source/integrity fields to the track record.

Something along these lines:

```text
source_type
source_id
source_url
expected_duration
downloaded_source_id
verified
verification_status
verification_reason
original_file_hash
downloaded_file_hash
replacement_allowed
```

And statuses:

```text
PENDING
DOWNLOADING
VERIFYING
VERIFIED
REVIEW_REQUIRED
FAILED
BLOCKED
```

This also makes your Sync History much more useful.

---

# Phase 11 — Logging needs to become explicit

Instead of:

```text
Downloading audio via ...
```

log:

```text
UPLOAD DOWNLOAD START
source_type=ytm_upload
expected_video_id=tAXJ0semc4E
```

Then:

```text
UPLOAD DOWNLOAD SUCCESS
expected_video_id=tAXJ0semc4E
actual_video_id=tAXJ0semc4E
verification=PASS
```

Or:

```text
UPLOAD DOWNLOAD BLOCKED
expected_video_id=tAXJ0semc4E
actual_video_id=<different ID>
reason=SOURCE_ID_MISMATCH
```

Most importantly, when authentication fails:

```text
UPLOAD DOWNLOAD BLOCKED
reason=PRIVATE_UPLOAD_UNAVAILABLE
fallback_search=DISABLED
replacement=DISABLED
```

That will make the problem obvious instead of hiding it behind retries.

---

# Phase 12 — Add regression tests specifically for your failure

This should become a mandatory test suite.

### Test A — private upload

```text
private upload
↓
authentication failure
↓
download fails
↓
NO catalog search
↓
NO file replacement
```

### Test B — same artist/title

```text
private upload = Artist A / Song X
public YouTube = Artist A / Song X

upload inaccessible
↓
must NOT choose public video
```

### Test C — wrong video ID

Mock:

```text
expected=tAXJ0semc4E
actual=different123
```

Expected:

```text
BLOCKED
```

### Test D — existing local file

```text
destination exists
download verified
```

Expected:

```text
original preserved
automatic replacement blocked
```

### Test E — search fallback

Explicitly assert:

```text
download_ytm_upload()
```

can never invoke:

```text
ytsearch1:
```

---

# Phase 13 — Add a global safety switch

I would add:

```env
YTM_SYNC_ALLOW_AUTOMATIC_REPLACEMENT=false
```

and make `false` the permanent default.

Even better:

```text
Automatic replacement:
OFF
```

in Settings.

Then an explicit manual action could eventually allow:

```text
Replace Existing File
```

with a confirmation showing:

```text
LOCAL FILE
/path/to/song.mp3
SHA256: ...

NEW SOURCE
YouTube Upload ID: tAXJ0semc4E

VERIFICATION
✓ Upload ID matches
✓ Duration matches
✓ Audio validation passed
```

---

# Phase 14 — Repair the files that were already replaced

Before we call this fixed, I would add a recovery procedure.

The database/history should tell us:

```text
original path
replacement date
replacement source
```

Then we can identify suspicious replacements caused by the bad downloader behavior.

Don't let the new code simply rescan those files and assume they're correct.

They need to be treated as:

```text
POSSIBLY CORRUPTED / REPLACED
```

until verified.

---

# Phase 15 — Change the architecture

The final architecture should be:

```text
             ┌───────────────────┐
             │   Upload Locker   │
             └─────────┬─────────┘
                       │
                 exact upload ID
                       │
                       ▼
              ┌─────────────────┐
              │ Upload Downloader│
              └────────┬────────┘
                       │
                 authenticated
                       │
              ┌────────▼────────┐
              │     Staging      │
              └────────┬────────┘
                       │
                 identity check
                       │
                 metadata check
                       │
                 audio check
                       │
                 ┌─────▼─────┐
                 │   SAFE?   │
                 └─────┬─────┘
                    YES│NO
                       │
              ┌────────▼───┐
              │  Commit    │       BLOCK
              │  / Copy    │        │
              └────────────┘        ▼
                                Preserve
                                original
```

## Release gate

I would **not consider YTM Sync ready for release** until these are all true:

```text
[x] Upload downloads never use ytsearch
[x] Upload source identity is stored separately from metadata
[x] Private-upload failure is fail-closed
[x] Download occurs only in staging
[x] Downloaded source ID is verified
[x] Existing files cannot be automatically overwritten
[x] Replacement requires explicit authorization
[x] Original SHA-256 is recorded before replacement
[x] Wrong-source regression tests exist
[x] Same-title/local-artist test exists
[x] Private-upload authentication-failure test exists
[x] Sync history records why a download was accepted/rejected
[x] Previously replaced files are audited
```

### Most important change

The single rule I would put at the top of the codebase is:

> **An Upload Locker item may only be downloaded from its exact authenticated upload source. Metadata may never be used to find a replacement source. If the exact source cannot be downloaded, the operation fails and the existing local file remains untouched.**

That is the rule that would have prevented the local artists' files from being replaced.

The repo already has a dedicated `downloader.py`, `matcher.py`, `database.py`, `metadata_tracker.py`, and `queue_service.py`, so this can be implemented as a contained integrity hardening effort rather than rewriting the whole application.

I would make this **Phase 0 of the final release audit**, ahead of the other release-hardening work in your existing `plan.md`.
