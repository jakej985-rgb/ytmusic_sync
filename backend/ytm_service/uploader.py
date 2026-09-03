import asyncio
import logging
from pathlib import Path
from typing import Optional
from .config import settings
from .database import db
from .models import UploadStatus
from .ytm_client import ytm_client
from .matcher import matcher
from .security import validate_fs_path

logger = logging.getLogger("ytm_sync.uploader")

class UploadQueueManager:
    def __init__(self):
        self._is_running = False
        self._worker_task: Optional[asyncio.Task] = None
        self.current_job_id: Optional[int] = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def enqueue_song(self, music_file_id: int) -> int:
        """Enqueue a single song for upload."""
        job_id = await db.create_sync_job(music_file_id)
        self.ensure_worker_running()
        return job_id

    async def enqueue_all_missing(self) -> int:
        """Enqueue all missing songs into the sync queue."""
        missing_files = await db.get_music_files(filter_status="missing", limit=100000)
        enqueued_count = 0
        for mf in missing_files:
            if mf.id is not None:
                await db.create_sync_job(mf.id)
                enqueued_count += 1
        
        if enqueued_count > 0:
            self.ensure_worker_running()
        return enqueued_count

    def ensure_worker_running(self):
        if not self._is_running or self._worker_task is None or self._worker_task.done():
            self._is_running = True
            self._worker_task = asyncio.create_task(self._process_queue_loop())

    async def stop_worker(self):
        self._is_running = False
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

    async def reconcile_and_resume(self) -> dict[str, int]:
        """Reconcile stuck sync jobs on startup and resume the worker if queued jobs exist."""
        result = await db.reconcile_stuck_sync_jobs()
        next_job = await db.get_next_queued_job()
        if next_job:
            logger.info(f"Startup reconciliation: {result['requeued']} jobs requeued, resuming upload worker.")
            self.ensure_worker_running()
        elif result['requeued'] > 0 or result['failed'] > 0:
            logger.info(f"Startup reconciliation completed: {result['requeued']} requeued, {result['failed']} failed.")
        return result

    async def _process_queue_loop(self):
        logger.info("Upload worker loop started.")
        self._is_running = True
        while self._is_running:
            job = await db.get_next_queued_job()
            if not job:
                logger.info("No more queued upload jobs. Worker sleeping.")
                self._is_running = False
                break

            self.current_job_id = job.id
            await self._process_single_job(job)
            self.current_job_id = None
            # Small delay between uploads to respect rate limits
            await asyncio.sleep(1.0)

    async def _process_single_job(self, job):
        job_id = job.id
        music_file = job.music_file
        
        if not music_file:
            await db.update_sync_job(job_id, UploadStatus.FAILED, error="Music file reference not found")
            return

        # Pre-upload duplicate check: if already matched, skip upload
        if music_file.id is not None and await db.is_file_matched(music_file.id):
            logger.info(f"Skipping upload for {music_file.filename}: Already matched in YouTube Music uploads.")
            await db.update_sync_job(job_id, UploadStatus.VERIFIED)
            return

        logger.info(f"Processing upload job {job_id} for: {music_file.filename}")
        await db.update_sync_job(job_id, UploadStatus.UPLOADING, increment_attempts=True)

        from .scanner import write_metadata_tags, extract_metadata
        import shutil

        try:
            upload_path = validate_fs_path(music_file.path, must_exist=True)
        except ValueError as e:
            logger.error(f"Upload path validation failed for job {job_id}: {e}")
            await db.update_sync_job(job_id, UploadStatus.FAILED, error=f"Invalid upload path: {e}")
            return

        temp_staged_path = None

        try:
            if upload_path.exists():
                current_meta = await asyncio.to_thread(extract_metadata, upload_path)
                tags_differ = (
                    current_meta.get("title") != music_file.title or
                    current_meta.get("artist") != music_file.artist or
                    current_meta.get("album") != music_file.album
                )
                if tags_differ:
                    staging_dir = Path("/tmp/ytm_staging")
                    staging_dir.mkdir(parents=True, exist_ok=True)
                    temp_staged_path = staging_dir / f"stage_{job_id}_{music_file.filename}"
                    shutil.copy2(upload_path, temp_staged_path)
                    await asyncio.to_thread(
                        write_metadata_tags,
                        temp_staged_path,
                        title=music_file.title,
                        artist=music_file.artist,
                        album=music_file.album,
                        track_number=music_file.track_number
                    )
                    upload_path = temp_staged_path
        except Exception as e:
            logger.warning(f"Could not stage custom metadata tags for {music_file.filename}: {e}")

        # Retry loop with exponential backoff
        max_retries = settings.max_retries
        attempt = job.attempts + 1
        uploaded_successfully = False
        last_error = None

        try:
            while attempt <= max_retries:
                try:
                    res = await ytm_client.upload_file(str(upload_path))
                    if res.get("success"):
                        uploaded_successfully = True
                        break
                    else:
                        last_error = f"YTM rejected upload: {res.get('response')}"
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Upload attempt {attempt}/{max_retries} failed for {music_file.filename}: {e}")

                attempt += 1
                if attempt <= max_retries:
                    backoff_secs = 2 ** attempt
                    logger.info(f"Retrying in {backoff_secs}s...")
                    await asyncio.sleep(backoff_secs)
        finally:
            if temp_staged_path and temp_staged_path.exists():
                try:
                    temp_staged_path.unlink()
                except Exception:
                    pass

        if not uploaded_successfully:
            logger.error(f"All upload attempts failed for job {job_id}: {last_error}")
            await db.update_sync_job(job_id, UploadStatus.FAILED, error=last_error)
            return

        # Uploaded successfully -> Update status
        await db.update_sync_job(job_id, UploadStatus.UPLOADED)

        # Verification step
        if settings.verify_uploads:
            await db.update_sync_job(job_id, UploadStatus.VERIFYING)
            try:
                # Wait 5 seconds for YTM backend indexing
                await asyncio.sleep(5.0)
                # Fetch remote uploads and re-match
                await ytm_client.fetch_and_cache_uploads()
                await matcher.match_all()
                await db.update_sync_job(job_id, UploadStatus.VERIFIED)
                logger.info(f"Successfully verified upload for {music_file.filename}")
            except Exception as e:
                logger.warning(f"Verification delayed for job {job_id}: {e}")
                # Still count as uploaded even if immediate verification query was delayed
                await db.update_sync_job(job_id, UploadStatus.UPLOADED, error=f"Verification pending: {e}")
        else:
            await db.update_sync_job(job_id, UploadStatus.VERIFIED)

        # Trigger event-based reconciliation for any watched replicated playlists (Section 14 of plan)
        try:
            from .playlist_watcher import playlist_watcher
            asyncio.create_task(playlist_watcher.on_new_upload_completed(str(job.source_id or job.music_file_id)))
        except Exception as ex:
            logger.debug(f"Could not trigger playlist watcher on upload completion: {ex}")

queue_manager = UploadQueueManager()
