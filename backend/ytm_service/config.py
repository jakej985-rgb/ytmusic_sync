import os
import shutil
from pathlib import Path
from pydantic import BaseModel

if "YTM_SYNC_DATA_DIR" in os.environ:
    DEFAULT_DATA_DIR = Path(os.environ["YTM_SYNC_DATA_DIR"])
elif Path("/config").is_dir() and os.access("/config", os.W_OK):
    DEFAULT_DATA_DIR = Path("/config")
else:
    DEFAULT_DATA_DIR = Path.home() / ".config" / "ytm_sync"

DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Subdirectories for clean persistent state
DATABASE_DIR = DEFAULT_DATA_DIR / "database"
AUTH_DIR = DEFAULT_DATA_DIR / "auth"
LOGS_DIR = DEFAULT_DATA_DIR / "logs"
BACKUPS_DIR = DEFAULT_DATA_DIR / "backups"

for d in (DATABASE_DIR, AUTH_DIR, LOGS_DIR, BACKUPS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Migration helpers if upgrading from flat root layout
legacy_db = DEFAULT_DATA_DIR / "ytm_sync.db"
target_db = DATABASE_DIR / "ytm_sync.db"
if legacy_db.exists() and not target_db.exists():
    shutil.move(str(legacy_db), str(target_db))

legacy_auth = DEFAULT_DATA_DIR / "headers_auth.json"
target_auth = AUTH_DIR / "headers_auth.json"
if legacy_auth.exists() and not target_auth.exists():
    shutil.move(str(legacy_auth), str(target_auth))

legacy_log = DEFAULT_DATA_DIR / "ytm_sync.log"
target_log = LOGS_DIR / "ytm_sync.log"
if legacy_log.exists() and not target_log.exists():
    shutil.move(str(legacy_log), str(target_log))

is_docker = os.environ.get("DOCKER") == "true" or Path("/.dockerenv").exists()
default_host = os.environ.get("YTM_SYNC_HOST", os.environ.get("HOST", "0.0.0.0" if is_docker else "127.0.0.1"))
default_port = int(os.environ.get("YTM_SYNC_PORT", os.environ.get("PORT", 8080 if is_docker else 8765)))

class Settings(BaseModel):
    data_dir: Path = DEFAULT_DATA_DIR
    database_dir: Path = DATABASE_DIR
    auth_dir: Path = AUTH_DIR
    logs_dir: Path = LOGS_DIR
    backups_dir: Path = BACKUPS_DIR
    db_path: Path = target_db
    auth_file: Path = target_auth
    log_file: Path = target_log
    host: str = default_host
    port: int = default_port
    log_level: str = os.environ.get("LOG_LEVEL", "INFO").upper()
    supported_extensions: list[str] = [".mp3", ".flac", ".m4a", ".ogg", ".wma"]
    scan_interval_minutes: int = 15
    auto_upload: bool = False
    verify_uploads: bool = True
    max_retries: int = 3
    web_dir: Path = Path(__file__).resolve().parent.parent / "web_dist"

settings = Settings()
