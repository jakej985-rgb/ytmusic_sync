import os
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport
from pathlib import Path

from ytm_service.main import app
from ytm_service.database import db
from ytm_service.models import UploadStatus
from ytm_service.uploader import queue_manager
from ytm_service import __version__
from ytm_service.metadata_tracker import metadata_tracker


@pytest.mark.asyncio
async def test_version_consolidation():
    """Verify FastAPI app and health endpoint use consolidated __version__."""
    assert app.version == __version__
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert data["version"] == __version__


@pytest.mark.asyncio
async def test_batch_limits_validation():
    """Verify batch endpoints enforce strict Pydantic upper and lower limits (422 on violation)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Batch upload: max 500
        res_oversized_upload = await ac.post("/api/upload/batch", json={"file_ids": list(range(501))})
        assert res_oversized_upload.status_code == 422

        res_empty_upload = await ac.post("/api/upload/batch", json={"file_ids": []})
        assert res_empty_upload.status_code == 422

        # 2. Batch delete songs: max 500
        res_oversized_del_songs = await ac.post("/api/songs/batch-delete", json={"file_ids": list(range(501))})
        assert res_oversized_del_songs.status_code == 422

        res_empty_del_songs = await ac.post("/api/songs/batch-delete", json={"file_ids": []})
        assert res_empty_del_songs.status_code == 422

        # 3. Batch delete YTM uploads: max 100
        res_oversized_del_ytm = await ac.post("/api/ytm/uploads/batch-delete", json={"entity_ids": [f"id_{i}" for i in range(101)]})
        assert res_oversized_del_ytm.status_code == 422

        res_empty_del_ytm = await ac.post("/api/ytm/uploads/batch-delete", json={"entity_ids": []})
        assert res_empty_del_ytm.status_code == 422

        # 4. Folders update: max 50
        res_oversized_folders = await ac.post("/api/folders", json={"folders": [f"/music/folder_{i}" for i in range(51)]})
        assert res_oversized_folders.status_code == 422


@pytest.mark.asyncio
async def test_startup_reconciliation_stuck_jobs(tmp_path):
    """Verify database job reconciliation resets interrupted jobs and fails exhausted ones."""
    file_id = await db.upsert_music_file({
        "path": str(tmp_path / "test_reconcile.mp3"),
        "filename": "test_reconcile.mp3",
        "artist": "Reconcile Artist",
        "album": "Reconcile Album",
        "title": "Reconcile Song",
        "duration": 180.0,
        "format": "MP3",
        "file_size": 1000,
        "modified_time": 1000.0,
        "file_hash": "hash_rec_1",
        "metadata_hash": "meta_rec_1"
    })

    # Insert stuck job 1: attempts = 1 (recoverable)
    job1_id = await db.create_sync_job(file_id)
    await db.update_sync_job(job1_id, UploadStatus.UPLOADING, increment_attempts=True)

    # Insert stuck job 2: attempts = 3 (exhausted)
    job2_id = await db.create_sync_job(file_id)
    await db.update_sync_job(job2_id, UploadStatus.UPLOADING, increment_attempts=True)
    async with db.get_connection() as conn:
        await conn.execute("UPDATE sync_jobs SET attempts = 3 WHERE id = ?", (job2_id,))
        await conn.commit()

    # Trigger reconciliation
    result = await db.reconcile_stuck_sync_jobs(max_attempts=3)
    assert result["requeued"] >= 1
    assert result["failed"] >= 1

    # Verify job 1 was reset to queued
    job1 = await db.get_sync_job_by_id(job1_id)
    assert job1.status == UploadStatus.QUEUED
    assert "Re-queued" in (job1.error or "")

    # Verify job 2 was marked failed
    job2 = await db.get_sync_job_by_id(job2_id)
    assert job2.status == UploadStatus.FAILED
    assert "Exceeded retry limit" in (job2.error or "")


@pytest.mark.asyncio
async def test_reconcile_and_resume_triggers_worker(tmp_path):
    """Verify reconcile_and_resume automatically resumes worker when queued jobs exist."""
    file_id = await db.upsert_music_file({
        "path": str(tmp_path / "resume_worker.mp3"),
        "filename": "resume_worker.mp3",
        "artist": "Resume Artist",
        "album": "Resume Album",
        "title": "Resume Song",
        "duration": 120.0,
        "format": "MP3",
        "file_size": 1000,
        "modified_time": 1000.0,
        "file_hash": "hash_res_1",
        "metadata_hash": "meta_res_1"
    })
    await db.create_sync_job(file_id)

    with patch.object(queue_manager, "ensure_worker_running") as mock_ensure:
        res = await queue_manager.reconcile_and_resume()
        mock_ensure.assert_called_once()


@pytest.mark.asyncio
async def test_read_only_metadata_update_handling(tmp_path):
    """Verify updating metadata on a read-only file updates DB and truthfully reports read-only status."""
    fake_audio = tmp_path / "readonly_track.mp3"
    fake_audio.write_bytes(b"dummy audio content")

    file_id = await db.upsert_music_file({
        "path": str(fake_audio),
        "filename": "readonly_track.mp3",
        "artist": "Original Artist",
        "album": "Original Album",
        "title": "Original Title",
        "duration": 200.0,
        "format": "MP3",
        "file_size": 1000,
        "modified_time": 1000.0,
        "file_hash": "hash_ro_1",
        "metadata_hash": "meta_ro_1"
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Mock os.access to return False (simulating :ro mount)
        with patch("ytm_service.main.os.access", return_value=False), \
             patch.object(metadata_tracker, "log_change") as mock_log:

            res = await ac.post(
                f"/api/songs/{file_id}/metadata",
                json={
                    "title": "New Title",
                    "artist": "New Artist",
                    "album": "New Album",
                    "track_number": 1
                }
            )
            assert res.status_code == 200
            data = res.json()
            assert data["title"] == "New Title"
            assert data["artist"] == "New Artist"

            # Verify DB updated
            db_file = await db.get_music_file_by_id(file_id)
            assert db_file.title == "New Title"
            assert db_file.artist == "New Artist"

            # Verify truthful tracker logging
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args.kwargs
            assert "read-only" in call_kwargs["detail"]
            assert "tags untouched" in call_kwargs["detail"]
