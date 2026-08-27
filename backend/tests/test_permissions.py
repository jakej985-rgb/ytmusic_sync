import os
import stat
import pytest
from pathlib import Path
from ytm_service.scanner import MusicScanner, extract_metadata
from ytm_service.database import Database

@pytest.mark.asyncio
async def test_filesystem_permissions_read_only_music(tmp_path: Path):
    # 1. Setup simulated read-only music mount
    music_dir = tmp_path / "music_ro"
    music_dir.mkdir()
    song_file = music_dir / "test_track.mp3"
    song_file.write_bytes(b"dummy mp3 data")

    # Make directory read-only (chmod 0555)
    music_dir.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)

    # Test: Read music -> works
    assert song_file.exists()
    assert len(song_file.read_bytes()) > 0

    # Test: Scan music -> works
    meta = extract_metadata(song_file)
    assert meta["filename"] == "test_track.mp3"
    assert meta["format"] == "MP3"

    # Test: Write music -> denied
    with pytest.raises((PermissionError, OSError)):
        (music_dir / "temp_junk.tmp").write_text("should fail")

    # Restore write permission for pytest tmp_path cleanup
    music_dir.chmod(stat.S_IRWXU)

@pytest.mark.asyncio
async def test_filesystem_permissions_writable_config(tmp_path: Path):
    # 2. Setup simulated writable /config mount
    config_dir = tmp_path / "config"
    db_dir = config_dir / "database"
    logs_dir = config_dir / "logs"
    db_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    # Test: Write config database -> works
    db_instance = Database(db_dir / "ytm_sync.db")
    await db_instance.init_db()
    file_id = await db_instance.upsert_music_file({
        "path": "/music/track.mp3",
        "filename": "track.mp3",
        "title": "Track",
        "format": "MP3",
        "file_size": 500,
        "modified_time": 1000.0,
    })
    assert file_id > 0

    # Test: Write logs -> works
    log_file = logs_dir / "ytm_sync.log"
    log_file.write_text("2026-08-27 12:00:00 INFO Test log entry\n")
    assert log_file.exists()
    assert "Test log entry" in log_file.read_text()
