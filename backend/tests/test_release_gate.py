import pytest
import subprocess
import hashlib
from unittest.mock import patch, MagicMock
from pathlib import Path
from ytm_service.database import Database
from ytm_service.downloader import (
    _download_sync,
    download_upload,
    download_ytm_upload,
    commit_staged_file_to_destination,
    PrivateUploadUnavailableError,
    DownloadIntegrityError
)
from ytm_service.models import SyncDecision, VerificationStatus, UploadStatus
from ytm_service.matcher import evaluate_sync_decision
from ytm_service.recovery import audit_and_flag_suspicious_files, POSSIBLY_CORRUPTED_STATUS
from ytm_service.config import settings


@pytest.fixture
async def gate_db(tmp_path):
    test_db = Database(db_path=tmp_path / "gate.db")
    await test_db.init_db()
    with patch("ytm_service.database.db", test_db), \
         patch("ytm_service.playlist_downloader.db", test_db), \
         patch("ytm_service.matcher.db", test_db), \
         patch("ytm_service.downloader.db", test_db), \
         patch("ytm_service.main.db", test_db):
        yield test_db


class TestReleaseGate:
    """
    Phase 15 — Release Gate Verification
    Validates that all 13 mandatory safety invariants are strictly satisfied.
    """

    # Gate 1: Upload downloads never use ytsearch
    @pytest.mark.asyncio
    async def test_gate_01_upload_downloads_never_use_ytsearch(self):
        called = []
        def fake_sub(cmd, **kwargs):
            called.append(cmd)
            return MagicMock(returncode=1, stderr="Sign in required", stdout="")

        with patch("subprocess.run", side_effect=fake_sub), \
             patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):
            with pytest.raises(PrivateUploadUnavailableError):
                await download_ytm_upload("testVideoId123")

        assert len(called) > 0
        for cmd in called:
            for term in cmd:
                assert "ytsearch" not in str(term), f"Release gate violation: ytsearch invoked: {term}"

    # Gate 2: Upload source identity is stored separately from metadata
    @pytest.mark.asyncio
    async def test_gate_02_upload_source_identity_stored_separately(self, gate_db):
        await gate_db.upsert_ytm_upload({
            "entity_id": "ent_sep_123",
            "video_id": "vid_authoritative_123",
            "title": "Track Title",
            "artist": "Artist Name",
            "duration": 220.0
        })
        upload = await gate_db.get_ytm_upload_by_entity_id("ent_sep_123")
        assert upload.source_type == "ytm_upload"
        assert upload.upload_video_id == "vid_authoritative_123"
        assert upload.upload_url == "https://www.youtube.com/watch?v=vid_authoritative_123"

    # Gate 3: Private-upload failure is fail-closed
    @pytest.mark.asyncio
    async def test_gate_03_private_upload_failure_is_fail_closed(self, tmp_path):
        dest_dir = tmp_path / "music"
        dest_dir.mkdir()
        local_f = dest_dir / "Song.mp3"
        local_f.write_bytes(b"UNTOUCHED LOCAL SONG")

        with patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="Private video")):
            with pytest.raises(PrivateUploadUnavailableError):
                await download_upload(
                    {"source_type": "ytm_upload", "source_id": "priv12345"},
                    dest_dir=dest_dir
                )
        assert local_f.read_bytes() == b"UNTOUCHED LOCAL SONG"

    # Gate 4: Download occurs only in staging
    @pytest.mark.asyncio
    async def test_gate_04_download_occurs_only_in_staging(self, tmp_path):
        staging_dir = settings.data_dir / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged_dummy = staging_dir / "ytm_staged_vid.mp3"
        staged_dummy.write_bytes(b"STAGED AUDIO")

        with patch("ytm_service.downloader._download_sync", return_value=staged_dummy):
            res_path = await download_upload({"source_type": "ytm_upload", "source_id": "staged_vid"})
            assert str(settings.data_dir / "staging") in str(res_path.parent)

    # Gate 5: Downloaded source ID is verified
    def test_gate_05_downloaded_source_id_is_verified(self, tmp_path):
        out_f = tmp_path / "staging" / "test.mp3"
        out_f.parent.mkdir(parents=True, exist_ok=True)

        def fake_sub(cmd, **kwargs):
            id_idx = cmd.index("--print-to-file")
            Path(cmd[id_idx + 2]).write_text("MISMATCHED_ID\n")
            out_f.write_bytes(b"WRONG DATA")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_sub):
            with pytest.raises(DownloadIntegrityError):
                _download_sync("EXPECTED_ID", out_f, None, "ytm_upload")
        assert not out_f.exists(), "Staging file was not destroyed on ID mismatch"

    # Gate 6: Existing files cannot be automatically overwritten
    def test_gate_06_existing_files_cannot_be_automatically_overwritten(self, tmp_path):
        dest_f = tmp_path / "Song.mp3"
        dest_f.write_bytes(b"ORIGINAL DATA")
        staged_f = tmp_path / "staged.mp3"
        staged_f.write_bytes(b"NEW DATA")

        with patch.object(settings, "allow_automatic_replacement", False):
            with pytest.raises(FileExistsError):
                commit_staged_file_to_destination(staged_f, dest_f, allow_overwrite=True)
        assert dest_f.read_bytes() == b"ORIGINAL DATA"

    # Gate 7: Replacement requires explicit authorization
    def test_gate_07_replacement_requires_explicit_authorization(self, tmp_path):
        dest_f = tmp_path / "Song.mp3"
        dest_f.write_bytes(b"ORIGINAL DATA")
        staged_f = tmp_path / "staged.mp3"
        staged_f.write_bytes(b"NEW DATA")

        with patch.object(settings, "allow_automatic_replacement", True):
            # caller did not explicitly authorize (allow_overwrite=False)
            with pytest.raises(FileExistsError):
                commit_staged_file_to_destination(staged_f, dest_f, allow_overwrite=False)
        assert dest_f.read_bytes() == b"ORIGINAL DATA"

    # Gate 8: Original SHA-256 is recorded before replacement
    @pytest.mark.asyncio
    async def test_gate_08_original_sha256_recorded_before_replacement(self, tmp_path, gate_db):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        dest_f = tmp_path / "Song.mp3"
        orig_bytes = b"ORIGINAL PRECIOUS DATA"
        dest_f.write_bytes(orig_bytes)
        orig_sha = hashlib.sha256(orig_bytes).hexdigest()

        staged_f = tmp_path / "staged.mp3"
        staged_f.write_bytes(b"NEW DATA")

        with patch.object(settings, "allow_automatic_replacement", True), \
             patch.object(settings, "backups_dir", backup_dir), \
             patch("ytm_service.downloader.db", gate_db):
            commit_staged_file_to_destination(
                staged_f, dest_f, allow_overwrite=True, replacement_source_id="new_source"
            )

        records = await gate_db.get_file_replacements()
        assert len(records) >= 1
        assert records[0]["original_sha256"] == orig_sha

    # Gate 9: Wrong-source regression tests exist
    def test_gate_09_wrong_source_regression_tests_exist(self):
        from tests.test_upload_integrity import TestUploadIncidentRegressionSuite
        assert hasattr(TestUploadIncidentRegressionSuite, "test_regression_c_wrong_video_id_blocked")

    # Gate 10: Same-title/local-artist test exists
    def test_gate_10_same_title_local_artist_test_exists(self):
        from tests.test_upload_integrity import TestUploadIncidentRegressionSuite
        assert hasattr(TestUploadIncidentRegressionSuite, "test_regression_b_same_artist_title_never_chooses_public_video")

    # Gate 11: Private-upload authentication-failure test exists
    def test_gate_11_private_upload_auth_failure_test_exists(self):
        from tests.test_upload_integrity import TestUploadIncidentRegressionSuite
        assert hasattr(TestUploadIncidentRegressionSuite, "test_regression_a_private_upload_auth_failure_no_search_no_replacement")

    # Gate 12: Sync history records why a download was accepted/rejected
    @pytest.mark.asyncio
    async def test_gate_12_sync_history_records_reasons(self, gate_db):
        await gate_db.upsert_music_file({
            "path": "/music/Track.mp3",
            "filename": "Track.mp3",
            "format": "mp3",
            "file_size": 1000,
            "modified_time": 100.0
        })
        mf = (await gate_db.get_music_files())[0]

        job_id = await gate_db.create_sync_job(
            music_file_id=mf.id,
            source_type="ytm_upload",
            source_id="test_vid_id",
            verification_status="PENDING"
        )
        await gate_db.update_sync_job(
            job_id=job_id,
            status=UploadStatus.VERIFIED,
            verified=True,
            verification_status=VerificationStatus.VERIFIED,
            verification_reason="Acoustic fingerprint and duration match upload locker record"
        )

        history = await gate_db.get_sync_history()
        assert len(history) >= 1
        job = history[0]
        assert job.verification_status == "VERIFIED"
        assert job.verification_reason == "Acoustic fingerprint and duration match upload locker record"

    # Gate 13: Previously replaced files are audited
    @pytest.mark.asyncio
    async def test_gate_13_previously_replaced_files_audited(self, tmp_path, gate_db):
        test_file = tmp_path / "ReplacedTrack.mp3"
        test_file.write_bytes(b"REPLACED AUDIO")

        await gate_db.upsert_music_file({
            "path": str(test_file),
            "filename": "ReplacedTrack.mp3",
            "format": "mp3",
            "file_size": 1000,
            "modified_time": 100.0
        })
        await gate_db.record_file_replacement(
            original_path=str(test_file),
            original_sha256="fake_orig_hash",
            original_size=1000,
            original_mtime=90.0,
            replacement_source_id="OLD_INCIDENT_SOURCE"
        )

        suspicious = await audit_and_flag_suspicious_files(database=gate_db)
        assert len(suspicious) >= 1
        assert any(s["path"] == str(test_file) for s in suspicious)
        assert any(s["verification_status"] == POSSIBLY_CORRUPTED_STATUS for s in suspicious)
