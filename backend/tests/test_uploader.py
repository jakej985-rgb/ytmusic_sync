import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from ytm_service.uploader import UploadQueueManager
from ytm_service.database import Database
from ytm_service.models import UploadStatus

@pytest.mark.asyncio
async def test_uploader_successful_flow(tmp_path: Path):
    db_instance = Database(tmp_path / "test_uploader.db")
    await db_instance.init_db()

    file_id = await db_instance.upsert_music_file({
        "path": str(tmp_path / "song.flac"),
        "filename": "song.flac",
        "artist": "Artist",
        "album": "Album",
        "title": "Song",
        "format": "FLAC",
        "file_size": 1000,
        "modified_time": 1000.0,
    })

    with patch("ytm_service.uploader.db", db_instance), \
         patch("ytm_service.uploader.ytm_client.upload_file", new_callable=AsyncMock) as mock_upload, \
         patch("ytm_service.uploader.settings.verify_uploads", False):

        mock_upload.return_value = {"success": True, "response": "STATUS_SUCCEEDED"}
        
        manager = UploadQueueManager()
        job_id = await manager.enqueue_song(file_id)
        assert job_id > 0

        # Wait for queue processing to complete
        await manager._process_queue_loop()

        job = await db_instance.get_next_queued_job()
        assert job is None

        history = await db_instance.get_sync_history()
        assert len(history) == 1
        assert history[0].status == UploadStatus.VERIFIED

@pytest.mark.asyncio
async def test_uploader_failed_after_retries(tmp_path: Path):
    db_instance = Database(tmp_path / "test_uploader_fail.db")
    await db_instance.init_db()

    file_id = await db_instance.upsert_music_file({
        "path": str(tmp_path / "failing.flac"),
        "filename": "failing.flac",
        "artist": "Artist",
        "album": "Album",
        "title": "Failing Song",
        "format": "FLAC",
        "file_size": 1000,
        "modified_time": 1000.0,
    })

    with patch("ytm_service.uploader.db", db_instance), \
         patch("ytm_service.uploader.ytm_client.upload_file", new_callable=AsyncMock) as mock_upload, \
         patch("ytm_service.uploader.settings.max_retries", 2), \
         patch("asyncio.sleep", new_callable=AsyncMock):

        mock_upload.side_effect = Exception("Connection Reset by Peer")
        
        manager = UploadQueueManager()
        job_id = await manager.enqueue_song(file_id)
        await manager._process_queue_loop()

        history = await db_instance.get_sync_history()
        assert len(history) == 1
        assert history[0].status == UploadStatus.FAILED
        assert "Connection Reset" in str(history[0].error)

@pytest.mark.asyncio
async def test_uploader_skips_duplicates(tmp_path: Path):
    db_instance = Database(tmp_path / "test_uploader_dup.db")
    await db_instance.init_db()

    file_id = await db_instance.upsert_music_file({
        "path": str(tmp_path / "dup.flac"),
        "filename": "dup.flac",
        "artist": "Artist",
        "album": "Album",
        "title": "Duplicate Song",
        "format": "FLAC",
        "file_size": 1000,
        "modified_time": 1000.0,
    })

    # Save match first
    await db_instance.save_match(file_id, "ytm_entity_123", "exact", 1.0)

    with patch("ytm_service.uploader.db", db_instance), \
         patch("ytm_service.uploader.ytm_client.upload_file", new_callable=AsyncMock) as mock_upload:
        
        manager = UploadQueueManager()
        job_id = await manager.enqueue_song(file_id)
        await manager._process_queue_loop()

        # upload_file should NEVER be called since it was already matched
        mock_upload.assert_not_called()

        history = await db_instance.get_sync_history()
        assert len(history) == 1
        assert history[0].status == UploadStatus.VERIFIED

@pytest.mark.asyncio
async def test_uploader_queue_persistence_across_restart(tmp_path: Path):
    # Simulate: 5 songs queued, 2 completed, 1 interrupted mid-upload, 2 pending
    db_instance = Database(tmp_path / "test_uploader_restart.db")
    await db_instance.init_db()

    file_ids = []
    for i in range(1, 6):
        fid = await db_instance.upsert_music_file({
            "path": str(tmp_path / f"song_{i}.flac"),
            "filename": f"song_{i}.flac",
            "title": f"Song {i}",
            "format": "FLAC",
            "file_size": 1000,
            "modified_time": 1000.0,
        })
        file_ids.append(fid)

    # Job 1 & 2: VERIFIED
    j1 = await db_instance.create_sync_job(file_ids[0])
    await db_instance.update_sync_job(j1, UploadStatus.VERIFIED)
    await db_instance.save_match(file_ids[0], "entity_1", "exact", 1.0)

    j2 = await db_instance.create_sync_job(file_ids[1])
    await db_instance.update_sync_job(j2, UploadStatus.VERIFIED)
    await db_instance.save_match(file_ids[1], "entity_2", "exact", 1.0)

    # Job 3: Was mid-upload (UPLOADING) when SIGTERM occurred
    j3 = await db_instance.create_sync_job(file_ids[2])
    await db_instance.update_sync_job(j3, UploadStatus.UPLOADING)

    # Job 4 & 5: QUEUED
    j4 = await db_instance.create_sync_job(file_ids[3])
    j5 = await db_instance.create_sync_job(file_ids[4])

    # Simulate Container Restart: init_db recovers interrupted state
    await db_instance.init_db()

    # Verify j3 was recovered to QUEUED
    recovered_j3 = await db_instance.get_sync_job_by_id(j3)
    assert recovered_j3.status == UploadStatus.QUEUED

    # Process remaining queue
    uploaded_files = []
    async def mock_upload(filepath):
        uploaded_files.append(Path(filepath).name)
        return {"success": True, "response": "STATUS_SUCCEEDED"}

    with patch("ytm_service.uploader.db", db_instance), \
         patch("ytm_service.uploader.ytm_client.upload_file", side_effect=mock_upload), \
         patch("ytm_service.uploader.settings.verify_uploads", False):

        manager = UploadQueueManager()
        await manager._process_queue_loop()

    # Ensure ONLY jobs 3, 4, and 5 were uploaded, and jobs 1 & 2 were NOT re-uploaded
    assert uploaded_files == ["song_3.flac", "song_4.flac", "song_5.flac"]

@pytest.mark.asyncio
async def test_uploader_handles_file_disappearing(tmp_path: Path):
    # Test failure mode: Song file disappears from filesystem before upload
    db_instance = Database(tmp_path / "test_uploader_missing_file.db")
    await db_instance.init_db()

    f1 = tmp_path / "exists.flac"
    f1.write_text("dummy")
    f2_missing = tmp_path / "deleted.flac"

    fid1 = await db_instance.upsert_music_file({
        "path": str(f1),
        "filename": "exists.flac",
        "title": "Existing Song",
        "format": "FLAC",
        "file_size": 1000,
        "modified_time": 1000.0,
    })

    fid2 = await db_instance.upsert_music_file({
        "path": str(f2_missing),
        "filename": "deleted.flac",
        "title": "Deleted Song",
        "format": "FLAC",
        "file_size": 1000,
        "modified_time": 1000.0,
    })

    uploaded = []
    async def mock_upload(filepath):
        if not Path(filepath).exists():
            return {"success": False, "error": "FileNotFoundError: File no longer exists on disk"}
        uploaded.append(Path(filepath).name)
        return {"success": True, "response": "STATUS_SUCCEEDED"}

    manager = UploadQueueManager()
    await db_instance.create_sync_job(fid2)  # Missing file queued first
    await db_instance.create_sync_job(fid1)  # Existing file queued second

    with patch("ytm_service.uploader.db", db_instance), \
         patch("ytm_service.uploader.ytm_client.upload_file", side_effect=mock_upload), \
         patch("ytm_service.uploader.settings.verify_uploads", False):

        await manager._process_queue_loop()

    # Deleted song marked FAILED, existing song continues and SUCCEEDS
    history = await db_instance.get_sync_history()
    assert len(history) == 2
    failed_job = [h for h in history if h.music_file_id == fid2][0]
    success_job = [h for h in history if h.music_file_id == fid1][0]

    assert failed_job.status == UploadStatus.FAILED
    assert success_job.status == UploadStatus.VERIFIED
    assert uploaded == ["exists.flac"]
