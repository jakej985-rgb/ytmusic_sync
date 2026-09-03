"""
Playlist Watcher Service:
Periodically monitors enabled replicated playlists and triggers
immediate reconciliation upon detecting source playlist changes
or upon completion of new locker uploads.
"""

import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone
from .database import db
from .playlist_replicator import playlist_replicator

logger = logging.getLogger("ytm_sync.playlist_watcher")


class PlaylistWatcher:
    def __init__(self):
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("PlaylistWatcher background loop started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("PlaylistWatcher background loop stopped")

    async def _watch_loop(self):
        while self._running:
            try:
                # Check every 60 seconds
                await asyncio.sleep(60)
                await self.check_all_replicas()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in PlaylistWatcher loop: {e}", exc_info=True)

    async def check_all_replicas(self):
        """Check and reconcile all enabled replicated playlists whose interval has elapsed."""
        try:
            replicas = await db.get_replicated_playlists(enabled_only=True)
            for r in replicas:
                should_sync = False
                if not r.last_sync_at:
                    should_sync = True
                else:
                    try:
                        last_dt = datetime.fromisoformat(r.last_sync_at)
                        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                        if elapsed >= r.sync_interval_seconds:
                            should_sync = True
                    except Exception:
                        should_sync = True

                if should_sync:
                    logger.info(f"Watcher triggering scheduled reconciliation for replica #{r.id} ('{r.source_playlist_name}')")
                    try:
                        await playlist_replicator.reconcile_playlist(r.id, dry_run=False)
                    except Exception as ex:
                        logger.error(f"Scheduled reconciliation failed for replica #{r.id}: {ex}")
                        await db.update_replicated_playlist(r.id, last_sync_status=f"FAILED: {str(ex)[:100]}")
        except Exception as e:
            logger.error(f"Error checking replicas in PlaylistWatcher: {e}")

    async def on_new_upload_completed(self, video_id: str):
        """
        Event Trigger (Section 14 & 15 of plan):
        Called immediately after a song is uploaded to YouTube Music locker.
        Tracks dependencies using source playlist snapshots: only reconciles
        replicas that actually contain the newly uploaded track.
        """
        logger.info(f"PlaylistWatcher received new upload completion for video_id={video_id}")
        try:
            replicas = await db.get_replicated_playlists(enabled_only=True)
            upload_rec = await db.get_ytm_upload_by_video_id(video_id)
            from .normalizer import normalize_text

            for r in replicas:
                is_affected = False
                snapshot = await db.get_latest_replicated_playlist_snapshot(r.id)
                if not snapshot:
                    # No snapshot yet, reconcile to establish baseline
                    is_affected = True
                else:
                    tracks = snapshot.get("tracks", [])
                    for t in tracks:
                        if t.get("video_id") == video_id:
                            is_affected = True
                            break
                        if upload_rec and upload_rec.title and t.get("title"):
                            if normalize_text(upload_rec.title) == normalize_text(t.get("title")):
                                is_affected = True
                                break

                if is_affected:
                    logger.info(f"Replica #{r.id} ('{r.source_playlist_name}') is affected by upload {video_id}. Reconciling...")
                    try:
                        await playlist_replicator.reconcile_playlist(r.id, dry_run=False)
                    except Exception as ex:
                        logger.error(f"Event-triggered reconciliation failed for replica #{r.id}: {ex}")
                else:
                    logger.debug(f"Replica #{r.id} ('{r.source_playlist_name}') is not affected by upload {video_id}. Skipping.")
        except Exception as e:
            logger.error(f"Error in on_new_upload_completed: {e}")


playlist_watcher = PlaylistWatcher()
