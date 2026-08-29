import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from ytm_service.main import app
from ytm_service.database import Database
from ytm_service.queue_service import unified_queue_service
from ytm_service.metadata_tracker import metadata_tracker
from ytm_service.playlist_downloader import playlist_sync_manager


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db_file = tmp_path / "test_queue.db"
    db_instance = Database(db_file)
    await db_instance.init_db()
    with patch("ytm_service.main.db", db_instance), \
         patch("ytm_service.queue_service.db", db_instance):
        yield db_instance


@pytest.mark.asyncio
async def test_unified_queue_empty(temp_db):
    metadata_tracker.clear()
    playlist_sync_manager.clear_history()
    playlist_sync_manager._status.is_running = False

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/queue")
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert data["summary"]["all"] == 0
        assert data["items"] == []


@pytest.mark.asyncio
async def test_unified_queue_with_metadata_and_playlist_tasks(temp_db):
    metadata_tracker.clear()
    playlist_sync_manager.clear_history()

    # 1. Log a metadata change
    metadata_tracker.log_change(
        title="Test Song",
        artist="Test Artist",
        album="Test Album",
        source="Metadata Editor",
        detail="Wrote ID3 tags"
    )

    # 2. Simulate an active playlist sync with 2 tracks
    playlist_sync_manager._status.is_running = True
    playlist_sync_manager._status.playlist_title = "Road Trip"
    playlist_sync_manager._status.total_tracks = 2
    playlist_sync_manager._status.completed_tracks = 0
    playlist_sync_manager._current_index = 0
    playlist_sync_manager._current_track_dict = {
        "video_id": "vid_active_1",
        "title": "Active Track",
        "artist": "Artist One",
        "album": "Album One",
        "thumbnail": None
    }
    playlist_sync_manager._queue = [
        playlist_sync_manager._current_track_dict,
        {
            "video_id": "vid_queued_2",
            "title": "Queued Track",
            "artist": "Artist Two",
            "album": "Album Two",
            "thumbnail": None
        }
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Test Category: ALL
        resp_all = await client.get("/api/queue?category=all")
        assert resp_all.status_code == 200
        data_all = resp_all.json()
        assert data_all["summary"]["metadata_change"] == 1
        assert data_all["summary"]["download"] == 2
        assert data_all["summary"]["upload"] == 2
        assert data_all["is_active"] is True
        assert len(data_all["items"]) == 3  # 1 active + 1 queued + 1 meta

        # Test Category: DOWNLOAD
        resp_dl = await client.get("/api/queue?category=download")
        data_dl = resp_dl.json()
        assert len(data_dl["items"]) == 2
        assert all(it["status"] in ("in_progress", "queued") for it in data_dl["items"])

        # Test Category: METADATA_CHANGE
        resp_meta = await client.get("/api/queue?category=metadata_change")
        data_meta = resp_meta.json()
        assert len(data_meta["items"]) == 1
        assert data_meta["items"][0]["title"] == "Test Song"

        # Test Clear Completed
        resp_clear = await client.post("/api/queue/clear-completed")
        assert resp_clear.status_code == 200
        assert len(metadata_tracker.get_recent()) == 0

        # Clean up
        playlist_sync_manager._status.is_running = False
