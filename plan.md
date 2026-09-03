Absolutely. Based on the audit of the uploaded repository, I would make this the **completion plan for the Upload Integrity / No-Wrong-Track Replacement fix**, and I would put it **ahead of the final release audit**.

# YTM Sync — Upload Integrity Completion Plan

**Repository:** `jakej985-rgb/ytmusic_sync`
**Goal:** Guarantee that a private YouTube Music Upload Locker track can never be silently replaced by a different YouTube Music/catalog recording.

**Current status:** Implementation substantially present, but **release is BLOCKED** until the verification path and test suite are completed.

---

# 0. RELEASE SAFETY RULE

This is the rule everything below is built around:

> **An Upload Locker track may only be downloaded from its exact authenticated upload source. Metadata may identify a track, but metadata can never authorize a replacement source. If the exact upload cannot be downloaded and verified, the operation must fail closed and the existing local file must remain untouched.**

Therefore:

```text
PRIVATE UPLOAD UNAVAILABLE
        ↓
       FAIL
        ↓
NO SEARCH
NO SUBSTITUTE
NO REPLACEMENT
NO DELETE
NO RENAME
NO OVERWRITE
```

This becomes a **non-negotiable release requirement**.

---

# Phase 1 — Freeze and Backup

## 1.1 Stop production syncing

Before making changes:

```bash
docker compose stop ytm-sync
```

Do **not** test the new code against the real music collection yet.

---

## 1.2 Backup the current database

Back up:

```text
/config/database/ytm_sync.db
```

and the complete config directory.

Example:

```bash
tar -czvf ytm_sync_pre_integrity_fix_$(date +%Y%m%d_%H%M%S).tar.gz ./config
```

---

## 1.3 Preserve the currently affected files

Because some local artist recordings were already replaced, create a separate list of suspicious files.

Do **not** automatically rescan/reconcile them yet.

They should be classified:

```text
POSSIBLY_REPLACED
```

until verified.

---

# Phase 2 — Establish the Current Baseline

## 2.1 Verify the current commit

```bash
git checkout main
git pull
git status
git rev-parse HEAD
```

Record the commit.

---

## 2.2 Create a working branch

Use something like:

```bash
git checkout -b fix/upload-integrity
```

Do not make this work directly on `main`.

---

# Phase 3 — Fix the Downloader Architecture

The repository currently has the critical downloader in:

```text
backend/ytm_service/downloader.py
```

along with:

```text
matcher.py
database.py
metadata_tracker.py
queue_service.py
```

These are the areas that need to remain aligned.

---

## 3.1 Separate Upload and Catalog download APIs

There should be two explicit concepts:

```python
download_ytm_upload(...)
```

and:

```python
download_catalog_track(...)
```

An Upload Locker download must never call generic catalog-resolution logic.

### Upload path

```text
Upload record
    ↓
exact upload ID
    ↓
authenticated YouTube Music
    ↓
download
```

### Catalog path

```text
Catalog record
    ↓
catalog source
    ↓
YouTube/catalog resolver
```

Never cross these paths.

---

# Phase 4 — Remove All Upload Search Fallbacks

Search the entire repository for:

```text
ytsearch
```

and:

```text
fallback_query
```

and:

```text
youtube search
```

and:

```text
search1
```

Every occurrence needs to be classified.

The rule:

```text
UPLOAD + SEARCH = BUG
```

---

## 4.1 Uploads must never execute

```text
ytsearch1:
```

Even if:

```text
private upload unavailable
```

Even if:

```text
title matches perfectly
```

Even if:

```text
artist matches perfectly
```

Even if:

```text
duration matches
```

Even if:

```text
MusicBrainz says it's the same song
```

It must still fail.

---

# Phase 5 — Make Source Identity Mandatory

Every upload record needs an explicit identity:

```text
source_type = ytm_upload
source_id = exact YouTube upload ID
```

For example:

```text
source_type: ytm_upload
source_id: tAXJ0semc4E
```

Do not rely on:

```text
title
artist
album
filename
duration
normalized metadata
```

as source identity.

---

## 5.1 Reject ambiguous records

If an upload record arrives without:

```text
source_type
source_id
```

the system should produce:

```text
BLOCKED
reason=UPLOAD_SOURCE_ID_MISSING
```

rather than guessing.

---

# Phase 6 — Fix the ytmusicapi Bypass

This is the **highest-priority code fix from the audit**.

Currently there are effectively two download routes:

```text
ytmusicapi
```

and:

```text
yt-dlp
```

The yt-dlp route has source verification.

The direct ytmusicapi route can return success before the same authoritative identity verification has been applied.

That creates an integrity gap.

---

## 6.1 Create one shared verification function

For example:

```python
verify_downloaded_upload(
    staged_file,
    expected_source_id,
    expected_metadata
)
```

Every download mechanism must call it.

---

## 6.2 Required pipeline

Change:

```text
ytmusicapi
 ↓
success
```

to:

```text
ytmusicapi
 ↓
staging
 ↓
source identity verification
 ↓
metadata verification
 ↓
audio integrity verification
 ↓
verified
```

And:

```text
yt-dlp
 ↓
staging
 ↓
source identity verification
 ↓
metadata verification
 ↓
audio integrity verification
 ↓
verified
```

The two paths must converge before either can return success.

---

# Phase 7 — Source-ID Verification

For every upload:

```text
EXPECTED
tAXJ0semc4E
```

must be compared with:

```text
ACTUAL
tAXJ0semc4E
```

Result:

```text
MATCH → continue
MISMATCH → BLOCK
```

Never:

```text
MISMATCH → try another source
```

---

## 7.1 Add explicit failure reason

Use something like:

```text
SOURCE_ID_MISMATCH
```

Log:

```text
expected_source_id=...
actual_source_id=...
```

but **never expose cookies or authentication credentials**.

---

# Phase 8 — Staging Must Remain Mandatory

The current staging architecture is good and should be retained.

The sequence must be:

```text
YouTube
 ↓
/config/staging
 ↓
verify
 ↓
commit
```

Never:

```text
YouTube
 ↓
/music/artist/song.mp3
```

---

# Phase 9 — Strengthen Commit Protection

The commit function already has overwrite protection.

Make this the permanent default:

```python
allow_overwrite=False
```

and:

```python
allow_automatic_replacement=False
```

---

## 9.1 Existing destination

If:

```text
/music/Artist/Song.mp3
```

already exists:

```text
BLOCK
```

unless the user explicitly performs a replacement operation.

---

## 9.2 Automatic sync cannot replace

The normal sync worker should never be able to do:

```text
existing file
 ↓
replace
```

Its only legal behaviors are:

```text
existing file + same verified content
        ↓
skip

existing file + different content
        ↓
block/review
```

---

# Phase 10 — Add Pre-Replacement Hashing

Before any explicitly authorized replacement:

```text
existing file
 ↓
SHA-256
 ↓
database/audit record
 ↓
replacement
```

Store:

```text
original_path
original_sha256
original_size
original_mtime
replacement_source_id
replacement_timestamp
```

This gives us a recovery trail.

---

# Phase 11 — Metadata Verification

After downloading, inspect:

```text
title
artist
album
duration
```

Compare against the Upload Locker record.

But remember:

> Metadata verification is a secondary check, not source identity.

So:

```text
source ID = required
metadata = additional safety
```

Not:

```text
metadata = source identity
```

---

# Phase 12 — Audio Verification

Run:

```text
duration
sample rate
channels
codec
file size
```

against expected values where available.

Flag suspicious differences.

Example:

```text
Expected: 3:47
Downloaded: 4:12
```

→

```text
BLOCKED
AUDIO_METADATA_MISMATCH
```

---

## 12.1 Optional stronger protection

Add an acoustic fingerprint where practical.

This isn't required to establish the YouTube upload identity, but it provides another defense against accidentally accepting a different recording.

---

# Phase 13 — Formal Download State Machine

Do not allow a simple:

```text
downloaded = true
```

state.

Use:

```text
PENDING
   ↓
DOWNLOADING
   ↓
VERIFYING
   ↓
VERIFIED
   ↓
COMMITTED
```

Failure paths:

```text
DOWNLOADING
    ↓
FAILED
```

or:

```text
VERIFYING
    ↓
BLOCKED
```

or:

```text
VERIFYING
    ↓
REVIEW_REQUIRED
```

---

# Phase 14 — Define Block Reasons

Standardize them.

At minimum:

```text
UPLOAD_SOURCE_ID_MISSING
PRIVATE_UPLOAD_UNAVAILABLE
AUTHENTICATION_FAILED
SOURCE_ID_MISMATCH
METADATA_MISMATCH
AUDIO_DURATION_MISMATCH
AUDIO_INTEGRITY_FAILED
DESTINATION_EXISTS
AUTOMATIC_REPLACEMENT_DISABLED
CATALOG_FALLBACK_BLOCKED
DOWNLOAD_FAILED
```

This makes troubleshooting much easier.

---

# Phase 15 — Logging Requirements

For every upload:

### Start

```text
UPLOAD DOWNLOAD START
source_type=ytm_upload
source_id=XXXXXXXX
```

### Success

```text
UPLOAD DOWNLOAD VERIFIED
source_id=XXXXXXXX
verification=PASS
```

### Private failure

```text
UPLOAD DOWNLOAD BLOCKED
source_id=XXXXXXXX
reason=PRIVATE_UPLOAD_UNAVAILABLE
catalog_fallback=DISABLED
```

### Wrong source

```text
UPLOAD DOWNLOAD BLOCKED
expected_source_id=XXXXXXXX
actual_source_id=YYYYYYYY
reason=SOURCE_ID_MISMATCH
```

### Existing file

```text
COMMIT BLOCKED
destination_exists=true
automatic_replacement=false
```

Never log:

```text
cookies
Authorization headers
session tokens
```

---

# Phase 16 — Regression Test Suite

This is where the current repository needs work because the audit found that pytest currently fails during collection because of the missing `aiosqlite` dependency.

First fix the test environment.

Then run:

```bash
pytest -q tests/test_upload_integrity.py
```

---

# Phase 17 — Mandatory Test Cases

## Test 1 — Private upload inaccessible

```text
Upload ID = ABC123
Authentication = fails
```

Expected:

```text
FAILED/BLOCKED
```

and:

```text
ytsearch = NEVER CALLED
```

---

## Test 2 — Same artist/title public video exists

This is the **critical artist-protection test**.

```text
Private upload:
Artist X - Track A
ID = PRIVATE123

Public video:
Artist X - Track A
ID = PUBLIC456
```

Private upload fails.

Expected:

```text
BLOCKED
```

NOT:

```text
PUBLIC456 downloaded
```

---

# Test 3 — Wrong source ID

Expected:

```text
expected=PRIVATE123
actual=PUBLIC456

→ SOURCE_ID_MISMATCH
→ BLOCK
```

---

# Test 4 — Existing local file

```text
/music/Artist/Track.mp3
```

already exists.

Sync runs.

Expected:

```text
original SHA256 unchanged
```

---

# Test 5 — ytmusicapi path

Mock ytmusicapi returning an incorrect source.

Expected:

```text
verification fails
```

This test specifically closes the audit finding.

---

# Test 6 — yt-dlp path

Mock yt-dlp returning the wrong video.

Expected:

```text
SOURCE_ID_MISMATCH
```

---

# Test 7 — No `ytsearch` execution

Patch/mock the search invocation.

Run:

```text
download_ytm_upload(...)
```

Assert:

```text
ytsearch1 was never invoked
```

---

# Test 8 — Catalog tracks still work

This is important.

We don't want to break legitimate catalog downloads.

Verify:

```text
source_type=catalog
```

can still use its intended resolver/search behavior.

So:

```text
Upload → strict
Catalog → normal
```

---

# Test 9 — Staging failure

Force:

```text
staging write failure
```

Expected:

```text
destination unchanged
```

---

# Test 10 — Verification failure

Download a bad file.

Expected:

```text
staging file removed/quarantined
destination unchanged
```

---

# Phase 18 — Run Full Test Suite

Once the targeted tests pass:

```bash
pytest -q
```

Do not proceed if:

```text
collection errors
```

or:

```text
test failures
```

remain.

Release requirement:

```text
0 collection errors
0 failures
```

---

# Phase 19 — Static Source Audit

Search the complete repository for dangerous operations:

```bash
grep -R "ytsearch" backend/
grep -R "unlink" backend/
grep -R "os.remove" backend/
grep -R "os.replace" backend/
grep -R "shutil.move" backend/
grep -R "shutil.copy" backend/
grep -R "rename" backend/
grep -R "overwrite" backend/
```

Every occurrence must be reviewed.

The question for every filesystem mutation is:

> Can this operation ever affect a user's existing music file without an explicit verified authorization?

If yes:

**BLOCK RELEASE.**

---

# Phase 20 — Real Test Library

Create a completely isolated test library:

```text
/test-music/
    Artist A/
    Artist B/
    Local Artist/
```

Put intentionally similar recordings inside.

Example:

```text
Artist A - Song.mp3
Local Artist - Song.mp3
Local Artist - Song (Live).mp3
```

Also create files with identical:

```text
artist
title
album
```

but different audio.

This simulates the exact problem that occurred.

---

# Phase 21 — Real YouTube Test Accounts/Tracks

Do not use your primary artist collection initially.

Test with:

```text
public catalog track
private upload
duplicate title
duplicate artist/title
different recording
```

Test the following:

### Case A

Exact upload available:

```text
PASS
```

### Case B

Exact upload unavailable:

```text
BLOCK
```

### Case C

Same-title public track exists:

```text
BLOCK
```

### Case D

Wrong source returned:

```text
BLOCK
```

### Case E

Existing local file:

```text
PRESERVE
```

---

# Phase 22 — Verify No File Changes

Before every test:

```bash
sha256sum /test-music/**/*
```

After every test:

```bash
sha256sum /test-music/**/*
```

Compare the results.

For all failed/blocked downloads:

```text
BEFORE HASH == AFTER HASH
```

must be true.

This is the most important physical safety validation.

---

# Phase 23 — Recovery Audit for Already-Replaced Files

Once the new system is proven safe, investigate the files affected by the previous bug.

For each:

```text
local path
database record
sync history
previous source ID
timestamp
SHA-256
```

Determine:

```text
VERIFIED
POSSIBLY WRONG
UNKNOWN
```

Do **not** automatically overwrite questionable files during recovery.

---

# Phase 24 — UI Safety

The UI should make blocked downloads obvious.

Instead of:

```text
Download failed
```

show:

```text
Upload unavailable

This track was NOT replaced because the exact
YouTube Music upload could not be verified.

Reason:
Private upload authentication failed.

Local file:
PRESERVED
```

For source mismatch:

```text
Download blocked

Expected upload:
XXXXXXXX

Received:
YYYYYYYY

Local file:
PRESERVED
```

This is especially important because the application is supposed to protect people's collections.

---

# Phase 25 — Sync Queue Safety

The queue must understand:

```text
BLOCKED
```

as a terminal state until manually retried.

It must **not** automatically retry using another source.

Bad:

```text
upload failed
 ↓
catalog fallback
```

Good:

```text
upload failed
 ↓
BLOCKED
 ↓
wait for authentication/user action
```

---

# Phase 26 — Database Migration

If the schema needs additional fields, add a proper migration.

Potential fields:

```text
source_type
source_id
verification_status
verification_reason
verified_source_id
original_sha256
replacement_timestamp
```

Do not destroy existing sync history.

Run migration tests against a copy of a real database.

---

# Phase 27 — Docker Verification

Build clean:

```bash
docker compose build --no-cache
```

Start:

```bash
docker compose up -d
```

Check:

```bash
docker compose ps
docker compose logs -f ytm-sync
```

Then verify:

```text
yt-dlp
ffmpeg
ytmusicapi
authentication
database
staging
```

all work inside the actual container.

---

# Phase 28 — Production Dry Run

Before enabling writes:

```text
DOWNLOAD
   ↓
VERIFY
   ↓
REPORT
```

but:

```text
NO COMMIT
```

Run against the real library in **dry-run mode**.

The application should tell us:

```text
23 uploads
19 verified
2 blocked
2 review
0 replacements
```

Nothing should change on disk.

---

# Phase 29 — Controlled Production Test

Pick **one expendable test track**.

Run:

```text
1 upload
 ↓
download
 ↓
verify
 ↓
commit
```

Verify:

```text
source ID
metadata
duration
file hash
destination
database
sync history
```

Then stop the sync worker.

---

# Phase 30 — Final Production Validation

Only after the one-track test succeeds:

```text
5 tracks
 ↓
verify
 ↓
10 tracks
 ↓
verify
 ↓
full collection
```

Do not jump immediately from one test to the entire artist library.

---

# Phase 31 — Final Release Gate

The release cannot be marked ready until every item below is true.

```text
UPLOAD INTEGRITY
[x] Exact upload ID stored
[x] Upload source type explicit
[x] Missing source ID blocks download
[x] Private upload failure blocks download
[x] Upload cannot invoke ytsearch
[x] Catalog fallback impossible for uploads
[x] ytmusicapi path verifies source identity
[x] yt-dlp path verifies source identity
[x] Metadata verification works
[x] Audio integrity verification works

FILE SAFETY
[x] Downloads use staging
[x] Existing files protected
[x] Automatic replacement disabled
[x] Failed downloads cannot reach destination
[x] Verification failures cannot reach destination
[x] Original hashes recorded before authorized replacement
[x] No unapproved delete
[x] No unapproved rename
[x] No unapproved overwrite

QUEUE SAFETY
[x] Failed upload becomes BLOCKED
[x] Retry does not change source
[x] Retry cannot become catalog search
[x] Authentication failure waits for correction

TESTING
[x] aiosqlite/dependencies fixed
[x] upload integrity tests collect
[x] upload integrity tests pass
[x] ytmusicapi verification test passes
[x] wrong-source test passes
[x] same-title artist test passes
[x] existing-file test passes
[x] no-search test passes
[x] full pytest suite passes
[x] Docker test passes
[x] dry-run passes

PRODUCTION
[x] isolated test library passes
[x] one-track real test passes
[x] five-track test passes
[x] real collection dry-run passes
[x] no unexpected file modifications
```

---

# Recommended implementation order

Don't tackle all 31 phases simultaneously.

Use this exact order:

```text
1. Freeze production
        ↓
2. Fix aiosqlite/test environment
        ↓
3. Fix ytmusicapi verification
        ↓
4. Make source identity mandatory
        ↓
5. Eliminate upload search/fallback paths
        ↓
6. Harden commit/overwrite protection
        ↓
7. Add/fix regression tests
        ↓
8. Run targeted tests
        ↓
9. Run entire test suite
        ↓
10. Static filesystem mutation audit
        ↓
11. Docker rebuild
        ↓
12. Isolated test library
        ↓
13. Real one-track test
        ↓
14. Small production batch
        ↓
15. Full dry-run
        ↓
16. Final release audit
        ↓
17. Release
```

## 🚨 The three hard blockers

If we're prioritizing, these are the three things I would **not compromise on**:

### BLOCKER #1

**The ytmusicapi direct-download path must go through the same source verification as yt-dlp.**

### BLOCKER #2

**An Upload Locker failure must never invoke `ytsearch`, metadata search, or catalog substitution.**

### BLOCKER #3

**An existing local file must remain byte-for-byte unchanged when an upload download fails, is mismatched, or cannot be verified.**

If those three are proven with automated tests, the specific failure that replaced your local artists' recordings becomes dramatically harder to reintroduce.

The existing repository already has much of the foundation for this—particularly staging, source tracking, overwrite controls, and integrity tests—so I would **finish and verify this implementation rather than redesign the whole application**.
