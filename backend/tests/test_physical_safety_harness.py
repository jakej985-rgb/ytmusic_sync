import os
import shutil
import hashlib
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from ytm_service.downloader import (
    download_upload,
    commit_staged_file_to_destination,
    PrivateUploadUnavailableError,
    DownloadIntegrityError,
    _download_sync
)
from ytm_service.config import settings
from ytm_service.database import Database
from ytm_service.models import UploadStatus, VerificationStatus


def compute_dir_hashes(directory: Path) -> dict[str, str]:
    """Compute sha256 hashes of all files in directory recursively."""
    hashes = {}
    for p in directory.rglob("*"):
        if p.is_file() and not p.name.endswith(".id") and not p.name.endswith(".bak"):
            h = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            hashes[str(p.relative_to(directory))] = h.hexdigest()
    return hashes


@pytest.fixture
def physical_test_environment(tmp_path):
    """
    Sets up isolated test_artifacts/ directory without touching real /music or /downloads.
    Creates:
      test_artifacts/music/Artist A/Track.mp3
      test_artifacts/music/Local Artist/Track.mp3
      test_artifacts/music/Same Title Artist/Track.mp3
    """
    root_artifacts = tmp_path / "test_artifacts"
    music_dir = root_artifacts / "music"
    staging_dir = root_artifacts / "staging"
    backups_dir = root_artifacts / "backups"

    music_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    backups_dir.mkdir(parents=True, exist_ok=True)

    # 1. Artist A - Track.mp3
    artist_a_dir = music_dir / "Artist A"
    artist_a_dir.mkdir()
    track_a = artist_a_dir / "Track.mp3"
    track_a.write_bytes(b"ORIGINAL_AUDIO_ARTIST_A_PRIVATE_RELEASE_12345")

    # 2. Local Artist - Track.mp3 (The critical local artist recording)
    local_art_dir = music_dir / "Local Artist"
    local_art_dir.mkdir()
    local_track = local_art_dir / "Track.mp3"
    local_track.write_bytes(b"CRITICAL_LOCAL_ARTIST_MASTER_RECORDING_DO_NOT_OVERWRITE")

    # 3. Same Title Artist - Track.mp3
    same_title_dir = music_dir / "Same Title Artist"
    same_title_dir.mkdir()
    same_title_track = same_title_dir / "Track.mp3"
    same_title_track.write_bytes(b"SAME_TITLE_DIFFERENT_BAND_ORIGINAL_AUDIO")

    with patch.object(settings, "data_dir", root_artifacts), \
         patch.object(settings, "backups_dir", backups_dir), \
         patch.object(settings, "allow_automatic_replacement", False):
        yield {
            "root": root_artifacts,
            "music_dir": music_dir,
            "staging_dir": staging_dir,
            "backups_dir": backups_dir,
            "track_a": track_a,
            "local_track": local_track,
            "same_title_track": same_title_track,
        }


class TestPhysicalSafetyHarness:
    """
    Phases 20–22: Physical Safety & SHA-256 Invariant Test Harness
    Verifies that under all failure, error, and wrong-source scenarios:
    BEFORE SHA-256 == AFTER SHA-256
    """

    @pytest.mark.asyncio
    async def test_same_artist_same_title_trap_preserves_hashes(self, physical_test_environment):
        """
        THE CRITICAL INCIDENT TRAP:
        Local file: 'Local Artist - Track.mp3'
        Expected private upload: 'Local Artist - Track' (ID: priv_loc_123)
        Public YouTube search result: 'Local Artist - Track' (ID: pub_diff_999)

        Execution:
        Private upload fails auth/unavailable.
        Under the bug, it fell back to ytsearch1: and downloaded pub_diff_999, overwriting the file.
        Under the hardened architecture:
        - ytsearch1 is NEVER called
        - Download immediately fails closed
        - BEFORE SHA256 == AFTER SHA256 (100% byte-for-byte identical)
        """
        env = physical_test_environment
        music_dir = env["music_dir"]
        local_track = env["local_track"]

        # Compute SHA-256 BEFORE
        hashes_before = compute_dir_hashes(music_dir)
        local_hash_before = hashes_before["Local Artist/Track.mp3"]

        invoked_commands = []
        def fake_subprocess_trap(cmd, **kwargs):
            invoked_commands.append(cmd)
            # Private stream unavailable
            return MagicMock(returncode=1, stderr="Sign in to confirm you are not a bot / private video", stdout="")

        upload_payload = {
            "source_type": "ytm_upload",
            "source_id": "priv_loc_123",
            "title": "Track",
            "artist": "Local Artist",
            "duration": 210.0
        }

        with patch("subprocess.run", side_effect=fake_subprocess_trap), \
             patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):

            with pytest.raises(PrivateUploadUnavailableError):
                await download_upload(upload_payload, dest_dir=local_track.parent)

        # Assert no search was ever invoked
        for cmd in invoked_commands:
            for arg in cmd:
                assert "ytsearch" not in str(arg), f"Security violation: ytsearch invoked: {arg}"

        # Compute SHA-256 AFTER
        hashes_after = compute_dir_hashes(music_dir)
        local_hash_after = hashes_after["Local Artist/Track.mp3"]

        assert local_hash_before == local_hash_after, "Local artist recording was altered!"
        assert hashes_before == hashes_after, "Filesystem modified during blocked upload!"

    @pytest.mark.asyncio
    async def test_wrong_video_id_preserves_hashes_and_destroys_staging(self, physical_test_environment):
        """
        Verify that if yt-dlp somehow returns a different video ID from the requested upload,
        staging is wiped and all local audio hashes remain identical.
        """
        env = physical_test_environment
        music_dir = env["music_dir"]
        track_a = env["track_a"]

        hashes_before = compute_dir_hashes(music_dir)

        def fake_subprocess_mismatch(cmd, **kwargs):
            id_idx = cmd.index("--print-to-file")
            id_file = Path(cmd[id_arg_idx := id_idx + 2])
            id_file.write_text("MISMATCHED_PUBLIC_ID\n")
            out_mp3 = Path(cmd[cmd.index("-o") + 1].replace(".%(ext)s", ".mp3"))
            out_mp3.write_bytes(b"MALICIOUS_SUBSTITUTED_AUDIO")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        upload_payload = {
            "source_type": "ytm_upload",
            "source_id": "EXPECTED_ID_123",
            "title": "Track",
            "artist": "Artist A",
            "duration": 180.0
        }

        import subprocess
        with patch("subprocess.run", side_effect=fake_subprocess_mismatch), \
             patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):

            with pytest.raises(DownloadIntegrityError):
                await download_upload(upload_payload, dest_dir=track_a.parent)

        hashes_after = compute_dir_hashes(music_dir)
        assert hashes_before == hashes_after, "Local hashes changed on mismatched ID!"

        # Ensure staging contains no leftover files
        staging_files = list(env["staging_dir"].glob("*"))
        assert len(staging_files) == 0, f"Staging files were not destroyed: {staging_files}"

    @pytest.mark.asyncio
    async def test_existing_file_automatic_replacement_blocked(self, physical_test_environment):
        """
        Verify that commit_staged_file_to_destination strictly blocks automatic replacement
        when allow_automatic_replacement is False, even if a verified download succeeded.
        """
        env = physical_test_environment
        music_dir = env["music_dir"]
        same_title_track = env["same_title_track"]

        hashes_before = compute_dir_hashes(music_dir)

        # Create staged dummy verified download
        staged = env["staging_dir"] / "ytm_verified.mp3"
        staged.write_bytes(b"VERIFIED_DOWNLOAD_CONTENT_FROM_CLOUD")

        # Attempt to commit over existing file
        with pytest.raises(FileExistsError):
            commit_staged_file_to_destination(
                staged_file=staged,
                destination_file=same_title_track,
                allow_overwrite=False
            )

        hashes_after = compute_dir_hashes(music_dir)
        assert hashes_before == hashes_after, "File was overwritten despite automatic replacement being disabled!"

    @pytest.mark.asyncio
    async def test_authorized_manual_replacement_creates_immutable_backup(self, physical_test_environment):
        """
        Verify that when a replacement is EXPLICITLY authorized (allow_overwrite=True and allow_automatic_replacement=True):
        1. An immutable .bak backup is created in backups_dir.
        2. The backup hash matches the original file hash.
        3. The destination file is updated with new content.
        """
        env = physical_test_environment
        music_dir = env["music_dir"]
        backups_dir = env["backups_dir"]
        track_a = env["track_a"]

        original_bytes = track_a.read_bytes()
        orig_sha = hashlib.sha256(original_bytes).hexdigest()

        staged = env["staging_dir"] / "ytm_new_verified.mp3"
        new_content = b"AUTHENTIC_NEW_REMASTER_FROM_CLOUD_LOCKER"
        staged.write_bytes(new_content)

        with patch.object(settings, "allow_automatic_replacement", True):
            committed = commit_staged_file_to_destination(
                staged_file=staged,
                destination_file=track_a,
                allow_overwrite=True,
                replacement_source_id="auth_upload_source_999"
            )

        assert committed == track_a
        assert track_a.read_bytes() == new_content

        # Verify .bak backup
        backups = list(backups_dir.glob("Track_*.bak"))
        assert len(backups) == 1
        assert backups[0].read_bytes() == original_bytes
        assert hashlib.sha256(backups[0].read_bytes()).hexdigest() == orig_sha
