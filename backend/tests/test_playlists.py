import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from pathlib import Path
from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.ytm_client import ytm_client

@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    settings.db_path = tmp_path / "test_playlists.db"
    settings.auth_file = tmp_path / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()

@pytest.mark.asyncio
async def test_get_playlists_unauthenticated():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/ytm/playlists")
        assert response.status_code == 400
        assert "not authenticated" in response.json()["detail"].lower()

@pytest.mark.asyncio
async def test_get_playlists_authenticated():
    mock_playlists = [
        {"playlistId": "PL123", "title": "My Rock Mix", "count": 10, "thumbnails": [{"url": "http://thumb.jpg"}]}
    ]

    with patch.object(ytm_client, "is_auth_configured", return_value=True), \
         patch.object(ytm_client, "_get_client") as mock_get_client:
        
        mock_yt = MagicMock()
        mock_yt.get_library_playlists.return_value = mock_playlists
        mock_get_client.return_value = mock_yt

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/ytm/playlists")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 2  # Liked Music + PL123
            assert data[0]["id"] == "LM"
            assert data[0]["title"] == "Liked Music"
            assert data[1]["id"] == "PL123"
            assert data[1]["title"] == "My Rock Mix"
            assert data[1]["track_count"] == 10

@pytest.mark.asyncio
async def test_get_playlist_details_with_matching():
    # Insert a local song
    await db.upsert_music_file({
        "path": "/music/queen_bohemian.mp3",
        "filename": "queen_bohemian.mp3",
        "title": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "duration": 354.0,
        "format": "mp3",
        "file_size": 5000000,
        "file_hash": "hash1",
        "metadata_hash": "meta1",
    })

    mock_playlist_data = {
        "id": "PL123",
        "title": "My Rock Mix",
        "description": "Best rock songs",
        "tracks": [
            {
                "videoId": "vid1",
                "title": "Bohemian Rhapsody",
                "artists": [{"name": "Queen"}],
                "album": {"name": "A Night at the Opera"},
                "duration": "5:54",
                "thumbnails": [{"url": "http://thumb1.jpg"}]
            },
            {
                "videoId": "vid2",
                "title": "Streaming Exclusive Song",
                "artists": [{"name": "Other Artist"}],
                "album": {"name": "Other Album"},
                "duration": "3:30",
                "thumbnails": [{"url": "http://thumb2.jpg"}]
            }
        ]
    }

    with patch.object(ytm_client, "is_auth_configured", return_value=True), \
         patch.object(ytm_client, "_get_client") as mock_get_client:
        
        mock_yt = MagicMock()
        mock_yt.get_playlist.return_value = mock_playlist_data
        mock_get_client.return_value = mock_yt

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/api/ytm/playlists/PL123")
            assert response.status_code == 200
            data = response.json()
            assert data["title"] == "My Rock Mix"
            assert data["track_count"] == 2
            assert len(data["tracks"]) == 2
            
            # First track matches local song
            assert data["tracks"][0]["title"] == "Bohemian Rhapsody"
            assert data["tracks"][0]["in_local"] is True
            assert data["tracks"][0]["local_path"] == "/music/queen_bohemian.mp3"

            # Second track does not match
            assert data["tracks"][1]["title"] == "Streaming Exclusive Song"
            assert data["tracks"][1]["in_local"] is False
            assert data["tracks"][1]["local_path"] is None
