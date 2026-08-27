import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from pathlib import Path
from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings

@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    settings.db_path = tmp_path / "test_fs.db"
    settings.auth_file = tmp_path / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()

@pytest.mark.asyncio
async def test_browse_filesystem_root():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/fs/browse?path=/")
        assert response.status_code == 200
        data = response.json()
        assert data["current_path"] == "/"
        assert isinstance(data["directories"], list)
        assert "free_space" in data
        assert "total_space" in data

@pytest.mark.asyncio
async def test_browse_filesystem_temp_dir(tmp_path: Path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    (music_dir / "Rock").mkdir()
    (music_dir / "Jazz").mkdir()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(f"/api/fs/browse?path={music_dir}")
        assert response.status_code == 200
        data = response.json()
        assert data["current_path"] == str(music_dir)
        dir_names = [d["name"] for d in data["directories"]]
        assert "Rock" in dir_names
        assert "Jazz" in dir_names

@pytest.mark.asyncio
async def test_folders_stats_endpoint(tmp_path: Path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    # Configure folder
    await db.set_setting("music_folders", [str(music_dir)])

    # Insert a music file inside that folder
    await db.upsert_music_file({
        "path": str(music_dir / "song1.mp3"),
        "filename": "song1.mp3",
        "title": "Song 1",
        "artist": "Artist 1",
        "album": "Album 1",
        "duration": 200.0,
        "format": "mp3",
        "file_size": 3000000,
        "modified_time": 1700000000.0,
        "file_hash": "hash1",
        "metadata_hash": "meta1",
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/folders/stats")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        stat = data[0]
        assert stat["path"] == str(music_dir)
        assert stat["exists"] is True
        assert stat["songs_count"] == 1
        assert stat["unmapped_count"] == 1  # Not matched yet
        assert "free_space" in stat
