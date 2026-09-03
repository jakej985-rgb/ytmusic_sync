import pytest
import subprocess
from unittest.mock import patch, MagicMock
from pathlib import Path
from ytm_service.database import Database, db
from ytm_service.downloader import (
    _download_sync,
    download_upload,
    download_catalog_track,
    download_ytm_upload,
    PrivateUploadUnavailableError,
    DownloadIntegrityError,
    commit_staged_file_to_destination
)
from ytm_service.models import SyncDecision, VerificationStatus, UploadStatus
from ytm_service.matcher import evaluate_sync_decision
from ytm_service.audio_fingerprint import (
    extract_audio_characteristics,
    compare_audio_characteristics,
    verify_audio_integrity,
    AudioFingerprintMismatchError
)


@pytest.fixture
async def temp_db(tmp_path):
    test_db_path = tmp_path / "test.db"
    test_db = Database(db_path=test_db_path)
    await test_db.init_db()
    with patch("ytm_service.database.db", test_db), \
         patch("ytm_service.playlist_downloader.db", test_db), \
         patch("ytm_service.matcher.db", test_db), \
         patch("ytm_service.downloader.db", test_db), \
         patch("ytm_service.main.db", test_db):
        yield test_db


@pytest.mark.asyncio
async def test_download_upload_source_type_validation():
    # Contradictory source_type
    with pytest.raises(ValueError, match="Contradictory source_type"):
        await download_upload({"source_type": "catalog", "source_id": "test12345"})

    # Missing source_id
    with pytest.raises(ValueError, match="Missing source_id"):
        await download_upload({"source_type": "ytm_upload"})


@pytest.mark.asyncio
async def test_download_catalog_track_source_type_validation():
    # Contradictory source_type
    with pytest.raises(ValueError, match="Contradictory source_type"):
        await download_catalog_track({"source_type": "ytm_upload", "source_id": "test12345"})

    # Missing source_id
    with pytest.raises(ValueError, match="Missing source_id"):
        await download_catalog_track({"source_type": "catalog"})


def test_download_sync_rejects_fallback_query_for_upload(tmp_path):
    target = tmp_path / "song.mp3"
    with pytest.raises(ValueError, match="Upload downloads cannot specify or use fallback search queries"):
        _download_sync("test12345", target, fallback_query="Some Artist Song", source_type="ytm_upload")


def test_download_sync_never_uses_ytsearch_for_upload(tmp_path):
    target = tmp_path / "song.mp3"

    called_urls = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        # Extract target url passed as last argument to yt-dlp
        target_url = cmd[-1]
        called_urls.append(target_url)
        # Simulate failure to test all tried URLs
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stderr = "Private video. Sign in required."
        mock_res.stdout = ""
        return mock_res

    with patch("subprocess.run", side_effect=mock_subprocess_run), \
         patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):
        with pytest.raises(PrivateUploadUnavailableError, match="private upload unavailable or auth failed"):
            _download_sync("valid_vid_123", target, fallback_query=None, source_type="ytm_upload")

    # Verify only direct watch URLs were attempted, and NO ytsearch query was ever added
    assert len(called_urls) > 0
    for url in called_urls:
        assert not url.startswith("ytsearch"), f"Dangerous ytsearch fallback was invoked: {url}"
        assert "valid_vid_123" in url


@pytest.mark.asyncio
async def test_ytm_upload_retains_authoritative_identity(temp_db):
    upload_data = {
        "entity_id": "ent_authoritative_123",
        "video_id": "tAXJ0semc4E",
        "title": "Big 8 - Track Name",
        "artist": "Big 8",
        "album": "Demo Tape",
        "duration": 227.0
    }
    await temp_db.upsert_ytm_upload(upload_data)

    retrieved = await temp_db.get_ytm_upload_by_entity_id("ent_authoritative_123")
    assert retrieved is not None
    assert retrieved.source_type == "ytm_upload"
    assert retrieved.upload_video_id == "tAXJ0semc4E"
    assert retrieved.upload_url == "https://www.youtube.com/watch?v=tAXJ0semc4E"
    assert retrieved.video_id == "tAXJ0semc4E"

    by_vid = await temp_db.get_ytm_upload_by_video_id("tAXJ0semc4E")
    assert by_vid is not None
    assert by_vid.entity_id == "ent_authoritative_123"


@pytest.mark.asyncio
async def test_playlist_sync_blocks_overwriting_existing_local_file(tmp_path, temp_db):
    from unittest.mock import AsyncMock
    from ytm_service.playlist_downloader import download_and_upload_playlist_track

    dest_dir = tmp_path / "music"
    dest_dir.mkdir()
    artist_dir = dest_dir / "Big 8" / "Album"
    artist_dir.mkdir(parents=True)
    existing_file = artist_dir / "Track Name.mp3"
    existing_file.write_bytes(b"ORIGINAL ARTIST RECORDING NEVER OVERWRITE")

    dummy_downloaded = tmp_path / "incoming_audio.mp3"
    dummy_downloaded.write_bytes(b"GENERIC YOUTUBE AUDIO CONTENT")

    with patch("ytm_service.playlist_downloader._download_sync", return_value=dummy_downloaded), \
         patch("ytm_service.playlist_downloader.ytm_client.upload_file", AsyncMock(return_value={"success": True})), \
         patch("ytm_service.playlist_downloader.write_metadata_tags"), \
         patch("ytm_service.playlist_downloader.settings.allowed_fs_roots", [dest_dir]):

        res = await download_and_upload_playlist_track(
            video_id="dQw4w9WgXcQ",
            raw_title="Track Name",
            raw_artist="Big 8",
            raw_album="Album",
            destination_dir=dest_dir,
            enrich_metadata=False,
            require_full_match=False
        )

        assert res["status"] == "success"
        # The existing file on disk must STILL contain the original recording!
        assert existing_file.read_bytes() == b"ORIGINAL ARTIST RECORDING NEVER OVERWRITE"


@pytest.mark.asyncio
async def test_private_upload_auth_failure_preserves_local_files_and_fails_closed(tmp_path, temp_db):
    from unittest.mock import AsyncMock
    from httpx import AsyncClient, ASGITransport
    from ytm_service.main import app

    # Insert upload entity into DB
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_priv_fail",
        "video_id": "tAXJ0semc4E",
        "title": "Private Artist Track",
        "artist": "Local Artist",
        "album": "Vault"
    })

    # Local music file that must NOT be touched or replaced
    local_music = tmp_path / "music"
    local_music.mkdir()
    local_track = local_music / "Local Artist - Track.mp3"
    local_track.write_bytes(b"LOCAL ARTIST FILE CONTENT - UNTOUCHED")

    mock_delete = AsyncMock(return_value={"success": True})
    mock_upload = AsyncMock(return_value={"success": True})

    with patch("ytm_service.main.download_ytm_upload", side_effect=PrivateUploadUnavailableError("private upload unavailable or auth failed (Private video. Sign in required.)")), \
         patch("ytm_service.main.ytm_client.delete_upload", mock_delete), \
         patch("ytm_service.main.ytm_client.upload_file", mock_upload):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ytm/uploads/ent_priv_fail/replace",
                json={
                    "title": "Private Artist Track",
                    "artist": "Local Artist",
                    "album": "Vault"
                }
            )

            # Must fail closed with 500
            assert resp.status_code == 500
            assert "private upload unavailable or auth failed" in resp.json()["detail"]

            # Must NOT delete existing upload on YTM
            mock_delete.assert_not_called()

            # Must NOT upload any substitute file to YTM
            mock_upload.assert_not_called()

            # Local file must remain completely intact
            assert local_track.read_bytes() == b"LOCAL ARTIST FILE CONTENT - UNTOUCHED"


def test_hard_source_integrity_gate_rejects_mismatched_video_id_and_deletes_staging(tmp_path):
    target = tmp_path / "staging" / "ytm_tAXJ0semc4E.mp3"
    target.parent.mkdir(parents=True, exist_ok=True)

    def mock_yt_dlp_mismatch(cmd, *args, **kwargs):
        # yt-dlp downloaded a catalog track (dQw4w9WgXcQ) instead of expected upload (tAXJ0semc4E)
        for idx, arg in enumerate(cmd):
            if arg == "--print-to-file":
                id_file_path = Path(cmd[idx + 2])
                id_file_path.write_text("dQw4w9WgXcQ", encoding="utf-8")
                break
        
        # Create staging file simulating downloaded audio
        target.write_bytes(b"WRONG AUDIO FROM PUBLIC CATALOG")

        res = MagicMock()
        res.returncode = 0
        res.stderr = ""
        res.stdout = ""
        return res

    with patch("subprocess.run", side_effect=mock_yt_dlp_mismatch), \
         patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):

        with pytest.raises(DownloadIntegrityError, match="expected video ID 'tAXJ0semc4E', but received 'dQw4w9WgXcQ'"):
            _download_sync(
                video_id="tAXJ0semc4E",
                output_path=target,
                fallback_query=None,
                source_type="ytm_upload"
            )

    # Hard source-integrity gate must have destroyed the staging file!
    assert not target.exists(), "Staging file with mismatched video ID was NOT deleted!"


def test_commit_staged_file_to_destination_refuses_overwrite(tmp_path):
    staged = tmp_path / "staging" / "test_song.mp3"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"NEW DOWNLOADED AUDIO FROM YOUTUBE")

    dest = tmp_path / "music" / "Artist" / "Album" / "ExistingSong.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"EXISTING ARTIST LOCAL RECORDING")

    with pytest.raises(FileExistsError, match="Destination file already exists"):
        commit_staged_file_to_destination(staged, dest, allow_overwrite=False)

    # Destination MUST remain untouched
    assert dest.read_bytes() == b"EXISTING ARTIST LOCAL RECORDING"


def test_commit_staged_file_to_destination_success_when_new(tmp_path):
    staged = tmp_path / "staging" / "test_song.mp3"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"NEW VERIFIED AUDIO")

    dest = tmp_path / "music" / "Artist" / "Album" / "NewSong.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)

    committed = commit_staged_file_to_destination(staged, dest, allow_overwrite=False)
    assert committed == dest
    assert dest.exists()
    assert dest.read_bytes() == b"NEW VERIFIED AUDIO"


def test_phase6_existing_file_blocked_when_global_replacement_is_false(tmp_path):
    from ytm_service.config import settings
    staged = tmp_path / "staging" / "test_song.mp3"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"INCOMING AUDIO")

    dest = tmp_path / "music" / "Artist" / "Album" / "Existing.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"ORIGINAL LOCAL RECORDING")

    # Even if caller passes allow_overwrite=True, global setting default is False -> MUST BLOCK
    with patch.object(settings, "allow_automatic_replacement", False):
        with pytest.raises(FileExistsError, match="Automatic replacement blocked by write policy"):
            commit_staged_file_to_destination(staged, dest, allow_overwrite=True)

    assert dest.read_bytes() == b"ORIGINAL LOCAL RECORDING"


def test_phase6_existing_file_replaced_only_when_both_caller_and_global_permit(tmp_path):
    from ytm_service.config import settings
    staged = tmp_path / "staging" / "test_song.mp3"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"INTENTIONAL UPGRADE AUDIO")

    dest = tmp_path / "music" / "Artist" / "Album" / "Existing.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"ORIGINAL LOCAL RECORDING")

    with patch.object(settings, "allow_automatic_replacement", True):
        commit_staged_file_to_destination(staged, dest, allow_overwrite=True)

    assert dest.read_bytes() == b"INTENTIONAL UPGRADE AUDIO"


def test_phase7_evaluate_sync_decision_all_branches():
    # 1. SAFE: exact identity verified
    decision, reason = evaluate_sync_decision(
        expected_id="vid123",
        actual_id="vid123",
        source_type="ytm_upload"
    )
    assert decision == SyncDecision.SAFE
    assert "Exact upload identity verified" in reason

    # 2. REVIEW: compatible metadata, but identity unknown or unverified
    decision, reason = evaluate_sync_decision(
        metadata_match="exact",
        expected_id=None,
        actual_id="diff_vid"
    )
    assert decision == SyncDecision.REVIEW
    assert "source identity not authoritatively established" in reason

    # 3. BLOCKED: private upload inaccessible
    decision, reason = evaluate_sync_decision(is_private_inaccessible=True)
    assert decision == SyncDecision.BLOCKED
    assert "Private upload stream inaccessible" in reason

    # 4. BLOCKED: wrong video ID
    decision, reason = evaluate_sync_decision(expected_id="exp1", actual_id="act2")
    assert decision == SyncDecision.BLOCKED
    assert "Wrong video ID" in reason

    # 5. BLOCKED: ytsearch fallback
    decision, reason = evaluate_sync_decision(has_search_fallback=True)
    assert decision == SyncDecision.BLOCKED
    assert "ytsearch fallback prohibited" in reason

    # 6. BLOCKED: unknown source type
    decision, reason = evaluate_sync_decision(source_type="unsupported_source")
    assert decision == SyncDecision.BLOCKED
    assert "unknown or invalid" in reason

    # 7. BLOCKED: destination exists without overwrite
    decision, reason = evaluate_sync_decision(destination_exists=True, allow_overwrite=False)
    assert decision == SyncDecision.BLOCKED
    assert "already exists" in reason

    # 8. BLOCKED: audio validation failed
    decision, reason = evaluate_sync_decision(audio_valid=False)
    assert decision == SyncDecision.BLOCKED
    assert "Audio integrity validation failed" in reason


@pytest.mark.asyncio
async def test_phase7_matcher_persists_sync_decision_in_database(temp_db):
    from ytm_service.matcher import matcher
    from ytm_service.models import MusicFile

    # Local file
    loc_id = await temp_db.upsert_music_file({
        "path": "/music/Band/Song.mp3",
        "filename": "Song.mp3",
        "title": "Local Song",
        "artist": "Local Band",
        "format": "mp3",
        "file_size": 1024,
        "modified_time": 1000.0,
        "duration": 180.0
    })

    # YTM Upload with matching metadata
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_sync_dec_1",
        "video_id": "vid_dec_1",
        "upload_video_id": "vid_dec_1",
        "source_type": "ytm_upload",
        "title": "Local Song",
        "artist": "Local Band",
        "duration": 180.0
    })

    files = await temp_db.get_music_files()
    assert len(files) > 0
    loc = files[0]
    res = await matcher.match_single_file(loc)

    assert res["matched"] is True
    assert "sync_decision" in res
    assert res["sync_decision"] in ("SAFE", "REVIEW")

    # Verify persisted in database matches table
    async with temp_db.get_connection() as conn:
        async with conn.execute("SELECT sync_decision, decision_reason FROM matches WHERE music_file_id = ?", (loc_id,)) as cursor:
            row = await cursor.fetchone()
            assert row is not None
            assert row["sync_decision"] == res["sync_decision"]
            assert row["decision_reason"] is not None


def test_phase8_audio_fingerprint_duration_comparison():
    # Expected: Big 8 - My Track (3:47 = 227s)
    # Downloaded: Big 8 - My Track (4:01 = 241s)
    expected = {"duration": 227.0}
    actual = {"duration": 241.0}

    is_valid, reason = compare_audio_characteristics(expected, actual, max_duration_diff=4.0)
    assert is_valid is False
    assert "Duration mismatch" in reason
    assert "expected 227.0s, got 241.0s" in reason

    # Within tolerance (e.g. 227s vs 228.5s)
    is_valid_ok, reason_ok = compare_audio_characteristics(expected, {"duration": 228.5}, max_duration_diff=4.0)
    assert is_valid_ok is True


@pytest.mark.asyncio
async def test_phase8_download_upload_blocks_and_destroys_staging_on_audio_mismatch(tmp_path):
    staged_target = tmp_path / "staging" / "ytm_tAXJ0semc4E.mp3"
    staged_target.parent.mkdir(parents=True, exist_ok=True)
    staged_target.write_bytes(b"DUMMY AUDIO")

    # Mock audio characteristics returning 241.0s (4:01) when 227.0s (3:47) was expected
    from ytm_service.audio_fingerprint import AudioCharacteristics
    mismatched_char = AudioCharacteristics(
        duration=241.0,
        codec="mp3",
        sample_rate=44100,
        channels=2,
        bitrate=192000
    )

    with patch("ytm_service.downloader._download_sync", return_value=staged_target), \
         patch("ytm_service.audio_fingerprint.extract_audio_characteristics", return_value=mismatched_char):

        with pytest.raises(AudioFingerprintMismatchError, match="Duration mismatch: expected 227.0s, got 241.0s"):
            await download_upload({
                "source_id": "tAXJ0semc4E",
                "source_type": "ytm_upload",
                "duration": 227.0
            })

    # Staging file must be destroyed!
    assert not staged_target.exists(), "Staged file with audio fingerprint mismatch was not destroyed!"


@pytest.mark.asyncio
async def test_phase9_file_replacement_creates_backup_and_audit_trail(tmp_path, temp_db):
    import hashlib
    from ytm_service.config import settings

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    dest_file = tmp_path / "music" / "LocalArtist" / "Album" / "OriginalSong.mp3"
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    original_content = b"PRECIOUS LOCAL ARTIST RECORDING NEVER LOSE THIS"
    dest_file.write_bytes(original_content)
    expected_sha256 = hashlib.sha256(original_content).hexdigest()

    staged_file = tmp_path / "staging" / "ytm_replacement.mp3"
    staged_file.parent.mkdir(parents=True, exist_ok=True)
    staged_file.write_bytes(b"REPLACEMENT AUDIO FILE")

    with patch.object(settings, "allow_automatic_replacement", True), \
         patch.object(settings, "backups_dir", backup_dir), \
         patch("ytm_service.downloader.db", temp_db):

        committed = commit_staged_file_to_destination(
            staged_file=staged_file,
            destination_file=dest_file,
            allow_overwrite=True,
            replacement_source_id="ytm_new_upgrade_999"
        )
        assert committed == dest_file
        assert dest_file.read_bytes() == b"REPLACEMENT AUDIO FILE"

    # 1. Verify pre-replacement backup was saved to disk
    backups = list(backup_dir.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == original_content

    # 2. Verify audit trail in SQLite file_replacements table
    replacements = await temp_db.get_file_replacements()
    assert len(replacements) >= 1
    record = replacements[0]
    assert record["original_path"] == str(dest_file)
    assert record["original_sha256"] == expected_sha256
    assert record["original_size"] == len(original_content)
    assert record["replacement_source_id"] == "ytm_new_upgrade_999"
    assert record["backup_path"] == str(backups[0])
    assert record["replacement_timestamp"] is not None


@pytest.mark.asyncio
async def test_phase10_database_model_changes(temp_db):
    # 1. Insert a test music file
    await temp_db.upsert_music_file({
        "path": "/music/Phase10Artist/Track01.mp3",
        "filename": "Track01.mp3",
        "artist": "Phase10Artist",
        "album": "IntegrityAlbum",
        "title": "Verified Track",
        "duration": 215.0,
        "format": "mp3",
        "file_size": 5000000,
        "modified_time": 1700000000.0,
        "file_hash": "original_hash_12345"
    })
    files = await temp_db.get_music_files()
    assert len(files) > 0
    mf_id = files[0].id

    # 2. Verify all Phase 10 statuses exist
    assert VerificationStatus.PENDING == "PENDING"
    assert VerificationStatus.DOWNLOADING == "DOWNLOADING"
    assert VerificationStatus.VERIFYING == "VERIFYING"
    assert VerificationStatus.VERIFIED == "VERIFIED"
    assert VerificationStatus.REVIEW_REQUIRED == "REVIEW_REQUIRED"
    assert VerificationStatus.FAILED == "FAILED"
    assert VerificationStatus.BLOCKED == "BLOCKED"

    # 3. Create sync_job with explicit source/integrity fields
    job_id = await temp_db.create_sync_job(
        music_file_id=mf_id,
        source_type="ytm_upload",
        source_id="upload_source_xyz",
        source_url="https://music.youtube.com/watch?v=upload_source_xyz",
        expected_duration=215.0,
        original_file_hash="original_hash_12345",
        replacement_allowed=False,
        verification_status=VerificationStatus.PENDING
    )
    assert job_id > 0

    # 4. Fetch job and verify initial Phase 10 fields
    job = await temp_db.get_sync_job_by_id(job_id)
    assert job is not None
    assert job.source_type == "ytm_upload"
    assert job.source_id == "upload_source_xyz"
    assert job.source_url == "https://music.youtube.com/watch?v=upload_source_xyz"
    assert job.expected_duration == 215.0
    assert job.original_file_hash == "original_hash_12345"
    assert job.replacement_allowed is False
    assert job.verified is False
    assert job.verification_status == "PENDING"
    assert job.downloaded_source_id is None

    # 5. Update job with verification results
    await temp_db.update_sync_job(
        job_id=job_id,
        status=UploadStatus.VERIFIED,
        downloaded_source_id="upload_source_xyz",
        verified=True,
        verification_status=VerificationStatus.VERIFIED,
        verification_reason="PCM acoustic hash match and duration verified",
        downloaded_file_hash="pcm_hash_abcde"
    )

    # 6. Verify updated job record in sync history
    updated_job = await temp_db.get_sync_job_by_id(job_id)
    assert updated_job.status in ("verified", "VERIFIED")
    assert updated_job.downloaded_source_id == "upload_source_xyz"
    assert updated_job.verified is True
    assert updated_job.verification_status == "VERIFIED"
    assert updated_job.verification_reason == "PCM acoustic hash match and duration verified"
    assert updated_job.downloaded_file_hash == "pcm_hash_abcde"

    # 7. Verify sync history returns the enriched Phase 10 model
    history = await temp_db.get_sync_history()
    assert len(history) >= 1
    hist_job = next(j for j in history if j.id == job_id)
    assert hist_job.verified is True
    assert hist_job.verification_status == "VERIFIED"
    assert hist_job.source_type == "ytm_upload"
    assert hist_job.expected_duration == 215.0


def test_phase11_explicit_logging_for_upload_downloads(tmp_path, caplog):
    import logging
    import subprocess
    caplog.set_level(logging.INFO)

    output_path = tmp_path / "test_out.mp3"

    # 1. Test SUCCESS logging
    def fake_subprocess_run_success(cmd, **kwargs):
        # Write id_file
        id_arg_idx = cmd.index("--print-to-file")
        id_path = Path(cmd[id_arg_idx + 2])
        id_path.write_text("tAXJ0semc4E\n")
        # Write dummy mp3
        out_mp3 = output_path.with_suffix(".mp3")
        out_mp3.write_bytes(b"TEST MP3 DATA")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_subprocess_run_success):
        _download_sync("tAXJ0semc4E", output_path, None, "ytm_upload")

    assert "UPLOAD DOWNLOAD START" in caplog.text
    assert "source_type=ytm_upload" in caplog.text
    assert "expected_video_id=tAXJ0semc4E" in caplog.text
    assert "UPLOAD DOWNLOAD SUCCESS" in caplog.text
    assert "actual_video_id=tAXJ0semc4E" in caplog.text
    assert "verification=PASS" in caplog.text

    # 2. Test SOURCE_ID_MISMATCH logging
    caplog.clear()
    def fake_subprocess_run_mismatch(cmd, **kwargs):
        id_arg_idx = cmd.index("--print-to-file")
        id_path = Path(cmd[id_arg_idx + 2])
        id_path.write_text("DIFFERENT_CATALOG_ID\n")
        out_mp3 = output_path.with_suffix(".mp3")
        out_mp3.write_bytes(b"TEST MP3 DATA")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_subprocess_run_mismatch):
        with pytest.raises(DownloadIntegrityError):
            _download_sync("tAXJ0semc4E", output_path, None, "ytm_upload")

    assert "UPLOAD DOWNLOAD BLOCKED" in caplog.text
    assert "reason=SOURCE_ID_MISMATCH" in caplog.text
    assert "expected_video_id=tAXJ0semc4E" in caplog.text
    assert "actual_video_id=DIFFERENT_CATALOG_ID" in caplog.text

    # 3. Test PRIVATE_UPLOAD_UNAVAILABLE logging
    caplog.clear()
    def fake_subprocess_run_auth_fail(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP Error 403: Private video")

    with patch("subprocess.run", side_effect=fake_subprocess_run_auth_fail):
        with pytest.raises(PrivateUploadUnavailableError):
            _download_sync("tAXJ0semc4E", output_path, None, "ytm_upload")

    assert "UPLOAD DOWNLOAD BLOCKED" in caplog.text
    assert "reason=PRIVATE_UPLOAD_UNAVAILABLE" in caplog.text
    assert "fallback_search=DISABLED" in caplog.text
    assert "replacement=DISABLED" in caplog.text
    assert "expected_video_id=tAXJ0semc4E" in caplog.text


class TestUploadIncidentRegressionSuite:
    """
    Phase 12 — Regression Tests Specifically for the Incident Failure Modes
    Ensures that an upload locker item can NEVER silently become a catalog track
    or overwrite a local file with unverified audio.
    """

    @pytest.mark.asyncio
    async def test_regression_a_private_upload_auth_failure_no_search_no_replacement(self, tmp_path):
        """
        Test A — private upload:
        private upload -> authentication failure -> download fails -> NO catalog search -> NO file replacement
        """
        dest_dir = tmp_path / "music"
        dest_dir.mkdir()
        local_file = dest_dir / "ExistingSong.mp3"
        local_content = b"PRECIOUS EXISTING ARTIST TRACK"
        local_file.write_bytes(local_content)

        called_cmds = []

        def fake_subprocess_auth_fail(cmd, **kwargs):
            called_cmds.append(cmd)
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stderr = "ERROR: [youtube] tAXJ0semc4E: Private video. Sign in required."
            mock_res.stdout = ""
            return mock_res

        with patch("subprocess.run", side_effect=fake_subprocess_auth_fail), \
             patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):

            with pytest.raises(PrivateUploadUnavailableError):
                await download_upload(
                    {"source_type": "ytm_upload", "source_id": "tAXJ0semc4E"},
                    dest_dir=dest_dir
                )

        # 1. Verify NO catalog search was ever invoked
        for cmd in called_cmds:
            for arg in cmd:
                assert not str(arg).startswith("ytsearch"), f"Forbidden search fallback was called: {arg}"

        # 2. Verify existing file was NOT modified or replaced
        assert local_file.read_bytes() == local_content

    @pytest.mark.asyncio
    async def test_regression_b_same_artist_title_never_chooses_public_video(self, tmp_path):
        """
        Test B — same artist/title:
        private upload = Artist A / Song X
        public YouTube = Artist A / Song X
        upload inaccessible -> must NOT choose public video
        """
        # Upload record metadata matches a public song's artist and title
        upload_record = {
            "source_type": "ytm_upload",
            "source_id": "tAXJ0semc4E",
            "artist": "Artist A",
            "title": "Song X"
        }

        with patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stderr="Private video", stdout="")):

            with pytest.raises(PrivateUploadUnavailableError):
                await download_upload(upload_record)

    def test_regression_c_wrong_video_id_blocked(self, tmp_path):
        """
        Test C — wrong video ID:
        Mock: expected=tAXJ0semc4E, actual=different123
        Expected: BLOCKED, DownloadIntegrityError, staging deleted
        """
        out_target = tmp_path / "staging" / "test.mp3"
        out_target.parent.mkdir(parents=True, exist_ok=True)

        def fake_subprocess_different_id(cmd, **kwargs):
            id_idx = cmd.index("--print-to-file")
            Path(cmd[id_idx + 2]).write_text("different123\n")
            out_target.write_bytes(b"WRONG AUDIO FROM PUBLIC SEARCH")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fake_subprocess_different_id):
            with pytest.raises(DownloadIntegrityError) as exc_info:
                _download_sync("tAXJ0semc4E", out_target, None, "ytm_upload")

            assert "expected video ID 'tAXJ0semc4E'" in str(exc_info.value)
            assert "received 'different123'" in str(exc_info.value)

        # Staging file must be destroyed
        assert not out_target.exists()

    def test_regression_d_existing_local_file_preserved(self, tmp_path):
        """
        Test D — existing local file:
        destination exists, download verified
        Expected: original preserved, automatic replacement blocked
        """
        dest_file = tmp_path / "music" / "Artist" / "Track.mp3"
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        original_audio = b"ORIGINAL UNTOUCHED AUDIO CONTENT"
        dest_file.write_bytes(original_audio)

        staged_verified = tmp_path / "staging" / "incoming.mp3"
        staged_verified.parent.mkdir(parents=True, exist_ok=True)
        staged_verified.write_bytes(b"NEW VERIFIED AUDIO")

        # Without explicit allow_overwrite + allow_automatic_replacement, must block
        with pytest.raises(FileExistsError, match="Automatic replacement blocked by write policy"):
            commit_staged_file_to_destination(
                staged_file=staged_verified,
                destination_file=dest_file,
                allow_overwrite=False
            )

        # Original file is preserved on disk
        assert dest_file.read_bytes() == original_audio

    @pytest.mark.asyncio
    async def test_regression_e_download_ytm_upload_can_never_invoke_ytsearch(self, tmp_path):
        """
        Test E — search fallback:
        Explicitly assert: download_ytm_upload() can never invoke ytsearch1:
        """
        executed_commands = []

        def fake_subprocess(cmd, **kwargs):
            executed_commands.append(cmd)
            mock_res = MagicMock()
            mock_res.returncode = 1
            mock_res.stderr = "Video unavailable"
            mock_res.stdout = ""
            return mock_res

        with patch("subprocess.run", side_effect=fake_subprocess), \
             patch("ytm_service.downloader._download_via_ytmusicapi", return_value=None):

            with pytest.raises(PrivateUploadUnavailableError):
                await download_ytm_upload("tAXJ0semc4E")

        assert len(executed_commands) > 0
        for cmd in executed_commands:
            for term in cmd:
                assert "ytsearch" not in str(term), f"CRITICAL SECURITY VIOLATION: ytsearch found in arguments: {term}"


@pytest.mark.asyncio
async def test_phase13_global_safety_switch_and_manual_confirmation(tmp_path, temp_db):
    from httpx import AsyncClient, ASGITransport
    from ytm_service.main import app
    from ytm_service.config import settings

    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {settings.api_key}"}

    # 1. Verify default in settings endpoint: allow_automatic_replacement is False
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/settings", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["allow_automatic_replacement"] is False

    # 2. Setup local song file on disk and in database
    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    local_song = music_dir / "Song.mp3"
    local_song.write_bytes(b"ORIGINAL LOCAL RECORDING")

    await temp_db.upsert_music_file({
        "path": str(local_song),
        "filename": "Song.mp3",
        "artist": "Big 8",
        "album": "The Album",
        "title": "My Track",
        "duration": 227.0,
        "format": "mp3",
        "file_size": len(b"ORIGINAL LOCAL RECORDING"),
        "modified_time": 1700000000.0,
    })
    files = await temp_db.get_music_files()
    local_mf_id = files[0].id

    # 3. Setup upload in database
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_test_replace_999",
        "video_id": "tAXJ0semc4E",
        "title": "My Track",
        "artist": "Big 8",
        "album": "The Album",
        "duration": 227.0
    })

    # 4. Test Preview Endpoint (POST /api/files/replace/preview)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        prev_res = await ac.post(
            "/api/files/replace/preview",
            json={
                "music_file_id": local_mf_id,
                "upload_entity_id": "ent_test_replace_999"
            },
            headers=headers
        )
        assert prev_res.status_code == 200
        preview = prev_res.json()
        assert preview["local_file"]["path"] == str(local_song)
        assert preview["local_file"]["sha256"] is not None
        assert preview["new_source"]["upload_id"] == "tAXJ0semc4E"
        assert preview["verification"]["upload_id_matches"] is True
        assert preview["verification"]["duration_matches"] is True
        assert preview["verification"]["audio_validation_passed"] is True
        assert preview["verification"]["status"] == "PASS"
        assert preview["settings"]["automatic_replacement_enabled"] is False
        assert preview["settings"]["manual_confirmation_required"] is True

    # 5. Test Execute Endpoint without confirm (must fail with 400)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        exec_fail = await ac.post(
            "/api/files/replace/execute",
            json={
                "music_file_id": local_mf_id,
                "upload_entity_id": "ent_test_replace_999",
                "confirm": False
            },
            headers=headers
        )
        assert exec_fail.status_code == 400
        assert "Explicit confirmation" in exec_fail.json()["detail"]

    # 6. Test Execute Endpoint with confirm=True
    dummy_staged = tmp_path / "staged_download.mp3"
    dummy_staged.write_bytes(b"REPLACEMENT VERIFIED DOWNLOAD FROM CLOUD")

    with patch("ytm_service.main.download_upload", return_value=dummy_staged):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            exec_ok = await ac.post(
                "/api/files/replace/execute",
                json={
                    "music_file_id": local_mf_id,
                    "upload_entity_id": "ent_test_replace_999",
                    "confirm": True
                },
                headers=headers
            )
            assert exec_ok.status_code == 200
            assert exec_ok.json()["status"] == "success"

    # Verify file was replaced
    assert local_song.read_bytes() == b"REPLACEMENT VERIFIED DOWNLOAD FROM CLOUD"
    # Verify pre-replacement backup was recorded
    replacements = await temp_db.get_file_replacements()
    assert len(replacements) >= 1
    assert replacements[0]["original_path"] == str(local_song)


@pytest.mark.asyncio
async def test_phase14_audit_flag_and_restore_replaced_files(tmp_path, temp_db):
    import hashlib
    from httpx import AsyncClient, ASGITransport
    from ytm_service.main import app
    from ytm_service.config import settings
    from ytm_service.recovery import (
        audit_and_flag_suspicious_files,
        restore_corrupted_file,
        POSSIBLY_CORRUPTED_STATUS,
        RESTORED_STATUS
    )

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 1. Setup local music file that was replaced
    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    corrupted_song = music_dir / "LocalArtistTrack.mp3"
    # File currently has bad/replaced content
    corrupted_song.write_bytes(b"BAD REPLACED CONTENT FROM YTSEARCH1")

    # Original good backup
    original_good_content = b"PRECIOUS LOCAL ARTIST MASTER RECORDING"
    good_sha256 = hashlib.sha256(original_good_content).hexdigest()
    backup_file = backup_dir / f"LocalArtistTrack_{good_sha256[:12]}.bak"
    backup_file.write_bytes(original_good_content)

    await temp_db.upsert_music_file({
        "path": str(corrupted_song),
        "filename": "LocalArtistTrack.mp3",
        "artist": "Local Artist",
        "album": "Master Tape",
        "title": "LocalArtistTrack",
        "duration": 210.0,
        "format": "mp3",
        "file_size": len(b"BAD REPLACED CONTENT FROM YTSEARCH1"),
        "modified_time": 1700000000.0,
    })

    # Record past replacement history in DB
    await temp_db.record_file_replacement(
        original_path=str(corrupted_song),
        original_sha256=good_sha256,
        original_size=len(original_good_content),
        original_mtime=1699999999.0,
        replacement_source_id="BAD_YTSEARCH_SOURCE",
        backup_path=str(backup_file)
    )

    with patch.object(settings, "backups_dir", backup_dir):
        # 2. Run Audit
        suspicious = await audit_and_flag_suspicious_files(database=temp_db)
        assert len(suspicious) >= 1
        found = next(s for s in suspicious if s["path"] == str(corrupted_song))
        assert found["verification_status"] == POSSIBLY_CORRUPTED_STATUS
        assert found["original_sha256"] == good_sha256
        assert found["replacement_source_id"] == "BAD_YTSEARCH_SOURCE"
        assert found["backup_available"] is True
        assert found["backup_path"] == str(backup_file)

        # 3. Verify that rescan does NOT overwrite POSSIBLY CORRUPTED status
        await temp_db.upsert_music_file({
            "path": str(corrupted_song),
            "filename": "LocalArtistTrack.mp3",
            "artist": "Local Artist",
            "album": "Master Tape",
            "title": "LocalArtistTrack",
            "duration": 210.0,
            "format": "mp3",
            "file_size": len(b"BAD REPLACED CONTENT FROM YTSEARCH1"),
            "modified_time": 1700000005.0,
        })
        async with temp_db.get_connection() as conn:
            async with conn.execute("SELECT verification_status FROM music_files WHERE path = ?", (str(corrupted_song),)) as cursor:
                row = await cursor.fetchone()
                assert row["verification_status"] == POSSIBLY_CORRUPTED_STATUS

        # 4. Restore corrupted file via API
        transport = ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {settings.api_key}"}
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.post(
                "/api/recovery/restore",
                json={"path": str(corrupted_song)},
                headers=headers
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert data["verification_status"] == RESTORED_STATUS

        # 5. Verify file on disk is now restored with original content
        assert corrupted_song.read_bytes() == original_good_content


@pytest.mark.asyncio
async def test_phase6_ytmusicapi_path_passes_through_unified_verification_gate(tmp_path):
    """
    Phase 6 / Blocker #1:
    Verify that even if _download_via_ytmusicapi succeeds in downloading a stream,
    it MUST pass through verify_downloaded_upload().
    If metadata/duration verification fails, it must destroy the staging file and raise DownloadIntegrityError.
    """
    output_path = tmp_path / "ytm_test_file.mp3"
    dummy_direct = tmp_path / "direct_download.mp3"
    dummy_direct.write_bytes(b"AUTHENTIC DIRECT STREAM FROM YTMUSICAPI")

    # 1. Successful verification when duration matches
    with patch("ytm_service.downloader._download_via_ytmusicapi", return_value=dummy_direct), \
         patch("ytm_service.audio_fingerprint.extract_audio_characteristics") as mock_extract:
        from ytm_service.audio_fingerprint import AudioCharacteristics
        mock_extract.return_value = AudioCharacteristics(
            duration=200.0,
            sample_rate=44100,
            channels=2,
            codec="mp3",
            bitrate=320000
        )

        res = _download_sync(
            video_id="directVid123",
            output_path=output_path,
            source_type="ytm_upload",
            expected_metadata={"duration": 200.0}
        )
        assert res == dummy_direct
        assert dummy_direct.exists()

    # 2. Failed verification when duration mismatches (e.g. 200s vs 250s)
    dummy_mismatch = tmp_path / "direct_mismatch.mp3"
    dummy_mismatch.write_bytes(b"WRONG DURATION AUDIO STREAM")

    with patch("ytm_service.downloader._download_via_ytmusicapi", return_value=dummy_mismatch), \
         patch("ytm_service.audio_fingerprint.extract_audio_characteristics") as mock_extract:
        from ytm_service.audio_fingerprint import AudioCharacteristics
        mock_extract.return_value = AudioCharacteristics(
            duration=250.0,
            sample_rate=44100,
            channels=2,
            codec="mp3",
            bitrate=320000
        )

        with pytest.raises(DownloadIntegrityError) as exc_info:
            _download_sync(
                video_id="directVid123",
                output_path=output_path,
                source_type="ytm_upload",
                expected_metadata={"duration": 200.0}
            )
        assert "Audio characteristic verification failed" in str(exc_info.value)
        assert not dummy_mismatch.exists(), "Staging file was not destroyed on direct ytmusicapi verification failure"














