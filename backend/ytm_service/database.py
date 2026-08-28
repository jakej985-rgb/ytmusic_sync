import aiosqlite
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any
from .config import settings
from .models import MusicFile, YtmUpload, MatchRecord, SyncJob, UploadStatus, MatchType

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS music_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    title TEXT,
    track_number INTEGER,
    disc_number INTEGER,
    duration REAL,
    format TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    modified_time REAL NOT NULL,
    file_hash TEXT,
    metadata_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ytm_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT UNIQUE NOT NULL,
    video_id TEXT,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    duration REAL,
    like_status TEXT,
    thumbnail TEXT,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    music_file_id INTEGER NOT NULL,
    ytm_upload_id TEXT NOT NULL,
    match_type TEXT NOT NULL,
    match_score REAL NOT NULL,
    confirmed BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(music_file_id) REFERENCES music_files(id) ON DELETE CASCADE,
    UNIQUE(music_file_id, ytm_upload_id)
);

CREATE TABLE IF NOT EXISTS sync_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    music_file_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error TEXT,
    attempts INTEGER DEFAULT 0,
    FOREIGN KEY(music_file_id) REFERENCES music_files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_music_files_path ON music_files(path);
CREATE INDEX IF NOT EXISTS idx_music_files_artist_title ON music_files(artist, title);
CREATE INDEX IF NOT EXISTS idx_ytm_uploads_entity ON ytm_uploads(entity_id);
CREATE INDEX IF NOT EXISTS idx_matches_music_file ON matches(music_file_id);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_status ON sync_jobs(status);
"""

class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path

    def get_connection(self):
        return aiosqlite.connect(self.db_path)

    async def init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA foreign_keys = ON;")
            await db.executescript(CREATE_TABLES_SQL)
            # Reset any interrupted jobs from previous SIGTERM or shutdown back to queued
            await db.execute("UPDATE sync_jobs SET status = 'queued' WHERE status IN ('uploading', 'verifying')")
            await db.commit()

    # Settings operations
    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    try:
                        return json.loads(row["value"])
                    except Exception:
                        return row["value"]
                return default

    async def set_setting(self, key: str, value: Any):
        val_str = json.dumps(value) if not isinstance(value, str) else value
        async with self.get_connection() as db:
            await db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, val_str)
            )
            await db.commit()

    # Music Files operations
    async def upsert_music_file(self, file_info: dict) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                INSERT INTO music_files (
                    path, filename, artist, album, title, track_number, disc_number,
                    duration, format, file_size, modified_time, file_hash, metadata_hash,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    filename=excluded.filename,
                    artist=excluded.artist,
                    album=excluded.album,
                    title=excluded.title,
                    track_number=excluded.track_number,
                    disc_number=excluded.disc_number,
                    duration=excluded.duration,
                    format=excluded.format,
                    file_size=excluded.file_size,
                    modified_time=excluded.modified_time,
                    file_hash=excluded.file_hash,
                    metadata_hash=excluded.metadata_hash,
                    updated_at=excluded.updated_at
                RETURNING id
                """,
                (
                    file_info["path"],
                    file_info["filename"],
                    file_info.get("artist"),
                    file_info.get("album"),
                    file_info.get("title"),
                    file_info.get("track_number"),
                    file_info.get("disc_number"),
                    file_info.get("duration"),
                    file_info["format"],
                    file_info["file_size"],
                    file_info["modified_time"],
                    file_info.get("file_hash"),
                    file_info.get("metadata_hash"),
                    now,
                )
            ) as cursor:
                row = await cursor.fetchone()
                await db.commit()
                return row[0]

    async def get_music_files(
        self,
        filter_status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 200,
        offset: int = 0
    ) -> list[MusicFile]:
        query = """
            SELECT 
                mf.*,
                COALESCE(sj.status, 
                    CASE 
                        WHEN m.id IS NOT NULL THEN 'verified'
                        ELSE 'not_uploaded'
                    END
                ) as upload_status,
                m.ytm_upload_id as matched_upload_id,
                m.match_score as match_score
            FROM music_files mf
            LEFT JOIN matches m ON mf.id = m.music_file_id
            LEFT JOIN (
                SELECT s1.* FROM sync_jobs s1
                JOIN (SELECT music_file_id, MAX(id) as max_id FROM sync_jobs GROUP BY music_file_id) s2
                ON s1.id = s2.max_id
            ) sj ON mf.id = sj.music_file_id
            WHERE 1=1
        """
        params: list[Any] = []

        if search:
            query += " AND (mf.title LIKE ? OR mf.artist LIKE ? OR mf.album LIKE ? OR mf.filename LIKE ?)"
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param, s_param])

        if filter_status == "uploaded":
            query += " AND (m.id IS NOT NULL OR sj.status IN ('uploaded', 'verified'))"
        elif filter_status == "missing":
            query += " AND (m.id IS NULL AND (sj.status IS NULL OR sj.status NOT IN ('uploaded', 'verified', 'uploading', 'queued')))"
        elif filter_status == "failed":
            query += " AND sj.status = 'failed'"
        elif filter_status == "queued":
            query += " AND sj.status IN ('queued', 'uploading', 'verifying')"

        query += " ORDER BY mf.artist ASC, mf.album ASC, mf.track_number ASC, mf.title ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    data = dict(row)
                    raw_st = data.pop("upload_status", "not_uploaded")
                    matched_id = data.pop("matched_upload_id", None)
                    m_score = data.pop("match_score", None)
                    item = MusicFile(**data)
                    try:
                        item.upload_status = UploadStatus(raw_st)
                    except ValueError:
                        item.upload_status = UploadStatus.NOT_UPLOADED
                    item.matched_upload_id = matched_id
                    item.match_score = m_score
                    results.append(item)
                return results

    async def get_music_file_by_id(self, file_id: int) -> Optional[MusicFile]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM music_files WHERE id = ?", (file_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                return MusicFile(**data)

    async def update_music_file_metadata(
        self,
        file_id: int,
        title: str,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        track_number: Optional[int] = None
    ) -> Optional[MusicFile]:
        from .normalizer import compute_metadata_hash
        now = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM music_files WHERE id = ?", (file_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                duration = row["duration"]

            meta_hash = compute_metadata_hash(artist, album, title, duration)

            await db.execute(
                """
                UPDATE music_files
                SET title = ?, artist = ?, album = ?, track_number = ?, metadata_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (title, artist, album, track_number, meta_hash, now, file_id)
            )
            await db.commit()

        return await self.get_music_file_by_id(file_id)

    async def get_all_local_songs(self) -> list[dict]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT path, title, artist, album, duration FROM music_files") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    # YTM Uploads
    async def upsert_ytm_upload(self, upload_info: dict):
        now = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO ytm_uploads (
                    entity_id, video_id, title, artist, album, duration, like_status, thumbnail, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    video_id=excluded.video_id,
                    title=excluded.title,
                    artist=excluded.artist,
                    album=excluded.album,
                    duration=excluded.duration,
                    like_status=excluded.like_status,
                    thumbnail=excluded.thumbnail,
                    last_seen=excluded.last_seen
                """,
                (
                    upload_info["entity_id"],
                    upload_info.get("video_id"),
                    upload_info["title"],
                    upload_info.get("artist"),
                    upload_info.get("album"),
                    upload_info.get("duration"),
                    upload_info.get("like_status"),
                    upload_info.get("thumbnail"),
                    now,
                )
            )
            await db.commit()

    async def get_all_ytm_uploads(self) -> list[YtmUpload]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM ytm_uploads") as cursor:
                rows = await cursor.fetchall()
                return [YtmUpload(**dict(r)) for r in rows]

    # Matches
    async def save_match(self, file_id: int, ytm_upload_id: str, match_type: MatchType | str, score: float):
        m_val = match_type.value if isinstance(match_type, MatchType) else str(match_type)
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO matches (music_file_id, ytm_upload_id, match_type, match_score)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(music_file_id, ytm_upload_id) DO UPDATE SET
                    match_type=excluded.match_type,
                    match_score=excluded.match_score
                """,
                (file_id, ytm_upload_id, m_val, score)
            )
            await db.commit()

    async def is_file_matched(self, music_file_id: int) -> bool:
        async with self.get_connection() as db:
            async with db.execute("SELECT 1 FROM matches WHERE music_file_id = ? LIMIT 1", (music_file_id,)) as cursor:
                return (await cursor.fetchone()) is not None

    async def clear_matches(self):
        async with self.get_connection() as db:
            await db.execute("DELETE FROM matches")
            await db.commit()

    # Sync Jobs & Queue
    async def create_sync_job(self, music_file_id: int) -> int:
        async with self.get_connection() as db:
            async with db.execute(
                "INSERT INTO sync_jobs (music_file_id, status, attempts) VALUES (?, 'queued', 0) RETURNING id",
                (music_file_id,)
            ) as cursor:
                row = await cursor.fetchone()
                await db.commit()
                return row[0]

    async def update_sync_job(
        self,
        job_id: int,
        status: UploadStatus,
        error: Optional[str] = None,
        increment_attempts: bool = False
    ):
        now = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            if status == UploadStatus.UPLOADING:
                await db.execute(
                    "UPDATE sync_jobs SET status = ?, started_at = ?, attempts = attempts + ? WHERE id = ?",
                    (status.value, now, 1 if increment_attempts else 0, job_id)
                )
            elif status in (UploadStatus.VERIFIED, UploadStatus.FAILED):
                await db.execute(
                    "UPDATE sync_jobs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
                    (status.value, now, error, job_id)
                )
            else:
                await db.execute(
                    "UPDATE sync_jobs SET status = ?, error = ? WHERE id = ?",
                    (status.value, error, job_id)
                )
            await db.commit()

    async def get_next_queued_job(self) -> Optional[SyncJob]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT sj.*, mf.path, mf.filename, mf.artist, mf.album, mf.title, mf.duration, mf.format, mf.file_size, mf.modified_time
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                WHERE sj.status = 'queued'
                ORDER BY sj.id ASC LIMIT 1
                """
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                mf_data = {k: data[k] for k in ["path", "filename", "artist", "album", "title", "duration", "format", "file_size", "modified_time"]}
                mf_data["id"] = data["music_file_id"]
                job = SyncJob(
                    id=data["id"],
                    music_file_id=data["music_file_id"],
                    status=UploadStatus(data["status"]),
                    started_at=data["started_at"],
                    completed_at=data["completed_at"],
                    error=data["error"],
                    attempts=data["attempts"],
                    music_file=MusicFile(**mf_data)
                )
                return job

    async def get_sync_job_by_id(self, job_id: int) -> Optional[SyncJob]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT sj.*, mf.path, mf.filename, mf.artist, mf.album, mf.title, mf.duration, mf.format, mf.file_size, mf.modified_time,
                       m.ytm_upload_id
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                LEFT JOIN matches m ON mf.id = m.music_file_id
                WHERE sj.id = ?
                """,
                (job_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                mf_data = {k: data[k] for k in ["path", "filename", "artist", "album", "title", "duration", "format", "file_size", "modified_time"]}
                mf_data["id"] = data["music_file_id"]
                return SyncJob(
                    id=data["id"],
                    music_file_id=data["music_file_id"],
                    status=UploadStatus(data["status"]),
                    started_at=data["started_at"],
                    completed_at=data["completed_at"],
                    error=data["error"],
                    attempts=data["attempts"],
                    ytm_entity_id=data.get("ytm_upload_id"),
                    music_file=MusicFile(**mf_data)
                )

    async def get_sync_history(self, limit: int = 100) -> list[SyncJob]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT sj.*, mf.path, mf.filename, mf.artist, mf.album, mf.title, mf.duration, mf.format, mf.file_size, mf.modified_time,
                       m.ytm_upload_id
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                LEFT JOIN matches m ON mf.id = m.music_file_id
                ORDER BY sj.id DESC LIMIT ?
                """,
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    data = dict(row)
                    mf_data = {k: data[k] for k in ["path", "filename", "artist", "album", "title", "duration", "format", "file_size", "modified_time"]}
                    mf_data["id"] = data["music_file_id"]
                    results.append(SyncJob(
                        id=data["id"],
                        music_file_id=data["music_file_id"],
                        status=UploadStatus(data["status"]),
                        started_at=data["started_at"],
                        completed_at=data["completed_at"],
                        error=data["error"],
                        attempts=data["attempts"],
                        ytm_entity_id=data.get("ytm_upload_id"),
                        music_file=MusicFile(**mf_data)
                    ))
                return results

    # Stats
    async def get_dashboard_counts(self) -> dict:
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM music_files") as c:
                local_count = (await c.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM ytm_uploads") as c:
                ytm_count = (await c.fetchone())[0]

            async with db.execute(
                """
                SELECT COUNT(*) FROM music_files mf
                LEFT JOIN matches m ON mf.id = m.music_file_id
                LEFT JOIN (
                    SELECT music_file_id, status FROM sync_jobs s1
                    WHERE id = (SELECT MAX(id) FROM sync_jobs s2 WHERE s2.music_file_id = s1.music_file_id)
                ) sj ON mf.id = sj.music_file_id
                WHERE m.id IS NULL AND (sj.status IS NULL OR sj.status NOT IN ('uploaded', 'verified', 'uploading', 'queued'))
                """
            ) as c:
                missing_count = (await c.fetchone())[0]

            async with db.execute("SELECT COUNT(DISTINCT music_file_id) FROM matches") as c:
                uploaded_count = (await c.fetchone())[0]

            async with db.execute(
                """
                SELECT COUNT(*) FROM sync_jobs s1
                WHERE s1.status = 'failed' 
                AND id = (SELECT MAX(id) FROM sync_jobs s2 WHERE s2.music_file_id = s1.music_file_id)
                """
            ) as c:
                failed_count = (await c.fetchone())[0]

            async with db.execute("SELECT COUNT(*) FROM sync_jobs WHERE status IN ('queued', 'uploading', 'verifying')") as c:
                in_queue_count = (await c.fetchone())[0]

            return {
                "local_songs_count": local_count,
                "ytm_uploads_count": ytm_count,
                "missing_count": missing_count,
                "uploaded_count": uploaded_count,
                "failed_count": failed_count,
                "in_queue_count": in_queue_count,
            }

    async def backup_database(self, dest_dir: Optional[Path] = None) -> str:
        import shutil
        b_dir = dest_dir or (self.db_path.parent / "backups")
        b_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_file = b_dir / f"ytm_sync_backup_{timestamp}.db"
        
        async with self.get_connection() as db:
            await db.execute("PRAGMA wal_checkpoint(FULL);")
        
        shutil.copy2(self.db_path, backup_file)
        return str(backup_file)

    async def get_folder_song_counts(self, folder_path: str) -> dict:
        folder_prefix = folder_path.rstrip("/") + "/%"
        exact_match = folder_path.rstrip("/")
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            # Total songs under this folder
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM music_files WHERE path LIKE ? OR path = ?",
                (folder_prefix, exact_match)
            ) as cursor:
                row = await cursor.fetchone()
                total = row["cnt"] if row else 0

            # Unmapped songs (songs not yet matched/uploaded)
            async with db.execute(
                """
                SELECT COUNT(*) as cnt FROM music_files mf
                LEFT JOIN matches m ON mf.id = m.music_file_id
                WHERE (mf.path LIKE ? OR mf.path = ?) AND m.id IS NULL
                """,
                (folder_prefix, exact_match)
            ) as cursor:
                row = await cursor.fetchone()
                unmapped = row["cnt"] if row else 0

            return {"total": total, "unmapped": unmapped}

db = Database()
