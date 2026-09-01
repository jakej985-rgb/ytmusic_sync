import asyncio
import logging
import os
import shutil
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
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
from .queue_service import unified_queue_service
from .metadata_tracker import metadata_tracker
from .security import (
    verify_api_key_header,
    validate_fs_path,
    validate_youtube_url,
    get_allowed_roots,
)
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
    # Recover interrupted upload jobs and resume worker if tasks are queued
    await queue_manager.reconcile_and_resume()
    # Start periodic background scanner
    background_scanner_task = asyncio.create_task(_periodic_scan_loop())
    yield
    # Shutdown: Clean up background workers
    logger.info("Stopping background upload worker and periodic scanner...")
    if background_scanner_task:
        background_scanner_task.cancel()
    await queue_manager.stop_worker()

app = FastAPI(
    title="YTM Sync Backend Service",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)

# Restricted CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "User-Agent"],
)

@app.middleware("http")
async def authenticate_api_requests(request: Request, call_next):
    path = request.url.path
    # Allow public health endpoint and static frontend files
    if path == "/health" or not path.startswith("/api/"):
        return await call_next(request)
    # Allow CORS preflight OPTIONS requests without credentials
    if request.method == "OPTIONS":
        return await call_next(request)

    auth_hdr = request.headers.get("Authorization")
    if not verify_api_key_header(auth_hdr):
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid or missing API key"},
            headers={"WWW-Authenticate": "Bearer"}
        )
    return await call_next(request)


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
    dest_path = None
    if req.destination_dir:
        try:
            dest_path = validate_fs_path(req.destination_dir, allow_create_in_parent=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid destination_dir: {e}")

    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        res = await download_and_upload_playlist_track(
            video_id=req.video_id,
            raw_title=req.title,
            raw_artist=req.artist,
            raw_album=req.album,
            raw_thumbnail=req.thumbnail,
            destination_dir=dest_path,
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
        validate_youtube_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid playlist URL: {e}")
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
async def get_ytm_playlist_details(playlist_id: str, refresh: bool = False):
    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        if refresh:
            try:
                await ytm_client.fetch_and_cache_uploads()
            except Exception as ex:
                logger.warning(f"Failed to refresh uploads from YTM: {ex}")
        details = await ytm_client.get_playlist_details(playlist_id)
        return details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlist details: {e}")

@app.post("/api/ytm/playlists/{playlist_id}/sync-missing")
async def sync_missing_playlist_tracks(playlist_id: str, destination_dir: Optional[str] = None):
    """Start background sync for all tracks in a playlist missing from uploads."""
    dest_path = None
    if destination_dir:
        try:
            dest_path = str(validate_fs_path(destination_dir, allow_create_in_parent=True))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid destination_dir: {e}")

    if not ytm_client.is_auth_configured():
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")

    try:
        # Refresh current uploads from YTM so deleted tracks can be detected and re-synced!
        try:
            await ytm_client.fetch_and_cache_uploads()
        except Exception as ex:
            logger.warning(f"Could not refresh remote uploads before sync: {ex}")

        details = await ytm_client.get_playlist_details(playlist_id)
        tracks = details.get("tracks", [])
        # Filter for tracks missing from cloud uploads and not duplicates
        missing = [t for t in tracks if not t.get("in_uploads") and not t.get("is_duplicate")]
        if not missing:
            return {"status": "ok", "message": "All tracks in this playlist are already in your cloud uploads!", "queued": 0}

        status = playlist_sync_manager.start_sync(
            playlist_id=playlist_id,
            playlist_title=details.get("title", "Playlist"),
            tracks_to_sync=missing,
            destination_dir=dest_path
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
    """Browse directories inside approved container filesystem roots."""
    allowed_roots = get_allowed_roots()
    if not allowed_roots:
        raise HTTPException(status_code=500, detail="No approved filesystem roots configured")

    if not path:
        target_path = next((r for r in allowed_roots if r.exists()), allowed_roots[0])
    else:
        try:
            target_path = validate_fs_path(path, must_exist=True)
        except ValueError as e:
            raise HTTPException(status_code=403, detail=str(e))

    if not target_path.is_dir():
        raise HTTPException(status_code=400, detail="Requested path is not a directory")

    directories = []
    try:
        for entry in os.scandir(str(target_path)):
            try:
                if entry.is_dir(follow_symlinks=True):
                    entry_p = Path(entry.path)
                    try:
                        validate_fs_path(entry_p, must_exist=True)
                        directories.append({
                            "name": entry.name,
                            "path": str(entry_p)
                        })
                    except ValueError:
                        continue
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass

    directories.sort(key=lambda x: x["name"].lower())

    parent_path = None
    if target_path != target_path.parent:
        try:
            parent_resolved = validate_fs_path(target_path.parent, must_exist=True)
            parent_path = str(parent_resolved)
        except ValueError:
            parent_path = None

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
        "total_space": total_space,
        "allowed_roots": [str(r) for r in allowed_roots]
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
    folders: list[str] = Field(..., min_length=1, max_length=50, description="Music folders allowlist (max 50)")

@app.post("/api/folders")
async def update_folders(req: FoldersUpdate):
    safe_folders = []
    for f in req.folders:
        try:
            p = validate_fs_path(f, must_exist=False)
            safe_folders.append(str(p))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid folder path '{f}': {e}")
    await db.set_setting("music_folders", safe_folders)
    return {"status": "success", "folders": safe_folders}

@app.post("/api/scan")
async def trigger_scan(bg_tasks: BackgroundTasks, req: Optional[ScanRequest] = None):
    if scanner.is_scanning:
        return {"status": "in_progress", "message": "Scan is already running"}

    raw_folders = req.folders if req and req.folders else await db.get_setting("music_folders", default=[])
    if not raw_folders:
        raise HTTPException(status_code=400, detail="No music folders configured to scan")

    folders = []
    for f in raw_folders:
        try:
            p = validate_fs_path(f, must_exist=True)
            folders.append(str(p))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid scan folder '{f}': {e}")

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
        tags_written = False
        target_path = Path(existing.path)
        is_writable = False
        try:
            is_writable = target_path.exists() and os.access(target_path, os.W_OK)
        except Exception:
            is_writable = False

        if is_writable:
            try:
                await asyncio.to_thread(
                    write_metadata_tags,
                    target_path,
                    title=req.title.strip(),
                    artist=req.artist.strip() if req.artist else None,
                    album=req.album.strip() if req.album else None,
                    track_number=req.track_number,
                    cover_url=req.cover_url
                )
                tags_written = True
            except (PermissionError, OSError) as e:
                logger.warning(f"File system is read-only; tags not written to {existing.path}: {e}")
            except Exception as e:
                logger.warning(f"Could not write tags directly to file {existing.path}: {e}")
        else:
            logger.info(f"Skipping direct tag modification for read-only file: {existing.path}")

        # Re-evaluate matching for this file
        try:
            await matcher.match_single_file(updated)
        except Exception as e:
            logger.warning(f"Matching re-evaluation failed: {e}")

        refreshed = await db.get_music_file_by_id(file_id)
        final_obj = refreshed or updated
        detail_msg = "Updated ID3 tags & metadata on disk" if tags_written else "Updated metadata in database (local file is read-only; tags untouched)"
        metadata_tracker.log_change(
            title=final_obj.title or req.title,
            artist=final_obj.artist,
            album=final_obj.album,
            thumbnail=req.cover_url,
            source="Local Song Metadata Editor",
            detail=detail_msg
        )
        return final_obj
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

class BatchUploadRequest(BaseModel):
    file_ids: list[int] = Field(..., min_length=1, max_length=500, description="List of file IDs to enqueue (max 500)")

@app.post("/api/upload/batch")
async def upload_batch(req: BatchUploadRequest):
    enqueued = 0
    for fid in req.file_ids:
        try:
            await queue_manager.enqueue_song(fid)
            enqueued += 1
        except Exception as e:
            logger.warning(f"Failed to enqueue song {fid}: {e}")
    return {"status": "enqueued", "enqueued_count": enqueued}

@app.post("/api/upload/all-missing")
async def upload_all_missing():
    count = await queue_manager.enqueue_all_missing()
    return {"status": "enqueued", "enqueued_count": count}

@app.post("/api/upload/{file_id}")
async def upload_single(file_id: int):
    file = await db.get_music_file_by_id(file_id)
    if not file:
        raise HTTPException(status_code=404, detail="Music file not found")
    job_id = await queue_manager.enqueue_song(file_id)
    return {"status": "enqueued", "job_id": job_id, "filename": file.filename}

class BatchDeleteSongsRequest(BaseModel):
    file_ids: list[int] = Field(..., min_length=1, max_length=500, description="List of song IDs to delete (max 500)")

@app.post("/api/songs/batch-delete")
async def batch_delete_songs(req: BatchDeleteSongsRequest):
    deleted = 0
    async with db.get_connection() as conn:
        for fid in req.file_ids:
            await conn.execute("DELETE FROM matches WHERE music_file_id = ?", (fid,))
            await conn.execute("DELETE FROM sync_jobs WHERE music_file_id = ?", (fid,))
            await conn.execute("DELETE FROM music_files WHERE id = ?", (fid,))
            deleted += 1
        await conn.commit()
    return {"status": "success", "deleted": deleted}

@app.get("/api/queue")
async def get_unified_queue(
    category: str = Query("all", description="all, metadata_change, download, upload, local_upload"),
    status: str = Query("all", description="all, active, completed, failed"),
    limit: int = Query(200, ge=1, le=1000)
):
    """Fetch unified queue aggregating playlist downloads, cloud locker uploads, local uploads, and metadata changes."""
    return await unified_queue_service.get_queue(category=category, status=status, limit=limit)

@app.post("/api/queue/clear-completed")
async def clear_completed_queue():
    """Clear completed and failed items from queue history."""
    unified_queue_service.clear_completed()
    return {"status": "ok"}

@app.post("/api/queue/cancel-all")
async def cancel_all_queue():
    """Cancel any active playlist sync and clear queued local jobs."""
    await unified_queue_service.cancel_all()
    return {"status": "ok"}

class ResolveNeedsHelpRequest(BaseModel):
    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    thumbnail: Optional[str] = None
    destination_dir: Optional[str] = None

@app.get("/api/needs-help")
async def get_needs_help_tracks():
    """List all tracks skipped during download because metadata match was missing."""
    return await db.get_needs_help_tracks()

@app.delete("/api/needs-help/{video_id}")
async def dismiss_needs_help_track(video_id: str):
    """Dismiss a track from needs-help list."""
    await db.delete_needs_help_track(video_id)
    return {"status": "ok"}

@app.post("/api/needs-help/{video_id}/resolve")
async def resolve_needs_help_track(video_id: str, req: ResolveNeedsHelpRequest):
    """Resolve a needs-help track with user-selected metadata, download, and upload."""
    dest_path = None
    if req.destination_dir:
        try:
            dest_path = validate_fs_path(req.destination_dir, allow_create_in_parent=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid destination_dir: {e}")

    res = await download_and_upload_playlist_track(
        video_id=video_id,
        raw_title=req.title,
        raw_artist=req.artist,
        raw_album=req.album,
        raw_thumbnail=req.thumbnail,
        destination_dir=dest_path,
        enrich_metadata=False,
        require_full_match=False
    )
    if res.get("status") == "success":
        await db.delete_needs_help_track(video_id)
        metadata_tracker.log_change(
            title=res.get("title") or req.title,
            artist=res.get("artist") or req.artist,
            album=res.get("album") or req.album,
            thumbnail=res.get("cover_url") or req.thumbnail,
            source="Needs Help Matcher",
            detail="Resolved metadata & uploaded to YouTube Music locker"
        )
    return res

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
    return {"status": "healthy", "version": __version__}

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

        if local_fp:
            try:
                safe_local = validate_fs_path(local_fp, must_exist=True)
                logger.info(f"Found local file for upload {entity_id} via library: {safe_local}")
                shutil.copy2(safe_local, target_staging)
                downloaded_path = target_staging
            except Exception as e:
                logger.warning(f"Local file {local_fp} failed path validation: {e}")

        if not downloaded_path and Path("/music").is_dir():
            clean_name = upload.title.strip()
            stem = Path(clean_name).stem
            candidates = list(Path("/music").rglob(f"{clean_name}*"))
            if not candidates:
                candidates = list(Path("/music").rglob(f"{stem}.*"))
            valid = [c for c in candidates if c.is_file()]
            if valid:
                try:
                    safe_candidate = validate_fs_path(valid[0], must_exist=True)
                    logger.info(f"Found local file for upload {entity_id} in /music: {safe_candidate}")
                    shutil.copy2(safe_candidate, target_staging)
                    downloaded_path = target_staging
                except Exception as e:
                    logger.warning(f"Candidate file {valid[0]} failed path validation: {e}")

        # 1. Download audio file from YTM if not available locally
        if not downloaded_path:
            logger.info(f"Phase 1: Downloading untagged upload {entity_id} (video: {upload.video_id})")
            search_query = f"{req.artist or ''} {req.title}".strip()
            downloaded_path = await download_ytm_upload(upload.video_id, fallback_query=search_query)

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
        if clean_thumb and clean_thumb.startswith("data:image/"):
            try:
                import base64
                art_dir = settings.data_dir / "artwork"
                art_dir.mkdir(parents=True, exist_ok=True)
                _, b64_data = clean_thumb.split(",", 1)
                art_bytes = base64.b64decode(b64_data)
                art_file = art_dir / f"custom_{upload.video_id}.jpg"
                art_file.write_bytes(art_bytes)
                clean_thumb = f"/api/artwork/{art_file.name}"
            except Exception as e:
                logger.warning(f"Could not persist custom artwork: {e}")

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

        metadata_tracker.log_change(
            title=clean_title,
            artist=clean_artist,
            album=clean_album,
            thumbnail=clean_thumb,
            source="Cloud Upload Re-tagger",
            detail="Replaced and retagged on YouTube Music"
        )

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
        ytm_client.mark_deleted(entity_id)
        return {"status": "success", "message": f"Deleted upload entity {entity_id}."}
    except Exception as e:
        logger.error(f"Failed to delete upload {entity_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete upload: {str(e)}")

class BatchDeleteUploadsRequest(BaseModel):
    entity_ids: list[str] = Field(..., min_length=1, max_length=100, description="List of YTM upload entity IDs to delete (max 100)")

@app.post("/api/ytm/uploads/batch-delete")
async def batch_delete_ytm_uploads(req: BatchDeleteUploadsRequest):
    """Batch delete uploads directly from YouTube Music and from the local database."""
    deleted = 0
    failed = 0
    for eid in req.entity_ids:
        try:
            await ytm_client.delete_upload(eid)
            await db.delete_ytm_upload_record(eid)
            ytm_client.mark_deleted(eid)
            deleted += 1
        except Exception as e:
            logger.warning(f"Failed to delete upload {eid}: {e}")
            failed += 1
    return {"status": "success", "deleted": deleted, "failed": failed}

@app.get("/api/artwork/{filename}")
async def get_artwork_file(filename: str):
    """Serve custom uploaded artwork images."""
    from fastapi.responses import FileResponse
    art_dir = (settings.data_dir / "artwork").resolve()
    try:
        art_file = validate_fs_path(art_dir / filename, allowed_roots=[art_dir], must_exist=True)
    except ValueError:
        raise HTTPException(status_code=404, detail="Artwork file not found")
    if not art_file.is_file():
        raise HTTPException(status_code=404, detail="Artwork file not found")
    return FileResponse(art_file)

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
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips
    )

if __name__ == "__main__":
    start()
