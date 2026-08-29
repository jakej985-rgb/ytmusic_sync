import asyncio
import logging
import os
import shutil
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .config import settings
from .database import db
from .models import (
    MusicFile, YtmUpload, SyncJob, DashboardStats,
    ScanRequest, AuthSetupRequest, ConnectionStatus, MusicBrainzMatch,
    PlaylistTrackDownloadRequest, PlaylistImportRequest
)
from .scanner import scanner
from .ytm_client import ytm_client
from .matcher import matcher
from .uploader import queue_manager
from .musicbrainz import musicbrainz_client
from .downloader import download_ytm_upload, extract_playlist_info
from .scanner import write_metadata_tags
from .playlist_downloader import playlist_sync_manager, download_and_upload_playlist_track
from logging.handlers import RotatingFileHandler
from fastapi.staticfiles import StaticFiles

log_file = settings.log_file
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    ]
)
logger = logging.getLogger("ytm_sync")

background_scanner_task: Optional[asyncio.Task] = None

async def _periodic_scan_loop():
    logger.info("Periodic background scanner initialized.")
    while True:
        try:
            interval_mins = await db.get_setting("scan_interval_minutes", default=settings.scan_interval_minutes)
            await asyncio.sleep(interval_mins * 60)
            
            folders = await db.get_setting("music_folders", default=[])
            if folders and not scanner.is_scanning:
                logger.info(f"Triggering scheduled periodic scan of {len(folders)} folders...")
                await scanner.scan_folders(folders)
                await matcher.match_all()

                auto_upload = await db.get_setting("auto_upload", default=False)
                if auto_upload and ytm_client.is_auth_configured():
                    logger.info("Auto-upload enabled: Enqueueing missing tracks...")
                    await queue_manager.enqueue_all_missing()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic background scan loop: {e}")
            await asyncio.sleep(30)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global background_scanner_task
    # Startup: Initialize DB
    logger.info(f"Initializing database at {settings.db_path}...")
    await db.init_db()
    # Start periodic background scanner
    background_scanner_task = asyncio.create_task(_periodic_scan_loop())
    yield
    # Shutdown: Clean up background workers
    logger.info("Stopping background upload worker and periodic scanner...")
    if background_scanner_task:
        background_scanner_task.cancel()
    await queue_manager.stop_worker()

app = FastAPI(title="YTM Sync Backend Service", version="0.0.1-beta", lifespan=lifespan)

# Allow Flutter Desktop / Localhost clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/status", response_model=DashboardStats)
async def get_dashboard_status():
    counts = await db.get_dashboard_counts()
    conn = await ytm_client.test_connection()
    return DashboardStats(
        ytm_connected=conn["connected"],
        account_name=conn["user_name"],
        local_songs_count=counts["local_songs_count"],
        ytm_uploads_count=counts["ytm_uploads_count"],
        missing_count=counts["missing_count"],
        uploaded_count=counts["uploaded_count"],
        failed_count=counts["failed_count"],
        in_queue_count=counts["in_queue_count"],
        is_scanning=scanner.is_scanning,
        is_uploading=queue_manager.is_running,
    )

@app.get("/api/auth/status", response_model=ConnectionStatus)
async def get_auth_status():
    res = await ytm_client.test_connection()
    return ConnectionStatus(
        connected=res["connected"],
        message=res["message"],
        user_name=res.get("user_name")
    )

@app.post("/api/auth/setup", response_model=ConnectionStatus)
async def setup_auth(req: AuthSetupRequest):
    if not req.raw_headers.strip():
        raise HTTPException(status_code=400, detail="Headers cannot be empty")
    try:
        res = await ytm_client.setup_auth(req.raw_headers)
        return ConnectionStatus(
            connected=res["connected"],
            message=res["message"],
            user_name=res.get("user_name")
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to setup authentication: {e}")

@app.post("/api/auth/test", response_model=ConnectionStatus)
async def test_auth():
    res = await ytm_client.test_connection()
    return ConnectionStatus(
        connected=res["connected"],
        message=res["message"],
        user_name=res.get("user_name")
    )

@app.get("/api/ytm/playlists")
async def get_ytm_playlists():
    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        playlists = await ytm_client.get_playlists()
        return playlists
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlists: {e}")

@app.get("/api/ytm/playlists/sync-status")
async def get_playlist_sync_status():
    """Get active playlist download/sync progress."""
    return playlist_sync_manager.status

@app.post("/api/ytm/playlists/download-track")
async def download_playlist_track_endpoint(req: PlaylistTrackDownloadRequest):
    """Download, tag, and upload a single playlist track to YouTube Music locker."""
    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        res = await download_and_upload_playlist_track(
            video_id=req.video_id,
            raw_title=req.title,
            raw_artist=req.artist,
            raw_album=req.album,
            raw_thumbnail=req.thumbnail,
            destination_dir=Path(req.destination_dir) if req.destination_dir else None,
            enrich_metadata=req.enrich_metadata
        )
        return res
    except Exception as e:
        logger.exception(f"Failed to download and upload track {req.video_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download and upload track: {e}")

@app.post("/api/ytm/playlists/import-url")
async def import_playlist_url_endpoint(req: PlaylistImportRequest):
    """Import and audit an external YouTube / YouTube Music playlist URL."""
    try:
        raw_info = await extract_playlist_info(req.url)
        tracks_raw = raw_info.get("tracks", [])

        # Match against local files and locker uploads
        local_files = await db.get_all_local_songs()
        uploads = await db.get_all_ytm_uploads()

        from .normalizer import normalize_text

        local_map = {}
        for f in local_files:
            key = f"{normalize_text(f.get('artist'))}|{normalize_text(f.get('title'))}"
            local_map[key] = f.get("path")
            title_key = normalize_text(f.get("title"))
            if title_key and title_key not in local_map:
                local_map[title_key] = f.get("path")

        uploads_set = set()
        for u in uploads:
            u_key = f"{normalize_text(u.artist)}|{normalize_text(u.title)}"
            uploads_set.add(u_key)
            u_title = normalize_text(u.title)
            if u_title:
                uploads_set.add(u_title)

        matched_tracks = []
        for t in tracks_raw:
            title = t.get("title", "")
            artist = t.get("artist")
            key = f"{normalize_text(artist)}|{normalize_text(title)}"
            title_k = normalize_text(title)

            in_local = key in local_map or title_k in local_map
            local_path = local_map.get(key) or local_map.get(title_k)
            in_uploads = key in uploads_set or title_k in uploads_set

            matched_tracks.append({
                **t,
                "in_local": in_local,
                "local_path": local_path,
                "in_uploads": in_uploads
            })

        return {
            **raw_info,
            "tracks": matched_tracks
        }
    except Exception as e:
        logger.exception(f"Failed to import playlist from URL {req.url}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to import playlist: {e}")

@app.get("/api/ytm/playlists/{playlist_id}")
async def get_ytm_playlist_details(playlist_id: str):
    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        details = await ytm_client.get_playlist_details(playlist_id)
        return details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlist details: {e}")

@app.post("/api/ytm/playlists/{playlist_id}/sync-missing")
async def sync_missing_playlist_tracks(playlist_id: str, destination_dir: Optional[str] = None):
    """Start background sync for all tracks in a playlist missing from uploads."""
    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        details = await ytm_client.get_playlist_details(playlist_id)
        tracks = details.get("tracks", [])
        # Filter for tracks missing from cloud uploads
        missing = [t for t in tracks if not t.get("in_uploads")]
        if not missing:
            return {"status": "ok", "message": "All tracks in this playlist are already in your cloud uploads!", "queued": 0}

        status = playlist_sync_manager.start_sync(
            playlist_id=playlist_id,
            playlist_title=details.get("title", "Playlist"),
            tracks_to_sync=missing,
            destination_dir=destination_dir
        )
        return {
            "status": "started",
            "message": f"Started syncing {len(missing)} missing tracks from '{details.get('title')}'",
            "queued": len(missing),
            "sync_status": status
        }
    except RuntimeError as re:
        raise HTTPException(status_code=409, detail=str(re))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start playlist sync: {e}")


def format_size(bytes_val: Optional[int | float]) -> str:
    if bytes_val is None or bytes_val < 0:
        return "N/A"
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.1f} PiB"

@app.get("/api/fs/browse")
async def browse_filesystem(path: Optional[str] = Query(None)):
    """Browse directories inside the container filesystem."""
    if not path:
        if Path("/music").exists() and Path("/music").is_dir():
            target_path = Path("/music")
        else:
            target_path = Path("/")
    else:
        target_path = Path(path)

    try:
        target_path = target_path.resolve()
        if not target_path.exists() or not target_path.is_dir():
            target_path = Path("/")
    except Exception:
        target_path = Path("/")

    directories = []
    try:
        for entry in os.scandir(str(target_path)):
            try:
                # Exclude system pseudo-filesystems when browsing /
                if str(target_path) == "/" and entry.name in ("proc", "sys", "dev", "run"):
                    continue
                if entry.is_dir(follow_symlinks=True):
                    directories.append({
                        "name": entry.name,
                        "path": entry.path
                    })
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    directories.sort(key=lambda x: x["name"].lower())
    parent_path = str(target_path.parent) if target_path != target_path.parent else None

    free_space = "N/A"
    total_space = "N/A"
    try:
        usage = shutil.disk_usage(str(target_path))
        free_space = format_size(usage.free)
        total_space = format_size(usage.total)
    except Exception:
        pass

    return {
        "current_path": str(target_path),
        "parent_path": parent_path,
        "directories": directories,
        "free_space": free_space,
        "total_space": total_space
    }

@app.get("/api/folders")
async def get_folders() -> list[str]:
    folders = await db.get_setting("music_folders", default=[])
    return folders

@app.get("/api/folders/stats")
async def get_folders_stats():
    """Get root folder statistics including free space, songs count, and unmapped count."""
    folders = await db.get_setting("music_folders", default=[])
    results = []
    for f in folders:
        p = Path(f)
        free_space = "N/A"
        total_space = "N/A"
        exists = p.exists() and p.is_dir()
        if exists:
            try:
                usage = shutil.disk_usage(str(p))
                free_space = format_size(usage.free)
                total_space = format_size(usage.total)
            except Exception:
                pass

        counts = await db.get_folder_song_counts(f)
        results.append({
            "path": f,
            "exists": exists,
            "free_space": free_space,
            "total_space": total_space,
            "songs_count": counts["total"],
            "unmapped_count": counts["unmapped"]
        })
    return results

class FoldersUpdate(BaseModel):
    folders: list[str]

@app.post("/api/folders")
async def update_folders(req: FoldersUpdate):
    await db.set_setting("music_folders", req.folders)
    return {"status": "success", "folders": req.folders}

@app.post("/api/scan")
async def trigger_scan(bg_tasks: BackgroundTasks, req: Optional[ScanRequest] = None):
    if scanner.is_scanning:
        return {"status": "in_progress", "message": "Scan is already running"}

    folders = req.folders if req and req.folders else await db.get_setting("music_folders", default=[])
    if not folders:
        raise HTTPException(status_code=400, detail="No music folders configured to scan")

    async def _run_scan_and_match():
        await scanner.scan_folders(folders)
        # Re-run matching automatically after scanning
        await matcher.match_all()

    bg_tasks.add_task(_run_scan_and_match)
    return {"status": "started", "message": "Scan started in background", "folders": folders}

@app.get("/api/songs")
async def get_songs(
    status: Optional[str] = Query(None, description="Filter: all, missing, uploaded, failed, queued"),
    search: Optional[str] = Query(None, description="Search query across title, artist, album"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0)
) -> list[MusicFile]:
    return await db.get_music_files(filter_status=status, search=search, limit=limit, offset=offset)

class MetadataUpdateRequest(BaseModel):
    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = None
    cover_url: Optional[str] = None

@app.post("/api/songs/{file_id}/metadata", response_model=MusicFile)
async def update_song_metadata(file_id: int, req: MetadataUpdateRequest):
    """Update title, artist, album, track_number for a local song and write to file if writable."""
    try:
        existing = await db.get_music_file_by_id(file_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Music file with ID {file_id} not found")

        updated = await db.update_music_file_metadata(
            file_id=file_id,
            title=req.title.strip(),
            artist=req.artist.strip() if req.artist else None,
            album=req.album.strip() if req.album else None,
            track_number=req.track_number
        )
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to update music file metadata in database")

        from .scanner import write_metadata_tags
        try:
            await asyncio.to_thread(
                write_metadata_tags,
                Path(existing.path),
                title=req.title.strip(),
                artist=req.artist.strip() if req.artist else None,
                album=req.album.strip() if req.album else None,
                track_number=req.track_number,
                cover_url=req.cover_url
            )
        except Exception as e:
            logger.warning(f"Could not write tags directly to file {existing.path}: {e}")

        # Re-evaluate matching for this file
        try:
            await matcher.match_single_file(updated)
        except Exception as e:
            logger.warning(f"Matching re-evaluation failed: {e}")

        refreshed = await db.get_music_file_by_id(file_id)
        return refreshed or updated
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Unhandled error in update_song_metadata: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating metadata: {str(e)}")

@app.post("/api/sync")
async def sync_remote_and_match(bg_tasks: BackgroundTasks):
    """Fetch remote YTM uploads and run local comparison matching."""
    async def _run_sync():
        try:
            logger.info("Fetching remote YouTube Music uploads...")
            await ytm_client.fetch_and_cache_uploads()
            logger.info("Running matching engine...")
            await matcher.match_all()
        except Exception as e:
            logger.error(f"Sync failed: {e}")

    bg_tasks.add_task(_run_sync)
    return {"status": "started", "message": "Library synchronization started"}

@app.post("/api/upload/{file_id}")
async def upload_single(file_id: int):
    file = await db.get_music_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="Music file not found")
    job_id = await queue_manager.enqueue_song(file_id)
    return {"status": "enqueued", "job_id": job_id, "filename": file.filename}

@app.post("/api/upload/all-missing")
async def upload_all_missing():
    count = await queue_manager.enqueue_all_missing()
    return {"status": "enqueued", "enqueued_count": count}

@app.get("/api/uploads", response_model=list[YtmUpload])
async def get_uploads() -> list[YtmUpload]:
    return await db.get_all_ytm_uploads()

@app.get("/api/jobs/{job_id}", response_model=SyncJob)
async def get_job_by_id(job_id: int) -> SyncJob:
    job = await db.get_sync_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    return job

@app.get("/api/history")
async def get_history(limit: int = 100) -> list[SyncJob]:
    return await db.get_sync_history(limit=limit)

@app.get("/api/logs")
async def get_recent_logs(lines: int = Query(100, ge=1, le=1000)) -> list[str]:
    if not log_file.exists():
        return []
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
        return [l.rstrip("\r\n") for l in all_lines[-lines:]]

@app.get("/api/settings")
async def get_all_settings():
    folders = await db.get_setting("music_folders", default=[])
    auto_upload = await db.get_setting("auto_upload", default=False)
    scan_interval = await db.get_setting("scan_interval_minutes", default=15)
    verify_uploads = await db.get_setting("verify_uploads", default=True)
    return {
        "music_folders": folders,
        "auto_upload": auto_upload,
        "scan_interval_minutes": scan_interval,
        "verify_uploads": verify_uploads,
    }

class SettingsUpdate(BaseModel):
    auto_upload: Optional[bool] = None
    scan_interval_minutes: Optional[int] = None
    verify_uploads: Optional[bool] = None

@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    if req.auto_upload is not None:
        await db.set_setting("auto_upload", req.auto_upload)
    if req.scan_interval_minutes is not None:
        await db.set_setting("scan_interval_minutes", req.scan_interval_minutes)
    if req.verify_uploads is not None:
        await db.set_setting("verify_uploads", req.verify_uploads)
    return {"status": "success"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/api/musicbrainz/search", response_model=list[MusicBrainzMatch])
async def search_musicbrainz(
    query: Optional[str] = Query(None, description="Free-text search query"),
    artist: Optional[str] = Query(None, description="Artist name"),
    title: Optional[str] = Query(None, description="Song title"),
    provider: Optional[str] = Query("all", description="Metadata provider: all, ytm, deezer, itunes, musicbrainz"),
    limit: int = Query(6, ge=1, le=12, description="Max results")
):
    try:
        matches = await musicbrainz_client.search(
            query=query,
            artist=artist,
            title=title,
            provider=provider,
            limit=limit
        )
        return matches
    except Exception as e:
        logger.error(f"Error in musicbrainz search endpoint: {e}", exc_info=True)
        return []

@app.post("/api/database/backup")
async def backup_db():
    backup_path = await db.backup_database()
    return {"status": "success", "backup_path": backup_path}

@app.get("/api/ytm/uploads/summary")
async def get_ytm_uploads_summary():
    """Get summary counts of YTM uploads (total, missing metadata, properly tagged)."""
    return await db.get_ytm_uploads_summary()

@app.get("/api/ytm/uploads")
async def get_ytm_uploads(
    filter_type: str = Query("all", description="Filter: all, missing_metadata, proper"),
    search: Optional[str] = Query(None, description="Search query"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200)
):
    """List YouTube Music uploads from DB with pagination, search, and health filters."""
    return await db.get_ytm_uploads(filter_type=filter_type, search=search, page=page, page_size=page_size)

@app.post("/api/ytm/uploads/{entity_id}/replace")
async def replace_ytm_upload(entity_id: str, req: MetadataUpdateRequest):
    """Download untagged upload from YTM, tag with new metadata, upload new version, and delete old upload."""
    upload = await db.get_ytm_upload_by_entity_id(entity_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload entity not found in database.")
    if not upload.video_id:
        raise HTTPException(status_code=400, detail="Upload does not have an associated video ID for streaming.")

    downloaded_path: Optional[Path] = None
    try:
        staging_dir = settings.data_dir / "staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        target_staging = staging_dir / f"ytm_{upload.video_id}_clean.mp3"

        # Phase 0: Check if this file already exists locally in DB matches, music_files, or /music
        local_fp = await db.get_local_filepath_for_upload(entity_id)
        if not local_fp:
            async with db.get_connection() as conn:
                clean_name = upload.title.strip()
                stem = Path(clean_name).stem
                async with conn.execute(
                    "SELECT path FROM music_files WHERE filename = ? OR filename = ? OR title = ? LIMIT 1",
                    (clean_name, f"{stem}.mp3", clean_name)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        local_fp = row[0]

        if local_fp and Path(local_fp).exists():
            logger.info(f"Found local file for upload {entity_id} via library: {local_fp}")
            shutil.copy2(local_fp, target_staging)
            downloaded_path = target_staging

        if not downloaded_path and Path("/music").is_dir():
            clean_name = upload.title.strip()
            stem = Path(clean_name).stem
            candidates = list(Path("/music").rglob(f"{clean_name}*"))
            if not candidates:
                candidates = list(Path("/music").rglob(f"{stem}.*"))
            valid = [c for c in candidates if c.is_file()]
            if valid:
                logger.info(f"Found local file for upload {entity_id} in /music: {valid[0]}")
                shutil.copy2(valid[0], target_staging)
                downloaded_path = target_staging

        # 1. Download audio file from YTM if not available locally
        if not downloaded_path:
            logger.info(f"Phase 1: Downloading untagged upload {entity_id} (video: {upload.video_id})")
            downloaded_path = await download_ytm_upload(upload.video_id)

        # 2. Write new metadata tags using Mutagen
        logger.info(f"Phase 2: Tagging audio with Title='{req.title}', Artist='{req.artist}', Album='{req.album}', CoverURL='{req.cover_url}'")
        await asyncio.to_thread(
            write_metadata_tags,
            downloaded_path,
            title=req.title.strip(),
            artist=req.artist.strip() if req.artist else None,
            album=req.album.strip() if req.album else None,
            track_number=req.track_number,
            cover_url=req.cover_url
        )

        # 3. Upload new tagged version to YouTube Music
        logger.info(f"Phase 3: Uploading newly tagged file {downloaded_path.name} to YTM")
        up_res = await ytm_client.upload_file(str(downloaded_path))
        if not up_res.get("success"):
            raise HTTPException(status_code=500, detail=f"Upload failed: {up_res.get('response')}")

        # 4. Delete old untagged upload from YouTube Music
        logger.info(f"Phase 4: Deleting old untagged upload entity {entity_id} from YTM")
        try:
            await ytm_client.delete_upload(entity_id)
        except Exception as e:
            logger.warning(f"Failed to delete old upload {entity_id} from YTM (non-fatal): {e}")

        # Mark deleted in memory so stale YTM continuation caches cannot resurrect it
        ytm_client.mark_deleted(entity_id)

        clean_title = req.title.strip()
        clean_artist = req.artist.strip() if req.artist else None
        clean_album = req.album.strip() if req.album else None
        clean_thumb = req.cover_url.strip() if req.cover_url else upload.thumbnail

        # Check if the retagged upload is still missing required metadata (artist, album, artwork, clean title)
        still_missing = (
            not clean_artist or clean_artist.lower() in ('unknown artist', 'unknown') or
            not clean_album or clean_album.lower() in ('unknown album', 'unknown') or
            not clean_thumb or
            clean_title.lower().endswith(('.mp3', '.flac', '.m4a', '.wav', '.opus', '.webm'))
        )

        # 5. If still missing any metadata, update local DB so it stays on list with fresh data.
        # If fully fixed, delete old untagged record so it disappears from missing metadata list.
        if still_missing:
            await db.update_ytm_upload(
                entity_id=entity_id,
                title=clean_title,
                artist=clean_artist,
                album=clean_album,
                thumbnail=clean_thumb
            )
        else:
            await db.delete_ytm_upload_record(entity_id)

        # Trigger delayed background refresh so YTM has time to process the newly uploaded file
        async def _delayed_refresh():
            await asyncio.sleep(20)
            try:
                await ytm_client.fetch_and_cache_uploads()
            except Exception as ex:
                logger.debug(f"Delayed YTM uploads refresh notice: {ex}")

        asyncio.create_task(_delayed_refresh())

        return {
            "status": "success",
            "message": f"Successfully retagged and replaced '{clean_title}' on YouTube Music.",
            "still_missing": still_missing,
            "title": clean_title,
            "artist": clean_artist,
            "album": clean_album,
            "thumbnail": clean_thumb
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to replace upload {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to replace upload: {str(e)}")
    finally:
        # Aggressive cleanup of temporary/staging audio files
        if downloaded_path and downloaded_path.exists():
            try:
                downloaded_path.unlink()
            except Exception:
                pass
        if upload and upload.video_id:
            staging_dir = settings.data_dir / "staging"
            if staging_dir.exists():
                for tmp_f in staging_dir.glob(f"*{upload.video_id}*"):
                    try:
                        tmp_f.unlink()
                    except Exception:
                        pass

@app.get("/api/songs/{file_id}/artwork")
async def get_song_artwork(file_id: int):
    """Serve embedded cover art from a local music file."""
    file = await db.get_music_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="Song not found")
    from .scanner import extract_artwork
    res = await asyncio.to_thread(extract_artwork, Path(file.path))
    if not res:
        raise HTTPException(status_code=404, detail="No embedded artwork found")
    data, mime = res
    from fastapi.responses import Response
    return Response(content=data, media_type=mime)

@app.get("/api/metadata/cover-art")
async def get_cover_art_url(
    artist: str = Query(...),
    title: Optional[str] = Query(None),
    album: Optional[str] = Query(None),
):
    """Fetch high-res album artwork URL for a track or album."""
    url = await musicbrainz_client.fetch_cover_art_url(artist=artist, title=title, album=album)
    return {"cover_url": url}

@app.delete("/api/ytm/uploads/{entity_id}")
async def delete_ytm_upload(entity_id: str):
    """Delete an upload directly from YouTube Music and from the local database."""
    try:
        await ytm_client.delete_upload(entity_id)
        await db.delete_ytm_upload_record(entity_id)
        return {"status": "success", "message": f"Deleted upload entity {entity_id}."}
    except Exception as e:
        logger.error(f"Failed to delete upload {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete upload: {str(e)}")

class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

# Mount Flutter Web frontend if built
web_dir_candidates = [
    settings.web_dir,
    Path(__file__).resolve().parent.parent / "web_dist",
    Path(__file__).resolve().parent.parent.parent / "app" / "build" / "web"
]
for candidate in web_dir_candidates:
    if candidate.is_dir() and (candidate / "index.html").exists():
        logger.info(f"Serving Flutter Web UI from {candidate}")
        app.mount("/", NoCacheStaticFiles(directory=str(candidate), html=True), name="web")
        break

def start():
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)

if __name__ == "__main__":
    start()
