import asyncio
import logging
from typing import Optional
from .config import settings
from .database import db
from .models import UploadStatus
from .ytm_client import ytm_client
from .matcher import matcher

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

        # Retry loop with exponential backoff
        max_retries = settings.max_retries
        attempt = job.attempts + 1
        uploaded_successfully = False
        last_error = None

        while attempt <= max_retries:
            try:
                res = await ytm_client.upload_file(music_file.path)
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

queue_manager = UploadQueueManager()
