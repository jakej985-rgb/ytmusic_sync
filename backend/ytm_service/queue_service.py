import logging
from typing import Optional, List, Dict, Any
from .database import db
from .models import UploadStatus
from .uploader import queue_manager as local_upload_queue
from .playlist_downloader import playlist_sync_manager
from .metadata_tracker import metadata_tracker

logger = logging.getLogger("ytm_sync.queue_service")


class UnifiedQueueService:
    """Aggregates all background tasks (downloads, uploads, local uploads, metadata changes) into a unified queue."""

    async def get_queue(
        self,
        category: str = "all",
        status: str = "all",
        limit: int = 200
    ) -> dict:
        # 1. Fetch playlist sync queue items (Downloads & Uploads)
        pl_items = playlist_sync_manager.get_queue_items()

        # 2. Fetch local upload jobs (Local Uploads & Uploads)
        local_sync_jobs = await db.get_active_or_queued_sync_jobs(limit=100)
        recent_sync_jobs = await db.get_sync_history(limit=50)

        local_items: List[Dict[str, Any]] = []
        seen_job_ids = set()

        for job in local_sync_jobs:
            mf = job.music_file
            is_active = job.status == UploadStatus.UPLOADING
            st = "in_progress" if is_active else "queued"
            step = "Uploading to YouTube Music cloud locker..." if is_active else "Queued for upload"
            local_items.append({
                "id": f"local_job_{job.id}",
                "category": "local_upload",
                "title": mf.title if mf else "Unknown Title",
                "artist": mf.artist if mf else None,
                "album": mf.album if mf else None,
                "thumbnail": None,
                "status": st,
                "current_step": step,
                "source": "Local Music Library",
                "created_at": job.started_at,
                "error": job.error
            })
            seen_job_ids.add(job.id)

        for job in recent_sync_jobs:
            if job.id in seen_job_ids:
                continue
            if job.status in (UploadStatus.COMPLETED, UploadStatus.VERIFIED, UploadStatus.FAILED):
                mf = job.music_file
                st = "completed" if job.status in (UploadStatus.COMPLETED, UploadStatus.VERIFIED) else "failed"
                step = "Uploaded to YouTube Music locker" if st == "completed" else f"Upload failed: {job.error or 'Unknown error'}"
                local_items.append({
                    "id": f"local_job_{job.id}",
                    "category": "local_upload",
                    "title": mf.title if mf else "Unknown Title",
                    "artist": mf.artist if mf else None,
                    "album": mf.album if mf else None,
                    "thumbnail": None,
                    "status": st,
                    "current_step": step,
                    "source": "Local Music Library",
                    "created_at": job.completed_at or job.started_at,
                    "error": job.error
                })
                seen_job_ids.add(job.id)

        # 3. Fetch metadata changes
        meta_items = metadata_tracker.get_recent(limit=100)

        # 4. Fetch tracks needing help (no match found during download)
        db_help_tracks = await db.get_needs_help_tracks(limit=100)
        help_items: List[Dict[str, Any]] = []
        seen_help_vids = set()
        for h in db_help_tracks:
            vid = h["video_id"]
            help_items.append({
                "id": f"help_{vid}",
                "video_id": vid,
                "category": "needs_help",
                "title": h["title"],
                "artist": h.get("artist"),
                "album": h.get("album"),
                "thumbnail": h.get("thumbnail"),
                "status": "needs_help",
                "current_step": f"Needs Help: {h.get('reason') or 'Missing verified metadata match'}",
                "source": h.get("source") or "Playlist Sync",
                "created_at": h.get("created_at"),
                "error": h.get("reason")
            })
            seen_help_vids.add(vid)

        # Summary count computation
        active_downloads = len([x for x in pl_items if x.get("status") in ("in_progress", "queued")])
        active_local_uploads = len([x for x in local_items if x.get("status") in ("in_progress", "queued")])
        active_uploads = active_downloads + active_local_uploads
        total_meta = len(meta_items)
        total_help = len(help_items)
        active_total = active_downloads + active_local_uploads

        summary = {
            "all": active_total + total_meta + total_help,
            "needs_help": total_help,
            "metadata_change": total_meta,
            "download": active_downloads,
            "upload": active_uploads,
            "local_upload": active_local_uploads,
            "active": active_total
        }

        # Combine all items with prioritized ordering:
        # 1. in_progress items first
        # 2. needs_help items second (requiring user attention!)
        # 3. queued items third
        # 4. metadata changes
        # 5. completed / failed items
        in_progress_items = [x for x in pl_items + local_items if x.get("status") == "in_progress"]
        queued_items = [x for x in pl_items + local_items if x.get("status") == "queued"]
        finished_items = [x for x in pl_items + local_items if x.get("status") in ("completed", "failed") and x.get("video_id") not in seen_help_vids]

        combined: List[Dict[str, Any]] = []
        combined.extend(in_progress_items)
        combined.extend(help_items)
        combined.extend(queued_items)
        combined.extend(meta_items)
        combined.extend(finished_items)

        # Filter by category
        filtered: List[Dict[str, Any]] = []
        for item in combined:
            cat = item.get("category", "")
            src = item.get("source", "")
            st = item.get("status", "")
            if category == "all":
                filtered.append(item)
            elif category == "needs_help":
                if cat == "needs_help" or st == "needs_help":
                    filtered.append(item)
            elif category == "download":
                if cat in ("download", "needs_help") or src.startswith("Playlist:"):
                    filtered.append(item)
            elif category == "upload":
                if cat in ("upload", "local_upload") or src.startswith("Playlist:"):
                    filtered.append(item)
            elif category == "local_upload":
                if cat == "local_upload" or src == "Local Music Library":
                    filtered.append(item)
            elif category == "metadata_change":
                if cat == "metadata_change":
                    filtered.append(item)

        # Filter by status
        if status == "active":
            filtered = [x for x in filtered if x.get("status") in ("in_progress", "queued", "needs_help")]
        elif status in ("completed", "failed", "needs_help"):
            filtered = [x for x in filtered if x.get("status") == status]

        is_active = playlist_sync_manager.status.is_running or local_upload_queue.is_running
        active_desc = ""
        if playlist_sync_manager.status.is_running:
            active_desc = f"Playlist Sync: {playlist_sync_manager.status.completed_tracks}/{playlist_sync_manager.status.total_tracks} tracks ({playlist_sync_manager.status.playlist_title or 'Playlist'})"
        elif local_upload_queue.is_running:
            active_desc = "Uploading local library songs to YouTube Music locker..."

        return {
            "summary": summary,
            "is_active": is_active,
            "active_description": active_desc,
            "items": filtered[:limit]
        }

    async def cancel_all(self):
        """Cancel active playlist sync and clear queued local jobs."""
        playlist_sync_manager.cancel_sync()
        await db.clear_queued_sync_jobs()

    def clear_completed(self):
        """Clear finished task history from memory."""
        playlist_sync_manager.clear_history()
        metadata_tracker.clear()


unified_queue_service = UnifiedQueueService()
