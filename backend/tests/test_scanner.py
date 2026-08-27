import pytest
from pathlib import Path
from ytm_service.scanner import extract_metadata, MusicScanner
from ytm_service.database import Database

@pytest.mark.asyncio
async def test_scanner_finds_supported_and_ignores_unsupported(tmp_path: Path):
    db_instance = Database(tmp_path / "test_scanner.db")
    await db_instance.init_db()

    # Create supported files
    (tmp_path / "song1.mp3").write_bytes(b"dummy mp3")
    (tmp_path / "song2.flac").write_bytes(b"dummy flac")
    (tmp_path / "song3.m4a").write_bytes(b"dummy m4a")
    (tmp_path / "song4.ogg").write_bytes(b"dummy ogg")
    (tmp_path / "song5.wma").write_bytes(b"dummy wma")

    # Create unsupported files
    (tmp_path / "cover.jpg").write_bytes(b"dummy jpg")
    (tmp_path / "readme.txt").write_bytes(b"dummy txt")
    (tmp_path / "app.exe").write_bytes(b"dummy exe")

    scanner = MusicScanner()
    count = await scanner.scan_folders([str(tmp_path)])
    assert count == 5
    assert not scanner.is_scanning

@pytest.mark.asyncio
async def test_scanner_handles_missing_folder():
    scanner = MusicScanner()
    count = await scanner.scan_folders(["/non_existent_folder_path_12345"])
    assert count == 0
    assert not scanner.is_scanning

def test_extract_metadata_fallback_and_malformed(tmp_path: Path):
    dummy_file = tmp_path / "corrupted_audio.mp3"
    dummy_file.write_bytes(b"corrupted audio content that is not valid ID3")

    meta = extract_metadata(dummy_file)
    assert meta["filename"] == "corrupted_audio.mp3"
    assert meta["title"] == "corrupted_audio"
    assert meta["format"] == "MP3"
    assert meta["file_size"] > 0
    assert meta["file_hash"] is not None
    assert meta["metadata_hash"] is not None
