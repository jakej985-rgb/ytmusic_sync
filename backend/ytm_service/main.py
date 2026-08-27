import asyncio
import logging
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
    ScanRequest, AuthSetupRequest, ConnectionStatus
)
from .scanner import scanner
from .ytm_client import ytm_client
from .matcher import matcher
from .uploader import queue_manager
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

@app.get("/api/folders")
async def get_folders() -> list[str]:
    folders = await db.get_setting("music_folders", default=[])
    return folders

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

@app.post("/api/database/backup")
async def backup_db():
    backup_path = await db.backup_database()
    return {"status": "success", "backup_path": backup_path}

# Mount Flutter Web frontend if built
web_dir_candidates = [
    settings.web_dir,
    Path(__file__).resolve().parent.parent / "web_dist",
    Path(__file__).resolve().parent.parent.parent / "app" / "build" / "web"
]
for candidate in web_dir_candidates:
    if candidate.is_dir() and (candidate / "index.html").exists():
        logger.info(f"Serving Flutter Web UI from {candidate}")
        app.mount("/", StaticFiles(directory=str(candidate), html=True), name="web")
        break

def start():
    import uvicorn
    uvicorn.run(app, host=settings.host, port=settings.port)

if __name__ == "__main__":
    start()
