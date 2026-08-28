import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from pathlib import Path
from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.scanner import write_metadata_tags, extract_metadata

@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    settings.db_path = tmp_path / "test_metadata.db"
    settings.auth_file = tmp_path / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()

@pytest.mark.asyncio
async def test_update_song_metadata_endpoint(tmp_path: Path):
    # Create a test audio file
    test_song_path = tmp_path / "akon_test.mp3"
    test_song_path.write_bytes(b"mock audio data")

    file_id = await db.upsert_music_file({
        "path": str(test_song_path),
        "filename": "akon_test.mp3",
        "title": "akon_test",
        "artist": None,
        "album": None,
        "duration": 240.0,
        "format": "mp3",
        "file_size": len(test_song_path.read_bytes()),
        "modified_time": 1700000000.0,
        "file_hash": "hash123",
        "metadata_hash": "meta123",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            f"/api/songs/{file_id}/metadata",
            json={
                "title": "I Wanna Love You",
                "artist": "Akon",
                "album": "Konvicted",
                "track_number": 3
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == file_id
        assert data["title"] == "I Wanna Love You"
        assert data["artist"] == "Akon"
        assert data["album"] == "Konvicted"
        assert data["track_number"] == 3

    # Check updated in database
    db_song = await db.get_music_file_by_id(file_id)
    assert db_song is not None
    assert db_song.title == "I Wanna Love You"
    assert db_song.artist == "Akon"
    assert db_song.album == "Konvicted"
    assert db_song.track_number == 3

@pytest.mark.asyncio
async def test_update_nonexistent_song():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/api/songs/99999/metadata",
            json={
                "title": "Nonexistent",
                "artist": "Ghost"
            }
        )
        assert response.status_code == 404
