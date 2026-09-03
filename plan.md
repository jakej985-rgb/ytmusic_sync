Ah — yes, that is a **different architecture**, and I understand what you mean now.

You want **ytmusic_sync to watch an existing YouTube Music playlist**, and maintain a **1:1 replica of that playlist using only songs that are actually in your local upload locker**.

### Example

You have a YT Music playlist:

**`My Local Artists`**

```text
1. Artist A - Song 1
2. Artist B - Song 2
3. Artist C - Song 3
4. Artist D - Song 4
```

Your locker contains uploads for only:

```text
Artist A - Song 1
Artist B - Song 2
Artist D - Song 4
```

The generated/replicated playlist should be:

```text
1. Artist A - Song 1
2. Artist B - Song 2
3. Artist D - Song 4
```

**Artist C is excluded because it isn't an uploaded locker track.**

And when the source playlist changes:

```text
Source YT Music playlist
        ↓
      WATCH
        ↓
Read playlist order
        ↓
Match against LOCKER UPLOADS
        ↓
Exclude anything not uploaded
        ↓
Reconcile destination playlist
        ↓
1:1 ordered locker-only copy
```

That's the feature I'd build.

---

# Detailed implementation plan

## 1. Define the two playlists

There should be a clear distinction between:

### Source playlist

The playlist you want to watch.

Example:

```text
Source:
"406 Playlist"
```

### Replicated playlist

The playlist ytmusic_sync maintains.

Example:

```text
Destination:
"406 Playlist - Locker"
```

Configuration:

```text
Source playlist:
406 Playlist

Destination playlist:
406 Playlist - Locker

Mode:
LOCKER ONLY

Enabled:
YES
```

---

# 2. The locker is the authority

This is the most important part.

The source playlist determines:

> **Which songs and what order?**

The locker determines:

> **Which songs are allowed to exist in the replicated playlist?**

So:

```text
SOURCE PLAYLIST
      │
      │ order + desired tracks
      ▼
MATCH ENGINE
      ▲
      │
      │ allowed tracks
      │
LOCKER DATABASE
```

The source playlist **cannot authorize a track by itself**.

If the track isn't in the locker, it doesn't make the destination playlist.

---

# 3. Don't match by title alone

Because of the wrong local-artist issue you've been fighting, this needs very strong identity matching.

Bad:

```text
artist + title
```

Better:

```text
YT Music video ID
```

Best for your system would be something like:

```text
LockerUpload
├── upload_id
├── ytmusic_video_id
├── ytmusic_track_id
├── artist
├── title
├── album
├── duration
├── file_path
└── upload_status
```

The matching engine should prefer stable IDs.

---

# 4. Source playlist scanner

Create a service:

```text
PlaylistWatcher
```

It periodically retrieves:

```text
source playlist
```

and produces:

```text
SourcePlaylistSnapshot
```

Example:

```text
Playlist:
406 Playlist

Revision:
abc123

Tracks:

1 → video_id=A
2 → video_id=B
3 → video_id=C
4 → video_id=D
```

Store the snapshot so you know what changed.

---

# 5. Locker lookup

Then query only successful uploads.

Conceptually:

```sql
SELECT *
FROM uploads
WHERE upload_status = 'SUCCESS'
AND ytmusic_video_id IS NOT NULL;
```

Build a lookup:

```text
video_id
   ↓
locker upload
```

Example:

```text
A → Locker Song A
B → Locker Song B
D → Locker Song D
```

---

# 6. Build the replicated playlist

Now walk the source playlist **in its exact order**.

Source:

```text
1 A
2 B
3 C
4 D
5 E
```

Locker:

```text
A
B
D
```

Generated destination:

```text
1 A
2 B
3 D
```

So the algorithm is essentially:

```text
for track in source_playlist:

    if track exists in locker:
        add track to desired_playlist

    else:
        skip
```

This gives you the important behavior:

### Source playlist controls order

### Locker controls eligibility

---

# 7. Destination playlist reconciliation

This should **not simply append everything**.

You specifically want a **1:1 copy**.

Therefore the destination playlist needs reconciliation.

Example:

### Current destination

```text
A
B
D
```

### Source changes

```text
B
A
D
```

The destination should become:

```text
B
A
D
```

not:

```text
A
B
D
B
A
D
```

---

# 8. Additions

Source:

```text
A
B
C
D
```

Locker:

```text
A
B
D
```

Destination:

```text
A
B
D
```

Then you upload C into the locker.

Locker becomes:

```text
A
B
C
D
```

Watcher detects it.

Destination becomes:

```text
A
B
C
D
```

---

# 9. Removals

This is equally important.

Source:

```text
A
B
D
```

Locker:

```text
A
B
C
D
```

Destination:

```text
A
B
D
```

C stays excluded.

If B is removed from the source:

```text
A
D
```

Destination must become:

```text
A
D
```

The system removes B from the replicated playlist.

---

# 10. Reordering

If source changes:

```text
A
B
C
D
```

to:

```text
D
B
A
C
```

and all four are in the locker:

Destination must become:

```text
D
B
A
C
```

Therefore the sync engine needs to compare **ordered lists**, not just sets.

---

# 11. Locker-only guarantee

I'd make this an explicit invariant:

```text
DESTINATION_TRACKS
    ⊆
LOCKER_UPLOADS
```

And:

```text
DESTINATION_ORDER
    =
SOURCE_ORDER
    filtered by LOCKER_UPLOADS
```

That's essentially the mathematical definition of what you're asking for.

---

# 12. What happens when a song isn't uploaded?

Don't create it.

Don't search YouTube Music for an alternative.

Don't download it.

Don't fuzzy-match another artist.

Don't substitute another version.

Example:

```text
Source:
Local Artist - My Song

Locker:
Local Artist - My Song ❌
```

Result:

```text
Destination:
[excluded]
```

Log:

```text
INFO Source track excluded:
Local Artist - My Song

Reason:
Not present in upload locker
```

This is particularly important for your local-artist problem.

---

# 13. What happens after an upload?

This is where the system gets useful.

Suppose the source playlist already contains:

```text
A
B
C
D
```

Locker:

```text
A
B
D
```

Destination:

```text
A
B
D
```

Later your normal sync process uploads C.

The playlist watcher sees:

```text
C now exists in locker
```

and destination becomes:

```text
A
B
C
D
```

**without requiring you to manually edit the playlist.**

---

# 14. Two triggers

I'd support two ways to trigger reconciliation.

### A. Playlist change

```text
Source playlist changed
       ↓
Reconcile
```

### B. Locker change

```text
New upload completed
       ↓
Reconcile affected playlists
```

That means you don't have to wait for the playlist polling interval after an upload.

---

# 15. Efficient reconciliation

Don't rebuild every playlist every time.

Track dependencies:

```text
Playlist A
 ├── Song A
 ├── Song B
 └── Song C

Playlist B
 ├── Song C
 └── Song D
```

If Song C gets uploaded:

```text
Song C
 ↓
Playlist A affected
Playlist B affected
```

Only those need reconciliation.

---

# 16. Configuration model

I'd use something like:

```text
ReplicatedPlaylist
├── id
├── source_playlist_id
├── source_playlist_name
├── destination_playlist_id
├── destination_playlist_name
├── enabled
├── sync_interval
├── last_source_revision
├── last_sync_at
└── last_sync_status
```

Example:

```text
Source:
406 Lyricists

Destination:
406 Lyricists - Locker

Mode:
Locker Only

Status:
Watching

Last Sync:
2026-09-02 20:42
```

---

# 17. Multiple replicated playlists

Design it so eventually you can have:

```text
YT Music Playlist          Locker Replica

Local Artists       →      Local Artists - Locker

406 Lyricists        →      406 Lyricists - Locker

Favorites            →      Favorites - Locker
```

Each operates independently.

---

# 18. Destination playlist ownership

I'd strongly recommend that the application clearly marks playlists it manages.

For example:

```text
managed_by = ytmusic_sync
replica_mode = locker_only
source_playlist_id = XXXXX
```

That prevents the application from accidentally treating an unrelated playlist as one it should control.

---

# 19. Never modify the source playlist

The source playlist is **read-only from ytmusic_sync's perspective**.

The application should never:

* remove songs
* add songs
* reorder songs
* rename it
* modify it

Only:

```text
READ SOURCE
     ↓
CALCULATE DESIRED STATE
     ↓
UPDATE DESTINATION
```

---

# 20. Destination playlist reconciliation states

Track operations:

```text
ADD
REMOVE
MOVE
NOOP
```

Example:

```text
Source:
A B D E

Locker:
A B C D

Destination:
A B C

Reconciliation:

ADD D
REMOVE C
```

Final:

```text
A B D
```

---

# 21. Important: duplicate source tracks

YouTube Music playlists can potentially contain repeated tracks.

You need to decide whether the replica preserves them.

For a true **1:1 playlist replica**, I recommend:

> **Preserve duplicate occurrences and their positions.**

Example:

```text
Source:
A
B
A
C
```

Locker:

```text
A
B
C
```

Destination:

```text
A
B
A
C
```

That is much closer to a true filtered copy.

---

# 22. Important: playlist title changes

I would **not automatically rename the destination based on the source** unless configured.

Instead:

```text
Source:
406 Lyricists

Destination:
406 Lyricists - Locker
```

The destination name is user-controlled.

---

# 23. UI

I'd add:

## Playlist Replication

```text
┌───────────────────────────────────┐
│ Playlist Replication              │
│                                   │
│ [✓] Enabled                       │
│                                   │
│ Source Playlist                   │
│ [ 406 Lyricists              ▼ ]  │
│                                   │
│ Locker Playlist                   │
│ [ 406 Lyricists - Locker     ▼ ]  │
│                                   │
│ Mode                              │
│ [ Locker Only                 ▼ ] │
│                                   │
│ Status: ● Watching                │
│                                   │
│ Source Tracks:        42          │
│ Locker Matches:       37          │
│ Excluded:              5          │
│ Destination:          37          │
│                                   │
│ [ Sync Now ]                      │
└───────────────────────────────────┘
```

---

# 24. Show excluded tracks

This would be extremely useful.

```text
Excluded from replica
────────────────────────────

5 tracks aren't uploaded.

○ Artist A - Song X
  Not in locker

○ Artist B - Song Y
  Not in locker

○ Artist C - Song Z
  Not in locker
```

That immediately tells you:

> "These are in my source playlist but haven't been uploaded yet."

---

# 25. Add a "why excluded" reason

Eventually:

```text
EXCLUDED

Reason:
Not uploaded

Source:
406 Lyricists

Locker:
No matching upload record
```

Possible reasons:

```text
NOT_UPLOADED
UPLOAD_FAILED
UPLOAD_PENDING
IDENTITY_MISMATCH
UNRESOLVED
```

---

# 26. Matching safety

Given the issue you're currently fixing, I would make matching tiers explicit.

### Tier 1 — exact stable ID

```text
ytmusic_video_id
```

### Tier 2 — exact upload-associated track ID

```text
ytmusic_track_id
```

### Tier 3 — metadata

Only use metadata as a **candidate**, never automatically accept it when ambiguity exists.

For example:

```text
Artist: Local Artist
Title: Track
Duration: 3:42
```

If two possible locker records exist:

```text
DO NOT GUESS
```

Mark:

```text
IDENTITY_AMBIGUOUS
```

That is much safer.

---

# 27. Database relationships

I'd structure the important relationship like:

```text
SourcePlaylist
      │
      │ contains
      ▼
SourcePlaylistTrack
      │
      │ matched to
      ▼
LockerUpload
      │
      │ replicated into
      ▼
ReplicatedPlaylistTrack
```

This lets you audit exactly why every destination track exists.

---

# 28. Audit trail

For every replica operation:

```text
PlaylistSyncEvent
├── playlist_id
├── source_track_id
├── locker_upload_id
├── action
├── timestamp
└── reason
```

Example:

```text
2026-09-02 20:43
ADD
Local Artist - Song
Reason: source track exists in locker
```

Or:

```text
2026-09-02 20:43
REMOVE
Artist B - Song
Reason: no longer present in source playlist
```

---

# 29. Dry-run mode

Before enabling automatic modification:

```bash
ytmusic-sync playlist replicate --dry-run
```

Output:

```text
Source:
406 Lyricists

Would ADD:
+ Artist A - Song 1

Would REMOVE:
- Artist B - Song 2

Would MOVE:
Artist C - Song 3
    position 7 → position 2

Would EXCLUDE:
Artist D - Song 4
    not uploaded
```

This would be very valuable during testing.

---

# 30. Testing plan

### Source changed

```text
source A B C
locker A B C
destination A B C
```

Change source:

```text
A C B
```

Expected:

```text
destination A C B
```

### Missing locker track

```text
source A B C
locker A C
```

Expected:

```text
destination A C
```

### New upload

```text
source A B C
locker A C
```

then upload B.

Expected:

```text
A B C
```

### Upload removed/invalidated

If your system marks an upload invalid:

```text
source A B C
locker A C
```

Expected:

```text
A C
```

### Wrong artist

Source:

```text
Local Artist - Song
```

Locker contains:

```text
Different Artist - Song
```

Expected:

```text
EXCLUDED
```

### Same title

```text
Artist A - Song
Artist B - Song
```

Only the exact verified locker identity should match.

### Duplicate tracks

```text
A
B
A
C
```

Expected:

```text
A
B
A
C
```

### Restart

Stop Docker → start Docker.

Expected:

```text
state restored
watcher resumes
no duplicate tracks
```

---

# Final architecture

The complete feature should ultimately look like this:

```text
                    YOUTUBE MUSIC
                         │
                         │
                  SOURCE PLAYLIST
                         │
                         │ watch/read
                         ▼
                ┌─────────────────┐
                │ Playlist Watcher│
                └────────┬────────┘
                         │
                         ▼
                Source Playlist
                    Snapshot
                         │
                         ▼
                ┌─────────────────┐
                │  Match Engine   │◄────────────┐
                └────────┬────────┘             │
                         │                      │
                  source order                 │
                         │                      │
                         ▼                      │
                ┌─────────────────┐             │
                │ Locker Filter   │             │
                └────────┬────────┘             │
                         │                      │
                         │ only verified        │
                         │ uploads              │
                         ▼                      │
                ┌─────────────────┐             │
                │ Desired Playlist│             │
                │     State       │             │
                └────────┬────────┘             │
                         │                      │
                         ▼                      │
                ┌─────────────────┐             │
                │  Reconciler     │             │
                └────────┬────────┘             │
                         │                      │
                  ADD / REMOVE / MOVE          │
                         │                      │
                         ▼                      │
                ┌─────────────────┐             │
                │ Locker Replica  │             │
                │ YT Music Playlist│            │
                └─────────────────┘             │
                                                │
                         LOCKER UPLOAD ─────────┘
```

## The core rule

The implementation should enforce this formula:

**Destination Playlist = Source Playlist − anything that is not a verified locker upload**

And preserve the source's ordering:

**Destination order = Source order after locker-only filtering.**

That is the cleanest way to get the **1:1 copy you want without allowing songs that aren't actually in your upload locker**, while also protecting against the wrong-artist matching problem.
