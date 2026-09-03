"""
Comprehensive test suite for YouTube Music Playlist Watcher & 1:1 Locker-Only Replica Engine.
Tests all scenarios specified in Section 30 of plan.md:
- Locker-only filtering
- Order preservation & duplicate preservation
- Reordering reconciliation
- New upload completion reconciliation
- Exclusion of un-uploaded / different artist tracks
- Source playlist read-only guarantee
- Dry-run mode
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from ytm_service.database import Database
from ytm_service.models import YtmUpload
from ytm_service.playlist_replicator import (
    build_locker_lookup,
    match_source_track_to_locker,
    filter_source_tracks_for_replica,
    calculate_reconciliation_diff,
    PlaylistReplicatorService
)


@pytest.fixture
async def temp_db(tmp_path: Path):
    db_file = tmp_path / "test_replicated.db"
    db_inst = Database(db_file)
    await db_inst.init_db()
    yield db_inst


@pytest.fixture
def sample_locker_uploads():
    return [
        YtmUpload(
            id=1,
            entity_id="up_entity_A",
            video_id="VID_A",
            artist="Local Artist A",
            title="Track One",
            duration=180.0
        ),
        YtmUpload(
            id=2,
            entity_id="up_entity_B",
            video_id="VID_B",
            artist="Local Artist B",
            title="Track Two",
            duration=210.0
        ),
        YtmUpload(
            id=3,
            entity_id="up_entity_C",
            video_id="VID_C",
            artist="Local Artist C",
            title="Track Three",
            duration=240.0
        ),
    ]


# ----------------------------------------------------------------------------
# 1. Locker Lookup & Matching Tests
# ----------------------------------------------------------------------------

def test_match_tier1_exact_video_id(sample_locker_uploads):
    lookup = build_locker_lookup(sample_locker_uploads)
    source_track = {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"}
    matched, reason = match_source_track_to_locker(source_track, lookup)
    assert matched is not None
    assert matched["video_id"] == "VID_A"
    assert reason is None


def test_match_tier2_exact_entity_id(sample_locker_uploads):
    lookup = build_locker_lookup(sample_locker_uploads)
    source_track = {"videoId": "up_entity_B", "title": "Track Two", "artist": "Local Artist B"}
    matched, reason = match_source_track_to_locker(source_track, lookup)
    assert matched is not None
    assert matched["entity_id"] == "up_entity_B"
    assert reason is None


def test_match_tier3_exact_metadata_and_duration(sample_locker_uploads):
    lookup = build_locker_lookup(sample_locker_uploads)
    # Source track has public or catalog video ID, but exact metadata matches locker track
    source_track = {
        "videoId": "PUBLIC_XYZ",
        "title": "Track Three",
        "artist": "Local Artist C",
        "duration_seconds": 241.0
    }
    matched, reason = match_source_track_to_locker(source_track, lookup)
    assert matched is not None
    assert matched["video_id"] == "VID_C"
    assert reason is None


def test_exclude_when_not_in_locker(sample_locker_uploads):
    lookup = build_locker_lookup(sample_locker_uploads)
    source_track = {
        "videoId": "UNUPLOADED_999",
        "title": "Unreleased Track",
        "artist": "Unuploaded Artist"
    }
    matched, reason = match_source_track_to_locker(source_track, lookup)
    assert matched is None
    assert reason == "NOT_PRESENT_IN_LOCKER"


def test_exclude_wrong_artist_same_title(sample_locker_uploads):
    lookup = build_locker_lookup(sample_locker_uploads)
    # Locker has 'Local Artist A' - 'Track One'
    # Source has 'Big Pop Star' - 'Track One'
    source_track = {
        "videoId": "CATALOG_POP_STAR",
        "title": "Track One",
        "artist": "Big Pop Star"
    }
    matched, reason = match_source_track_to_locker(source_track, lookup)
    assert matched is None
    assert reason == "NOT_PRESENT_IN_LOCKER"


def test_exclude_ambiguous_metadata():
    # Two locker tracks with the same artist and title
    uploads = [
        YtmUpload(id=1, entity_id="up1", video_id="V1", artist="Artist", title="Song"),
        YtmUpload(id=2, entity_id="up2", video_id="V2", artist="Artist", title="Song"),
    ]
    lookup = build_locker_lookup(uploads)
    source_track = {"videoId": "CATALOG_123", "artist": "Artist", "title": "Song"}
    matched, reason = match_source_track_to_locker(source_track, lookup)
    assert matched is None
    assert reason == "IDENTITY_AMBIGUOUS"


# ----------------------------------------------------------------------------
# 2. Filtering & Invariant Tests (Section 11 & 21 of plan)
# ----------------------------------------------------------------------------

def test_locker_only_filtering_and_order_preservation(sample_locker_uploads):
    """
    Source: A, B, C, D (where D is not in locker)
    Expected: Desired = [A, B, C], Excluded = [D]
    """
    lookup = build_locker_lookup(sample_locker_uploads)
    source_tracks = [
        {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"},
        {"videoId": "VID_B", "title": "Track Two", "artist": "Local Artist B"},
        {"videoId": "VID_C", "title": "Track Three", "artist": "Local Artist C"},
        {"videoId": "VID_D", "title": "Track Four", "artist": "Local Artist D"},  # Not in locker
    ]
    desired, excluded = filter_source_tracks_for_replica(source_tracks, lookup)

    assert len(desired) == 3
    assert [d["video_id"] for d in desired] == ["VID_A", "VID_B", "VID_C"]
    assert len(excluded) == 1
    assert excluded[0]["video_id"] == "VID_D"
    assert excluded[0]["reason"] == "NOT_PRESENT_IN_LOCKER"


def test_preserve_duplicate_tracks_and_positions(sample_locker_uploads):
    """
    Section 21 of plan:
    Source: A, B, A, C
    Locker: A, B, C
    Destination must become: A, B, A, C (preserving duplicate occurrences and order).
    """
    lookup = build_locker_lookup(sample_locker_uploads)
    source_tracks = [
        {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"},
        {"videoId": "VID_B", "title": "Track Two", "artist": "Local Artist B"},
        {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"},  # Duplicate
        {"videoId": "VID_C", "title": "Track Three", "artist": "Local Artist C"},
    ]
    desired, excluded = filter_source_tracks_for_replica(source_tracks, lookup)

    assert len(desired) == 4
    assert [d["video_id"] for d in desired] == ["VID_A", "VID_B", "VID_A", "VID_C"]
    assert len(excluded) == 0


# ----------------------------------------------------------------------------
# 3. Reconciliation Algorithm Tests (Additions, Removals, Reordering)
# ----------------------------------------------------------------------------

def test_reconciliation_noop_when_in_sync():
    current_dest = [
        {"videoId": "VID_A", "setVideoId": "s1"},
        {"videoId": "VID_B", "setVideoId": "s2"}
    ]
    desired = [
        {"video_id": "VID_A"},
        {"video_id": "VID_B"}
    ]
    diff = calculate_reconciliation_diff(current_dest, desired)
    assert diff["status"] == "IN_SYNC"
    assert len(diff["actions"]) == 0


def test_reconciliation_removals_and_additions():
    """
    Current Destination: A, B, X (where X is not desired)
    Desired: A, B, C
    Expected: REMOVE X, ADD C
    """
    current_dest = [
        {"videoId": "VID_A", "setVideoId": "s1"},
        {"videoId": "VID_B", "setVideoId": "s2"},
        {"videoId": "VID_X", "setVideoId": "sX"}
    ]
    desired = [
        {"video_id": "VID_A"},
        {"video_id": "VID_B"},
        {"video_id": "VID_C"}
    ]
    diff = calculate_reconciliation_diff(current_dest, desired)
    assert diff["status"] == "CHANGES_REQUIRED"
    assert any(a["action"] == "REMOVE" and a["video_id"] == "VID_X" for a in diff["actions"])
    assert any(a["action"] == "ADD" and a["video_id"] == "VID_C" for a in diff["actions"])


def test_reconciliation_reordering():
    """
    Section 10 of plan:
    Current: A, B, C
    Desired: C, B, A
    Expected: reordered = True, MOVE actions detected
    """
    current_dest = [
        {"videoId": "VID_A", "setVideoId": "s1"},
        {"videoId": "VID_B", "setVideoId": "s2"},
        {"videoId": "VID_C", "setVideoId": "s3"}
    ]
    desired = [
        {"video_id": "VID_C"},
        {"video_id": "VID_B"},
        {"video_id": "VID_A"}
    ]
    diff = calculate_reconciliation_diff(current_dest, desired)
    assert diff["status"] == "CHANGES_REQUIRED"
    assert diff["reordered"] is True
    move_actions = [a for a in diff["actions"] if a["action"] == "MOVE"]
    assert len(move_actions) >= 2
    # C moved from pos 3 to pos 1
    move_c = next(m for m in move_actions if m["video_id"] == "VID_C")
    assert move_c["from_position"] == 3
    assert move_c["to_position"] == 1
    # A moved from pos 1 to pos 3
    move_a = next(m for m in move_actions if m["video_id"] == "VID_A")
    assert move_a["from_position"] == 1
    assert move_a["to_position"] == 3


# ----------------------------------------------------------------------------
# 4. Service Orchestration & Read-Only Source Invariant Tests
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_playlist_service_dry_run(temp_db, sample_locker_uploads):
    """
    Test dry-run execution: calculates diff without mutating destination playlist.
    """
    # 1. Insert locker uploads
    for u in sample_locker_uploads:
        await temp_db.upsert_ytm_upload(u)

    # 2. Insert replicated playlist config
    rep_id = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_PLAYLIST_100",
        source_playlist_name="My Artists",
        destination_playlist_id="DEST_PLAYLIST_200",
        destination_playlist_name="My Artists - Locker"
    )

    replicator = PlaylistReplicatorService()

    # Mock ytm_client
    mock_ytm = MagicMock()
    mock_ytm.get_playlist_raw = AsyncMock(side_effect=lambda pid: {
        "SRC_PLAYLIST_100": {
            "title": "My Artists",
            "tracks": [
                {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"},
                {"videoId": "VID_B", "title": "Track Two", "artist": "Local Artist B"},
                {"videoId": "VID_NOT_IN_LOCKER", "title": "Ghost", "artist": "Ghost"}
            ]
        },
        "DEST_PLAYLIST_200": {
            "title": "My Artists - Locker",
            "tracks": [
                {"videoId": "VID_A", "setVideoId": "set_1"}
            ]
        }
    }[pid])
    mock_ytm.add_playlist_items = AsyncMock()
    mock_ytm.remove_playlist_items = AsyncMock()
    mock_ytm.create_playlist = AsyncMock()

    with patch("ytm_service.playlist_replicator.db", temp_db), \
         patch("ytm_service.playlist_replicator.ytm_client", mock_ytm):
        res = await replicator.reconcile_playlist(rep_id, dry_run=True)

        assert res["dry_run"] is True
        assert res["source_tracks_count"] == 3
        assert res["desired_tracks_count"] == 2
        assert res["excluded_count"] == 1
        assert res["excluded_tracks"][0]["video_id"] == "VID_NOT_IN_LOCKER"

        # Assert no mutations occurred in dry-run mode
        mock_ytm.add_playlist_items.assert_not_called()
        mock_ytm.remove_playlist_items.assert_not_called()


@pytest.mark.asyncio
async def test_source_playlist_is_strictly_read_only(temp_db, sample_locker_uploads):
    """
    Section 19 of plan: Source playlist must NEVER be modified.
    Assert that add/remove/create are never called on SRC_PLAYLIST_100.
    """
    for u in sample_locker_uploads:
        await temp_db.upsert_ytm_upload(u)

    rep_id = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_PLAYLIST_100",
        source_playlist_name="My Artists",
        destination_playlist_id="DEST_PLAYLIST_200",
        destination_playlist_name="My Artists - Locker"
    )

    replicator = PlaylistReplicatorService()

    mock_ytm = MagicMock()
    mock_ytm.get_playlist_raw = AsyncMock(side_effect=lambda pid: {
        "SRC_PLAYLIST_100": {
            "title": "My Artists",
            "tracks": [
                {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"},
                {"videoId": "VID_B", "title": "Track Two", "artist": "Local Artist B"}
            ]
        },
        "DEST_PLAYLIST_200": {
            "title": "My Artists - Locker",
            "tracks": []
        }
    }[pid])
    mock_ytm.add_playlist_items = AsyncMock(return_value={"status": "ok"})
    mock_ytm.remove_playlist_items = AsyncMock(return_value={"status": "ok"})

    with patch("ytm_service.playlist_replicator.db", temp_db), \
         patch("ytm_service.playlist_replicator.ytm_client", mock_ytm):
        await replicator.reconcile_playlist(rep_id, dry_run=False)

        # Confirm destination was populated
        mock_ytm.add_playlist_items.assert_called_once_with(
            "DEST_PLAYLIST_200", ["VID_A", "VID_B"], duplicates=True
        )

        # Confirm source playlist was NEVER the target of add or remove
        for call in mock_ytm.add_playlist_items.call_args_list:
            assert call[0][0] != "SRC_PLAYLIST_100"
        for call in mock_ytm.remove_playlist_items.call_args_list:
            assert call[0][0] != "SRC_PLAYLIST_100"

        # Check audit trail recorded in database
        events = await temp_db.get_replicated_playlist_events(rep_id)
        assert len(events) >= 2
        assert all(e["action"] == "ADD" for e in events)


@pytest.mark.asyncio
async def test_new_upload_event_triggers_watcher(temp_db):
    """
    Section 14 of plan: When a new upload completes, watcher should reconcile affected replicas.
    """
    from ytm_service.playlist_watcher import playlist_watcher

    rep_id = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_PLAYLIST_100",
        source_playlist_name="My Artists",
        destination_playlist_id="DEST_PLAYLIST_200",
        destination_playlist_name="My Artists - Locker",
        enabled=True
    )

    with patch("ytm_service.playlist_watcher.db", temp_db), \
         patch("ytm_service.playlist_watcher.playlist_replicator.reconcile_playlist", new_callable=AsyncMock) as mock_reconcile:
        mock_reconcile.return_value = {"status": "IN_SYNC"}

        await playlist_watcher.on_new_upload_completed("NEW_VID_123")

        mock_reconcile.assert_called_once_with(rep_id, dry_run=False)


@pytest.mark.asyncio
async def test_api_replicated_playlists_crud(temp_db):
    """
    Test full REST API lifecycle for replicated playlists.
    """
    from fastapi.testclient import TestClient
    from ytm_service.main import app
    from ytm_service.config import settings

    client = TestClient(app)
    auth_headers = {"Authorization": f"Bearer {settings.api_key}"}

    with patch("ytm_service.main.db", temp_db), \
         patch("ytm_service.playlist_replicator.db", temp_db), \
         patch("ytm_service.main.ytm_client.get_playlist_details", new_callable=AsyncMock) as mock_details, \
         patch("ytm_service.main.playlist_replicator.reconcile_playlist", new_callable=AsyncMock) as mock_reconcile:

        mock_details.return_value = {"title": "406 Lyricists"}
        mock_reconcile.return_value = {
            "replicated_id": 1,
            "source_tracks_count": 10,
            "desired_tracks_count": 8,
            "excluded_count": 2,
            "status": "CHANGES_REQUIRED",
            "dry_run": True
        }

        # 1. Create
        create_res = client.post("/api/replicated-playlists", json={
            "source_playlist_id": "SRC_406",
            "enabled": True,
            "sync_interval_seconds": 300
        }, headers=auth_headers)
        assert create_res.status_code == 200
        created_data = create_res.json()
        assert created_data["source_playlist_name"] == "406 Lyricists"
        assert created_data["destination_playlist_name"] == "406 Lyricists - Locker"
        rep_id = created_data["id"]

        # 2. List
        list_res = client.get("/api/replicated-playlists", headers=auth_headers)
        assert list_res.status_code == 200
        items = list_res.json()
        assert len(items) == 1
        assert items[0]["id"] == rep_id

        # 3. Get details with preview
        get_res = client.get(f"/api/replicated-playlists/{rep_id}", headers=auth_headers)
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["config"]["id"] == rep_id
        assert get_data["preview"]["excluded_count"] == 2

        # 4. Dry-run endpoint
        dry_res = client.post(f"/api/replicated-playlists/{rep_id}/dry-run", headers=auth_headers)
        assert dry_res.status_code == 200
        assert dry_res.json()["dry_run"] is True

        # 5. Update
        update_res = client.put(f"/api/replicated-playlists/{rep_id}", json={
            "destination_playlist_name": "Custom Name",
            "sync_interval_seconds": 600
        }, headers=auth_headers)
        assert update_res.status_code == 200
        assert update_res.json()["destination_playlist_name"] == "Custom Name"
        assert update_res.json()["sync_interval_seconds"] == 600

        # 6. Delete
        del_res = client.delete(f"/api/replicated-playlists/{rep_id}", headers=auth_headers)
        assert del_res.status_code == 200

        # 7. Verify deleted
        list_res2 = client.get("/api/replicated-playlists", headers=auth_headers)
        assert len(list_res2.json()) == 0


@pytest.mark.asyncio
async def test_source_playlist_snapshot_persisted_and_retrieved(temp_db, sample_locker_uploads):
    """
    Section 4 of plan: Test that SourcePlaylistSnapshot is generated and stored in database.
    """
    for u in sample_locker_uploads:
        await temp_db.upsert_ytm_upload(u)

    rep_id = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_SNAP_100",
        source_playlist_name="Snapshot Playlist",
        destination_playlist_id="DEST_SNAP_200",
        destination_playlist_name="Snapshot Playlist - Locker"
    )

    replicator = PlaylistReplicatorService()

    mock_ytm = MagicMock()
    mock_ytm.get_playlist_raw = AsyncMock(side_effect=lambda pid: {
        "SRC_SNAP_100": {
            "title": "Snapshot Playlist",
            "tracks": [
                {"videoId": "VID_A", "title": "Track One", "artist": "Local Artist A"},
                {"videoId": "VID_B", "title": "Track Two", "artist": "Local Artist B"}
            ]
        },
        "DEST_SNAP_200": {
            "title": "Snapshot Playlist - Locker",
            "tracks": []
        }
    }[pid])
    mock_ytm.add_playlist_items = AsyncMock(return_value={"status": "ok"})
    mock_ytm.remove_playlist_items = AsyncMock(return_value={"status": "ok"})

    with patch("ytm_service.playlist_replicator.db", temp_db), \
         patch("ytm_service.playlist_replicator.ytm_client", mock_ytm):
        res = await replicator.reconcile_playlist(rep_id, dry_run=False)
        assert res["revision"] is not None

        # Verify snapshot is stored in database
        snapshot = await temp_db.get_latest_replicated_playlist_snapshot(rep_id)
        assert snapshot is not None
        assert snapshot["revision"] == res["revision"]
        assert snapshot["track_count"] == 2
        assert len(snapshot["tracks"]) == 2
        assert snapshot["tracks"][0]["video_id"] == "VID_A"
        assert snapshot["tracks"][1]["video_id"] == "VID_B"


@pytest.mark.asyncio
async def test_section13_subsequent_upload_reconciles_destination(temp_db):
    """
    Section 13 of plan:
    Source: A, B, C, D
    Locker starts: A, B, D (C missing)
    Reconciliation 1 -> Destination becomes: A, B, D (C excluded)
    Normal sync uploads C into locker.
    PlaylistWatcher detects new upload -> Destination becomes: A, B, C, D
    """
    from ytm_service.playlist_watcher import playlist_watcher
    from ytm_service.playlist_replicator import playlist_replicator

    # 1. Locker initially has A, B, D
    await temp_db.upsert_ytm_upload(YtmUpload(id=1, entity_id="up_A", video_id="VID_A", artist="Art A", title="Song A"))
    await temp_db.upsert_ytm_upload(YtmUpload(id=2, entity_id="up_B", video_id="VID_B", artist="Art B", title="Song B"))
    await temp_db.upsert_ytm_upload(YtmUpload(id=4, entity_id="up_D", video_id="VID_D", artist="Art D", title="Song D"))

    rep_id = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_S13",
        source_playlist_name="Source S13",
        destination_playlist_id="DEST_S13",
        destination_playlist_name="Source S13 - Locker",
        enabled=True
    )

    dest_state = []

    def mock_get_playlist(pid):
        if pid == "SRC_S13":
            return {
                "title": "Source S13",
                "tracks": [
                    {"videoId": "VID_A", "title": "Song A", "artist": "Art A"},
                    {"videoId": "VID_B", "title": "Song B", "artist": "Art B"},
                    {"videoId": "VID_C", "title": "Song C", "artist": "Art C"},
                    {"videoId": "VID_D", "title": "Song D", "artist": "Art D"},
                ]
            }
        else:
            return {
                "title": "Source S13 - Locker",
                "tracks": list(dest_state)
            }

    mock_ytm = MagicMock()
    mock_ytm.get_playlist_raw = AsyncMock(side_effect=mock_get_playlist)

    async def mock_add_items(pid, vids, duplicates=True):
        for v in vids:
            dest_state.append({"videoId": v, "setVideoId": f"set_{v}"})
        return {"status": "ok"}

    async def mock_remove_items(pid, items):
        to_del = {i["videoId"] for i in items}
        dest_state[:] = [t for t in dest_state if t["videoId"] not in to_del]
        return {"status": "ok"}

    mock_ytm.add_playlist_items = AsyncMock(side_effect=mock_add_items)
    mock_ytm.remove_playlist_items = AsyncMock(side_effect=mock_remove_items)

    with patch("ytm_service.playlist_replicator.db", temp_db), \
         patch("ytm_service.playlist_replicator.ytm_client", mock_ytm), \
         patch("ytm_service.playlist_watcher.db", temp_db):

        # Initial reconciliation
        res1 = await playlist_replicator.reconcile_playlist(rep_id, dry_run=False)
        assert res1["desired_tracks_count"] == 3
        assert res1["excluded_count"] == 1
        assert res1["excluded_tracks"][0]["video_id"] == "VID_C"
        # Destination currently has A, B, D
        assert [t["videoId"] for t in dest_state] == ["VID_A", "VID_B", "VID_D"]

        # Step 2: Song C finishes uploading to locker
        await temp_db.upsert_ytm_upload(YtmUpload(id=3, entity_id="up_C", video_id="VID_C", artist="Art C", title="Song C"))

        # Event trigger fires (called by uploader upon completion)
        await playlist_watcher.on_new_upload_completed("VID_C")

        # Destination is now updated to A, B, C, D in exact source order!
        assert [t["videoId"] for t in dest_state] == ["VID_A", "VID_B", "VID_C", "VID_D"]


@pytest.mark.asyncio
async def test_section15_efficient_reconciliation_dependency_tracking(temp_db):
    """
    Section 15 of plan:
    Playlist A: Song A, Song B, Song C
    Playlist B: Song X, Song Y (does not contain Song C)
    When Song C completes upload:
    - Playlist A is reconciled
    - Playlist B is skipped
    """
    from ytm_service.playlist_watcher import playlist_watcher

    # Playlist A
    rep_a = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_A",
        source_playlist_name="Playlist A",
        destination_playlist_id="DEST_A",
        destination_playlist_name="Playlist A - Locker",
        enabled=True
    )
    await temp_db.save_replicated_playlist_snapshot(rep_a, "rev_a", [
        {"position": 1, "video_id": "VID_A", "title": "Song A"},
        {"position": 2, "video_id": "VID_B", "title": "Song B"},
        {"position": 3, "video_id": "VID_C", "title": "Song C"},
    ])

    # Playlist B
    rep_b = await temp_db.create_replicated_playlist(
        source_playlist_id="SRC_B",
        source_playlist_name="Playlist B",
        destination_playlist_id="DEST_B",
        destination_playlist_name="Playlist B - Locker",
        enabled=True
    )
    await temp_db.save_replicated_playlist_snapshot(rep_b, "rev_b", [
        {"position": 1, "video_id": "VID_X", "title": "Song X"},
        {"position": 2, "video_id": "VID_Y", "title": "Song Y"},
    ])

    await temp_db.upsert_ytm_upload(YtmUpload(id=10, entity_id="up_C", video_id="VID_C", artist="Art C", title="Song C"))

    with patch("ytm_service.playlist_watcher.db", temp_db), \
         patch("ytm_service.playlist_watcher.playlist_replicator.reconcile_playlist", new_callable=AsyncMock) as mock_reconcile:
        mock_reconcile.return_value = {"status": "IN_SYNC"}

        await playlist_watcher.on_new_upload_completed("VID_C")

        # Assert: Reconcile was called for Playlist A (which depends on Song C), but NOT for Playlist B!
        called_rep_ids = [c[0][0] for c in mock_reconcile.call_args_list]
        assert rep_a in called_rep_ids
        assert rep_b not in called_rep_ids


@pytest.mark.asyncio
async def test_section18_destination_playlist_ownership_marker(temp_db):
    """
    Section 18 of plan:
    Managed playlists must have ownership marker in description.
    """
    from ytm_service.playlist_replicator import is_managed_by_ytmusic_sync

    assert is_managed_by_ytmusic_sync("Automated 1:1 Locker-Only Replica. [managed_by=ytmusic_sync;replica_mode=locker_only;source_playlist_id=123]") is True
    assert is_managed_by_ytmusic_sync("My personal favorites playlist") is False


def test_section29_cli_dry_run_formatting():
    """
    Section 29 of plan: Assert CLI dry run format matches expected output.
    """
    from ytm_service.cli import format_replica_diff

    res = {
        "source_playlist_name": "406 Lyricists",
        "dry_run": True,
        "actions": [
            {"action": "ADD", "video_id": "V1", "title": "Song 1", "artist": "Artist A"},
            {"action": "REMOVE", "video_id": "V2", "title": "Song 2", "artist": "Artist B"},
            {"action": "MOVE", "video_id": "V3", "title": "Song 3", "artist": "Artist C", "from_position": 7, "to_position": 2}
        ],
        "excluded_tracks": [
            {"video_id": "V4", "title": "Song 4", "artist": "Artist D", "human_reason": "not uploaded"}
        ]
    }
    output = format_replica_diff(res)
    assert "Source:\n406 Lyricists" in output
    assert "Would ADD:\n+ Artist A - Song 1" in output
    assert "Would REMOVE:\n- Artist B - Song 2" in output
    assert "Would MOVE:\nArtist C - Song 3\n    position 7 -> position 2" in output
    assert "Would EXCLUDE:\nArtist D - Song 4\n    not uploaded" in output





