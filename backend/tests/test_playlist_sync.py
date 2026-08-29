import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from ytm_service.main import app
from ytm_service.database import Database
from ytm_service.playlist_downloader import clean_youtube_title, download_and_upload_playlist_track, playlist_sync_manager


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db_file = tmp_path / "test_pl.db"
    db_instance = Database(db_file)
    await db_instance.init_db()
    with patch("ytm_service.main.db", db_instance), \
         patch("ytm_service.playlist_downloader.db", db_instance):
        yield db_instance


def test_clean_youtube_title():
    # Test title with artist and tag brackets
    title1, artist1 = clean_youtube_title("Queen - Bohemian Rhapsody [Official Audio]")
    assert title1 == "Bohemian Rhapsody"
    assert artist1 == "Queen"

    # Test title with parentheses official music video
    title2, artist2 = clean_youtube_title("Imagine (Official Music Video)", channel_name="John Lennon")
    assert title2 == "Imagine"
    assert artist2 == "John Lennon"

    # Test title with clean text
    title3, artist3 = clean_youtube_title("Clean Song Name")
    assert title3 == "Clean Song Name"
    assert artist3 is None


@pytest.mark.asyncio
async def test_download_and_upload_playlist_track(temp_db, tmp_path):
    dummy_file = tmp_path / "pl_dummy.mp3"
    dummy_file.write_bytes(b"dummy mp3 data")

    with patch("ytm_service.playlist_downloader._download_sync", return_value=dummy_file), \
         patch("ytm_service.playlist_downloader.write_metadata_tags") as mock_tags, \
         patch("ytm_service.playlist_downloader.ytm_client.upload_file", AsyncMock(return_value={"success": True, "response": "STATUS_SUCCEEDED"})), \
         patch("ytm_service.playlist_downloader.musicbrainz_client.search", AsyncMock(return_value=[])):

        res = await download_and_upload_playlist_track(
            video_id="pl_vid_123",
            raw_title="Daft Punk - One More Time [Official Audio]",
            raw_artist="Daft Punk",
            raw_album="Discovery",
            raw_thumbnail="https://example.com/art.jpg",
            destination_dir=tmp_path / "music",
            enrich_metadata=False
        )

        assert res["status"] == "success"
        assert res["video_id"] == "pl_vid_123"
        assert res["title"] == "One More Time"
        assert res["artist"] == "Daft Punk"
        assert mock_tags.called


@pytest.mark.asyncio
async def test_playlist_sync_endpoints(temp_db, tmp_path):
    with patch("ytm_service.main.ytm_client.is_auth_configured", return_value=True), \
         patch("ytm_service.main.ytm_client.get_playlist_details", AsyncMock(return_value={
             "id": "PL123",
             "title": "My Test Playlist",
             "tracks": [
                 {
                     "video_id": "vid_1",
                     "title": "Track One",
                     "artist": "Artist A",
                     "in_uploads": False,
                     "in_local": False
                 },
                 {
                     "video_id": "vid_2",
                     "title": "Track Two",
                     "artist": "Artist B",
                     "in_uploads": True,
                     "in_local": True
                 }
             ]
         })), \
         patch("ytm_service.main.playlist_sync_manager.start_sync") as mock_start_sync:

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Test sync-missing
            resp = await client.post("/api/ytm/playlists/PL123/sync-missing")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "started"
            assert data["queued"] == 1
            assert mock_start_sync.called

            # Test sync-status
            status_resp = await client.get("/api/ytm/playlists/sync-status")
            assert status_resp.status_code == 200
            assert "is_running" in status_resp.json()


@pytest.mark.asyncio
async def test_download_track_endpoint(temp_db, tmp_path):
    dummy_file = tmp_path / "track_dl.mp3"
    dummy_file.write_bytes(b"dummy")

    with patch("ytm_service.main.ytm_client.is_auth_configured", return_value=True), \
         patch("ytm_service.playlist_downloader._download_sync", return_value=dummy_file), \
         patch("ytm_service.playlist_downloader.write_metadata_tags"), \
         patch("ytm_service.playlist_downloader.ytm_client.upload_file", AsyncMock(return_value={"success": True, "response": "STATUS_SUCCEEDED"})), \
         patch("ytm_service.playlist_downloader.musicbrainz_client.search", AsyncMock(return_value=[])):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ytm/playlists/download-track",
                json={
                    "video_id": "vid_single_1",
                    "title": "Solo Track",
                    "artist": "Solo Artist",
                    "album": "Solo Album",
                    "thumbnail": "https://example.com/thumb.jpg",
                    "enrich_metadata": False
                }
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_import_playlist_url_endpoint(temp_db):
    with patch("ytm_service.main.extract_playlist_info", AsyncMock(return_value={
        "id": "ext_pl_1",
        "title": "Imported Playlist",
        "description": "Public hits",
        "track_count": 1,
        "tracks": [
            {
                "video_id": "ext_vid_1",
                "title": "Hit Song",
                "artist": "Hit Maker",
                "album": "Hit Album",
                "duration": 200,
                "thumbnail": "https://example.com/hit.jpg"
            }
        ]
    })):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ytm/playlists/import-url",
                json={"url": "https://www.youtube.com/playlist?list=ext_pl_1"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["title"] == "Imported Playlist"
            assert len(data["tracks"]) == 1
            assert data["tracks"][0]["in_uploads"] is False
