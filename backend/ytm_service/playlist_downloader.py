import os
import re
import json
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from .config import settings
from .database import db
from .ytm_client import ytm_client
from .musicbrainz import musicbrainz_client
from .scanner import write_metadata_tags, extract_metadata
from .downloader import _download_sync, extract_playlist_info_sync
from .normalizer import normalize_text
from .matcher import string_similarity
from .security import validate_fs_path

logger = logging.getLogger("ytm_sync.playlist_downloader")


class PlaylistSyncStatus(BaseModel):
    is_running: bool = False
    playlist_id: Optional[str] = None
    playlist_title: Optional[str] = None
    total_tracks: int = 0
    completed_tracks: int = 0
    failed_tracks: int = 0
    needs_help_tracks: int = 0
    current_track: Optional[str] = None
    errors: list[str] = []


def clean_youtube_title(title: str, channel_name: Optional[str] = None) -> tuple[str, Optional[str]]:
    """
    Clean YouTube video title artifacts (Official Audio, HD, Lyrics, etc.)
    and extract artist if formatted as 'Artist - Title'.
    """
    cleaned = title.strip()

    # Remove brackets with common noise: [Official Audio], (Official Music Video), [HD], (Lyrics), etc.
    noise_patterns = [
        r"\[(?:official\s+audio|official\s+video|official\s+music\s+video|audio|video|hd|hq|4k|lyrics|live|live\s+audio)\]",
        r"\((?:official\s+audio|official\s+video|official\s+music\s+video|audio|video|hd|hq|4k|lyrics|visualizer|live|live\s+audio)\)",
        r"\b(?:official\s+music\s+video|official\s+video|official\s+audio)\b",
    ]
    for pat in noise_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()

    # Remove trailing/leading hyphens or dashes
    cleaned = re.sub(r"\s*-\s*$", "", cleaned).strip()
    cleaned = re.sub(r"^\s*-\s*", "", cleaned).strip()

    # Check for "Artist - Title" format
    artist = channel_name.strip() if channel_name else None
    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        artist_candidate = parts[0].strip()
        title_candidate = parts[1].strip()
        if artist_candidate and title_candidate:
            artist = artist_candidate
            cleaned = title_candidate

    # Clean any double spaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if artist:
        artist = re.sub(r"\s+", " ", artist).strip()

    return cleaned, artist


def is_valid_metadata_match(
    candidate_title: Optional[str],
    candidate_artist: Optional[str],
    candidate_album: Optional[str],
    target_title: str,
    target_artist: Optional[str],
) -> bool:
    """
    Verify that an online metadata result is an authentic match for the track
    and NOT a random guess or different song by the same artist.
    """
    if not candidate_title or not candidate_title.strip():
        return False
    if not candidate_artist or not candidate_artist.strip():
        return False
    if not candidate_album or not candidate_album.strip():
        return False

    n_target_title = normalize_text(target_title)
    n_cand_title = normalize_text(candidate_title)
    if not n_target_title or not n_cand_title:
        return False

    # 1. Title match verification: reject guessing different songs
    title_sim = string_similarity(n_target_title, n_cand_title)
    title_match = (
        n_target_title == n_cand_title
        or title_sim >= 0.80
        or (title_sim >= 0.60 and (n_target_title in n_cand_title or n_cand_title in n_target_title))
    )
    if not title_match:
        return False

    # 2. Artist match verification (if target artist is provided)
    if target_artist and target_artist.strip():
        n_target_art = normalize_text(target_artist)
        n_cand_art = normalize_text(candidate_artist)
        if n_target_art and n_cand_art:
            art_sim = string_similarity(n_target_art, n_cand_art)
            art_match = (
                n_target_art == n_cand_art
                or art_sim >= 0.70
                or (n_target_art in n_cand_art or n_cand_art in n_target_art)
            )
            if not art_match:
                return False

    # 3. Album verification: must not be a generic dummy value
    cand_alb_lower = candidate_album.strip().lower()
    if cand_alb_lower in ("unknown", "unknown album", "single"):
        return False

    return True


async def download_and_upload_playlist_track(
    video_id: str,
    raw_title: str,
    raw_artist: Optional[str] = None,
    raw_album: Optional[str] = None,
    raw_thumbnail: Optional[str] = None,
    destination_dir: Optional[Path] = None,
    enrich_metadata: bool = True,
    require_full_match: bool = True
) -> dict:
    """
    Download a single playlist track via yt-dlp, enrich/clean its metadata,
    write tags & artwork, upload to YouTube Music cloud locker, and save to local library.
    If require_full_match is True and no verified match for artist, title, and album can be found:
    skip download/upload and mark as 'needs_help'.
    """
    # 1. Clean title and artist candidates
    clean_title, detected_artist = clean_youtube_title(raw_title, raw_artist)
    final_artist = raw_artist or detected_artist
    final_title = clean_title or raw_title
    final_album = raw_album
    final_cover_url = raw_thumbnail

    def is_known(val: Optional[str], forbidden=("unknown", "unknown artist", "unknown album", "single")) -> bool:
        if not val or not str(val).strip():
            return False
        return str(val).strip().lower() not in forbidden

    # 2. Metadata enrichment & search if needed
    if enrich_metadata and final_title:
        try:
            search_query = final_title
            search_artist = final_artist if is_known(final_artist) else None
            logger.info(f"Searching metadata match for '{final_title}' (Artist: '{search_artist or 'Unknown'}')...")
            matches = await musicbrainz_client.search(
                query=search_query,
                artist=search_artist,
                provider="all"
            )
            if matches:
                # Strictly filter for genuine matches where title, artist, and album match without guessing
                verified_matches = [
                    m for m in matches 
                    if is_known(m.title, forbidden=()) 
                    and is_known(m.artist, forbidden=("unknown", "unknown artist")) 
                    and is_known(m.album, forbidden=("unknown", "unknown album", "single"))
                    and is_valid_metadata_match(
                        candidate_title=m.title,
                        candidate_artist=m.artist,
                        candidate_album=m.album,
                        target_title=final_title,
                        target_artist=final_artist
                    )
                ]
                if verified_matches:
                    # Pick match with highest title similarity to avoid guesswork
                    verified_matches.sort(
                        key=lambda m: string_similarity(normalize_text(final_title), normalize_text(m.title)),
                        reverse=True
                    )
                    best = verified_matches[0]
                    final_title = best.title
                    final_artist = best.artist
                    final_album = best.album
                    if best.cover_url:
                        final_cover_url = best.cover_url
                    logger.info(f"Found confirmed metadata match: '{final_title}' by '{final_artist}' on album '{final_album}'")
                else:
                    logger.info(f"No strict metadata match found for '{final_title}' (Artist: '{final_artist or 'Unknown'}') - refusing to guess")
        except Exception as ex:
            logger.debug(f"Metadata search exception: {ex}")

    # 3. Check if we have a complete match for artist, title, and album
    has_artist_match = is_known(final_artist, forbidden=("unknown", "unknown artist"))
    has_title_match = bool(final_title and final_title.strip())
    has_album_match = is_known(final_album, forbidden=("unknown", "unknown album"))

    if require_full_match and (not has_artist_match or not has_title_match or not has_album_match):
        missing_parts = []
        if not has_artist_match:
            missing_parts.append("artist")
        if not has_title_match:
            missing_parts.append("title")
        if not has_album_match:
            missing_parts.append("album")
        reason = f"No match found for: {', '.join(missing_parts)}"
        logger.info(f"Skipping download/upload for track {video_id} ('{final_title}') - Needs Help ({reason})")

        # Save to database needs_help_tracks
        await db.upsert_needs_help_track(
            video_id=video_id,
            title=final_title,
            artist=final_artist,
            album=final_album,
            thumbnail=final_cover_url,
            source="Playlist Sync",
            reason=reason
        )

        return {
            "status": "needs_help",
            "video_id": video_id,
            "title": final_title,
            "artist": final_artist or "Unknown Artist",
            "album": final_album or "Unknown Album",
            "thumbnail": final_cover_url,
            "reason": reason
        }

    # If we have all 3, ensure final_album and final_artist have valid fallbacks
    final_artist = final_artist or "Unknown Artist"
    final_album = final_album or "Single"

    # 4. Proceed to download audio file via yt-dlp
    staging_dir = settings.data_dir / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    temp_base = staging_dir / f"pl_{video_id}"

    search_query = f"{final_artist} {final_title}".strip()
    logger.info(f"Downloading track {video_id} ('{final_title}' by '{final_artist}') via yt-dlp...")
    downloaded_file = await asyncio.to_thread(_download_sync, video_id, temp_base, search_query)
    if not downloaded_file or not downloaded_file.exists() or downloaded_file.stat().st_size == 0:
        raise RuntimeError(f"Failed to download audio for video {video_id}")

    try:
        # Write metadata tags & embed cover art
        logger.info(f"Tagging audio: Title='{final_title}', Artist='{final_artist}', Album='{final_album}'")
        await asyncio.to_thread(
            write_metadata_tags,
            downloaded_file,
            title=final_title,
            artist=final_artist,
            album=final_album,
            cover_url=final_cover_url
        )

        # Upload newly tagged song to YouTube Music cloud locker
        logger.info(f"Uploading tagged track '{final_title}' to YouTube Music locker...")
        up_res = await ytm_client.upload_file(str(downloaded_file))
        if not up_res.get("success"):
            raise RuntimeError(f"YouTube Music upload failed: {up_res.get('response')}")

        # Save a local copy if directory exists and is writable within approved roots
        local_saved_path: Optional[str] = None
        if destination_dir:
            target_music_dir = validate_fs_path(destination_dir, allow_create_in_parent=True)
        else:
            allowed_roots = settings.allowed_fs_roots
            default_root = Path("/music")
            target_music_dir = default_root if any(default_root == r or default_root.is_relative_to(r) for r in allowed_roots) else allowed_roots[0]

        if target_music_dir.exists() and os.access(str(target_music_dir), os.W_OK):
            try:
                safe_artist = re.sub(r'[\\/*?:"<>|]', "", final_artist).strip() or "Unknown Artist"
                safe_album = re.sub(r'[\\/*?:"<>|]', "", final_album).strip() or "Unknown Album"
                safe_title = re.sub(r'[\\/*?:"<>|]', "", final_title).strip() or "Track"

                album_folder = target_music_dir / safe_artist / safe_album
                album_folder.mkdir(parents=True, exist_ok=True)
                local_file = album_folder / f"{safe_title}.mp3"

                shutil.copy2(downloaded_file, local_file)
                local_saved_path = str(local_file)

                # Register in SQLite database music_files
                meta = await asyncio.to_thread(extract_metadata, local_file)
                await db.upsert_music_file(meta)
                logger.info(f"Saved local copy to {local_saved_path}")
            except Exception as e:
                logger.warning(f"Could not save local copy to {target_music_dir}: {e}")

        # Upsert into ytm_uploads so it appears immediately in locker
        await db.upsert_ytm_upload({
            "entity_id": f"up_{video_id}",
            "video_id": video_id,
            "title": final_title,
            "artist": final_artist,
            "album": final_album,
            "thumbnail": final_cover_url
        })

        # Remove from needs_help_tracks if previously marked
        await db.delete_needs_help_track(video_id)

        return {
            "status": "success",
            "video_id": video_id,
            "title": final_title,
            "artist": final_artist,
            "album": final_album,
            "cover_url": final_cover_url,
            "local_path": local_saved_path
        }

    finally:
        if downloaded_file and downloaded_file.exists():
            try:
                downloaded_file.unlink()
            except Exception:
                pass


class PlaylistSyncManager:
    """Manages background batch sync of missing playlist tracks."""

    def __init__(self):
        from collections import deque
        self._status = PlaylistSyncStatus()
        self._task: Optional[asyncio.Task] = None
        self._queue: list[dict] = []
        self._current_index: int = -1
        self._current_track_dict: Optional[dict] = None
        self._history: deque = deque(maxlen=100)

    @property
    def status(self) -> PlaylistSyncStatus:
        return self._status

    def start_sync(
        self,
        playlist_id: str,
        playlist_title: str,
        tracks_to_sync: list[dict],
        destination_dir: Optional[str] = None
    ) -> PlaylistSyncStatus:
        """Start a background sync for missing playlist tracks."""
        if self._status.is_running:
            raise RuntimeError("A playlist sync is already in progress.")

        dest_path = validate_fs_path(destination_dir, allow_create_in_parent=True) if destination_dir else None
        self._status = PlaylistSyncStatus(
            is_running=True,
            playlist_id=playlist_id,
            playlist_title=playlist_title,
            total_tracks=len(tracks_to_sync),
            completed_tracks=0,
            failed_tracks=0,
            errors=[]
        )
        self._queue = list(tracks_to_sync)
        self._current_index = -1
        self._current_track_dict = None
        self._task = asyncio.create_task(self._sync_worker(dest_path))
        return self._status

    async def _sync_worker(self, destination_dir: Optional[Path]):
        from datetime import datetime
        logger.info(f"Starting playlist sync for {len(self._queue)} tracks...")
        seen_sync_keys = set()
        try:
            for idx, track in enumerate(self._queue):
                self._current_index = idx
                self._current_track_dict = track

                video_id = track.get("video_id")
                if not video_id:
                    self._status.failed_tracks += 1
                    continue

                title = track.get("title", "Untitled")
                artist = track.get("artist")
                album = track.get("album")
                thumb = track.get("thumbnail")

                from .normalizer import normalize_text
                sync_key = f"{normalize_text(artist)}|{normalize_text(title)}"
                if sync_key in seen_sync_keys:
                    logger.info(f"Skipping duplicate track in playlist queue: '{title}' by '{artist}'")
                    self._status.completed_tracks += 1
                    continue
                seen_sync_keys.add(sync_key)

                # Check if already present in database ytm_uploads
                existing = await db.find_ytm_upload_by_title_artist(title, artist)
                if not existing:
                    existing = await db.get_ytm_upload_by_video_id(video_id)
                if existing:
                    logger.info(f"Skipping track '{title}' by '{artist}' - already in cloud uploads ({existing.title}).")
                    self._status.completed_tracks += 1
                    continue

                self._status.current_track = f"{artist} - {title}" if artist else title
                logger.info(f"Syncing ({self._status.completed_tracks + 1}/{self._status.total_tracks}): {self._status.current_track}")

                try:
                    res = await download_and_upload_playlist_track(
                        video_id=video_id,
                        raw_title=title,
                        raw_artist=artist,
                        raw_album=album,
                        raw_thumbnail=thumb,
                        destination_dir=destination_dir,
                        enrich_metadata=True,
                        require_full_match=True
                    )
                    if res.get("status") == "needs_help":
                        self._status.needs_help_tracks += 1
                        self._history.appendleft({
                            "id": f"pl_help_{video_id}",
                            "category": "download",
                            "title": res.get("title") or title,
                            "artist": res.get("artist") or artist,
                            "album": res.get("album") or album,
                            "thumbnail": thumb,
                            "status": "needs_help",
                            "current_step": f"Needs Help: {res.get('reason')}",
                            "source": f"Playlist: {self._status.playlist_title}",
                            "created_at": datetime.now().isoformat(),
                            "error": res.get("reason")
                        })
                    else:
                        self._status.completed_tracks += 1
                        self._history.appendleft({
                            "id": f"pl_done_{video_id}",
                            "category": "download",
                            "title": res.get("title") or title,
                            "artist": res.get("artist") or artist,
                            "album": res.get("album") or album,
                            "thumbnail": thumb,
                            "status": "completed",
                            "current_step": "Downloaded & uploaded to cloud locker",
                            "source": f"Playlist: {self._status.playlist_title}",
                            "created_at": datetime.now().isoformat(),
                            "error": None
                        })
                except Exception as ex:
                    logger.error(f"Failed to sync track {video_id} ('{title}'): {ex}")
                    self._status.failed_tracks += 1
                    self._status.errors.append(f"{title}: {str(ex)[:150]}")
                    self._history.appendleft({
                        "id": f"pl_fail_{video_id}",
                        "category": "download",
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "thumbnail": thumb,
                        "status": "failed",
                        "current_step": f"Failed: {str(ex)[:120]}",
                        "source": f"Playlist: {self._status.playlist_title}",
                        "created_at": datetime.now().isoformat(),
                        "error": str(ex)
                    })

                # Rate limiting between uploads to avoid YouTube rate limits
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info("Playlist sync task was cancelled.")
        finally:
            self._status.is_running = False
            self._status.current_track = None
            self._current_track_dict = None
            self._current_index = -1
            logger.info(
                f"Playlist sync finished: {self._status.completed_tracks} completed, "
                f"{self._status.failed_tracks} failed."
            )

    def get_queue_items(self) -> list[dict]:
        """Return structured queue items for the unified queue view."""
        items = []

        # 1. Currently active item
        if self._status.is_running and self._current_track_dict:
            t = self._current_track_dict
            items.append({
                "id": f"pl_active_{t.get('video_id')}",
                "category": "download",
                "title": t.get("title", "Untitled"),
                "artist": t.get("artist"),
                "album": t.get("album"),
                "thumbnail": t.get("thumbnail"),
                "status": "in_progress",
                "current_step": f"Downloading & Uploading ({self._status.completed_tracks + 1}/{self._status.total_tracks})",
                "source": f"Playlist: {self._status.playlist_title}",
                "created_at": None,
                "error": None
            })

        # 2. Remaining items in current queue
        if self._status.is_running and self._current_index >= 0:
            remaining = self._queue[self._current_index + 1:]
            for r in remaining:
                items.append({
                    "id": f"pl_queued_{r.get('video_id')}",
                    "category": "download",
                    "title": r.get("title", "Untitled"),
                    "artist": r.get("artist"),
                    "album": r.get("album"),
                    "thumbnail": r.get("thumbnail"),
                    "status": "queued",
                    "current_step": "Waiting in download/upload queue",
                    "source": f"Playlist: {self._status.playlist_title}",
                    "created_at": None,
                    "error": None
                })

        # 3. Recent session history
        items.extend(list(self._history))
        return items

    def cancel_sync(self):
        """Cancel ongoing sync process."""
        if self._task and not self._task.done():
            self._task.cancel()
        self._status.is_running = False
        self._status.current_track = None
        self._current_track_dict = None
        self._current_index = -1

    def clear_history(self):
        self._history.clear()


playlist_sync_manager = PlaylistSyncManager()
