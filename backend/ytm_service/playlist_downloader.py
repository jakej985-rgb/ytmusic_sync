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

logger = logging.getLogger("ytm_sync.playlist_downloader")


class PlaylistSyncStatus(BaseModel):
    is_running: bool = False
    playlist_id: Optional[str] = None
    playlist_title: Optional[str] = None
    total_tracks: int = 0
    completed_tracks: int = 0
    failed_tracks: int = 0
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
        r"\[(?:official\s+audio|official\s+video|official\s+music\s+video|audio|video|hd|hq|4k|lyrics)\]",
        r"\((?:official\s+audio|official\s+video|official\s+music\s+video|audio|video|hd|hq|4k|lyrics|visualizer)\)",
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


async def download_and_upload_playlist_track(
    video_id: str,
    raw_title: str,
    raw_artist: Optional[str] = None,
    raw_album: Optional[str] = None,
    raw_thumbnail: Optional[str] = None,
    destination_dir: Optional[Path] = None,
    enrich_metadata: bool = True
) -> dict:
    """
    Download a single playlist track via yt-dlp, enrich/clean its metadata,
    write tags & artwork, upload to YouTube Music cloud locker, and save to local library.
    """
    staging_dir = settings.data_dir / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    temp_base = staging_dir / f"pl_{video_id}"

    # 1. Download audio file
    search_query = f"{raw_artist or ''} {raw_title}".strip()
    logger.info(f"Downloading track {video_id} ('{raw_title}') via yt-dlp...")
    downloaded_file = await asyncio.to_thread(_download_sync, video_id, temp_base, search_query)
    if not downloaded_file or not downloaded_file.exists() or downloaded_file.stat().st_size == 0:
        raise RuntimeError(f"Failed to download audio for video {video_id}")

    try:
        # 2. Clean title and artist
        clean_title, detected_artist = clean_youtube_title(raw_title, raw_artist)
        final_artist = raw_artist or detected_artist or "Unknown Artist"
        final_title = clean_title or raw_title
        final_album = raw_album or "Single"
        final_cover_url = raw_thumbnail

        # 3. Optional metadata enrichment (query MusicBrainz / Deezer / YTM)
        if enrich_metadata and final_title and final_artist != "Unknown Artist":
            try:
                logger.info(f"Enriching metadata for '{final_title}' by '{final_artist}'...")
                matches = await musicbrainz_client.search(
                    query=final_title,
                    artist=final_artist,
                    provider="all"
                )
                if matches and len(matches) > 0:
                    best = matches[0]
                    final_title = best.title or final_title
                    final_artist = best.artist or final_artist
                    if best.album:
                        final_album = best.album
                    if best.cover_url:
                        final_cover_url = best.cover_url
                    logger.info(f"Matched with '{final_title}' by '{final_artist}' (Album: '{final_album}')")
            except Exception as ex:
                logger.debug(f"Metadata enrichment skipped: {ex}")

        # 4. Write metadata tags & embed cover art
        logger.info(f"Tagging audio: Title='{final_title}', Artist='{final_artist}', Album='{final_album}'")
        await asyncio.to_thread(
            write_metadata_tags,
            downloaded_file,
            title=final_title,
            artist=final_artist,
            album=final_album,
            cover_url=final_cover_url
        )

        # 5. Upload newly tagged song to YouTube Music cloud locker
        logger.info(f"Uploading tagged track '{final_title}' to YouTube Music locker...")
        up_res = await ytm_client.upload_file(str(downloaded_file))
        if not up_res.get("success"):
            raise RuntimeError(f"YouTube Music upload failed: {up_res.get('response')}")

        # 6. Save a local copy to /music if directory exists and is writable
        local_saved_path: Optional[str] = None
        target_music_dir = destination_dir or Path("/music")
        if target_music_dir.exists() and os.access(str(target_music_dir), os.W_OK):
            try:
                # Organize by /music/<Artist>/<Album>/<Title>.mp3
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

        # 7. Upsert into ytm_uploads so it appears immediately in locker
        await db.upsert_ytm_upload({
            "entity_id": f"up_{video_id}",
            "video_id": video_id,
            "title": final_title,
            "artist": final_artist,
            "album": final_album,
            "thumbnail": final_cover_url
        })

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
        self._status = PlaylistSyncStatus()
        self._task: Optional[asyncio.Task] = None
        self._queue: list[dict] = []

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

        dest_path = Path(destination_dir) if destination_dir else None
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
        self._task = asyncio.create_task(self._sync_worker(dest_path))
        return self._status

    async def _sync_worker(self, destination_dir: Optional[Path]):
        logger.info(f"Starting playlist sync for {len(self._queue)} tracks...")
        seen_sync_keys = set()
        try:
            for track in self._queue:
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
                    await download_and_upload_playlist_track(
                        video_id=video_id,
                        raw_title=title,
                        raw_artist=artist,
                        raw_album=album,
                        raw_thumbnail=thumb,
                        destination_dir=destination_dir,
                        enrich_metadata=True
                    )
                    self._status.completed_tracks += 1
                except Exception as ex:
                    logger.error(f"Failed to sync track {video_id} ('{title}'): {ex}")
                    self._status.failed_tracks += 1
                    self._status.errors.append(f"{title}: {str(ex)[:150]}")

                # Rate limiting between uploads to avoid YouTube rate limits
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info("Playlist sync task was cancelled.")
        finally:
            self._status.is_running = False
            self._status.current_track = None
            logger.info(
                f"Playlist sync finished: {self._status.completed_tracks} completed, "
                f"{self._status.failed_tracks} failed."
            )


playlist_sync_manager = PlaylistSyncManager()
