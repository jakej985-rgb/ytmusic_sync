import aiosqlite
import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any, Union
from .config import settings
from .models import MusicFile, YtmUpload, MatchRecord, SyncJob, UploadStatus, MatchType, ReplicatedPlaylist, ReplicatedPlaylistEvent, SourcePlaylistSnapshot, SourcePlaylistTrackSnapshot

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
    verification_status TEXT DEFAULT 'UNVERIFIED',
    verification_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ytm_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT UNIQUE NOT NULL,
    video_id TEXT,
    upload_video_id TEXT,
    upload_url TEXT,
    source_type TEXT DEFAULT 'ytm_upload',
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
    sync_decision TEXT DEFAULT 'REVIEW',
    decision_reason TEXT,
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
    source_type TEXT DEFAULT 'ytm_upload',
    source_id TEXT,
    source_url TEXT,
    expected_duration REAL,
    downloaded_source_id TEXT,
    verified BOOLEAN DEFAULT 0,
    verification_status TEXT DEFAULT 'PENDING',
    verification_reason TEXT,
    original_file_hash TEXT,
    downloaded_file_hash TEXT,
    replacement_allowed BOOLEAN DEFAULT 0,
    FOREIGN KEY(music_file_id) REFERENCES music_files(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS needs_help_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    artist TEXT,
    album TEXT,
    thumbnail TEXT,
    source TEXT,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS file_replacements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    original_sha256 TEXT NOT NULL,
    original_size INTEGER NOT NULL,
    original_mtime REAL NOT NULL,
    replacement_source_id TEXT NOT NULL,
    replacement_path TEXT,
    backup_path TEXT,
    replacement_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS replicated_playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_playlist_id TEXT NOT NULL,
    source_playlist_name TEXT NOT NULL,
    destination_playlist_id TEXT NOT NULL,
    destination_playlist_name TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    sync_interval_seconds INTEGER DEFAULT 300,
    last_source_revision TEXT,
    last_sync_at TIMESTAMP,
    last_sync_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_playlist_id, destination_playlist_id)
);

CREATE TABLE IF NOT EXISTS replicated_playlist_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    replicated_playlist_id INTEGER NOT NULL,
    source_track_id TEXT,
    source_video_id TEXT,
    locker_upload_id TEXT,
    action TEXT NOT NULL,
    reason TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(replicated_playlist_id) REFERENCES replicated_playlists(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS replicated_playlist_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    replicated_playlist_id INTEGER NOT NULL,
    revision TEXT NOT NULL,
    track_count INTEGER NOT NULL,
    tracks_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(replicated_playlist_id) REFERENCES replicated_playlists(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_music_files_path ON music_files(path);
CREATE INDEX IF NOT EXISTS idx_music_files_artist_title ON music_files(artist, title);
CREATE INDEX IF NOT EXISTS idx_ytm_uploads_entity ON ytm_uploads(entity_id);
CREATE INDEX IF NOT EXISTS idx_matches_music_file ON matches(music_file_id);
CREATE INDEX IF NOT EXISTS idx_sync_jobs_status ON sync_jobs(status);
CREATE INDEX IF NOT EXISTS idx_needs_help_video ON needs_help_tracks(video_id);
CREATE INDEX IF NOT EXISTS idx_file_replacements_path ON file_replacements(original_path);
CREATE INDEX IF NOT EXISTS idx_replicated_playlists_source ON replicated_playlists(source_playlist_id);
CREATE INDEX IF NOT EXISTS idx_replicated_playlist_events_rep_id ON replicated_playlist_events(replicated_playlist_id);
CREATE INDEX IF NOT EXISTS idx_replicated_playlist_snapshots_rep_id ON replicated_playlist_snapshots(replicated_playlist_id);
"""

class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path

    @asynccontextmanager
    async def get_connection(self):
        async with aiosqlite.connect(self.db_path, timeout=60.0) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute("PRAGMA busy_timeout = 60000;")
            yield conn

    async def init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self.get_connection() as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")
            await db.executescript(CREATE_TABLES_SQL)
            
            # Ensure upload identity columns exist on existing databases
            async with db.execute("PRAGMA table_info(ytm_uploads)") as cursor:
                cols = [row["name"] for row in await cursor.fetchall()]
                if "upload_video_id" not in cols:
                    await db.execute("ALTER TABLE ytm_uploads ADD COLUMN upload_video_id TEXT")
                if "upload_url" not in cols:
                    await db.execute("ALTER TABLE ytm_uploads ADD COLUMN upload_url TEXT")
                if "source_type" not in cols:
                    await db.execute("ALTER TABLE ytm_uploads ADD COLUMN source_type TEXT DEFAULT 'ytm_upload'")

            # Ensure sync_decision columns exist on existing databases
            async with db.execute("PRAGMA table_info(matches)") as cursor:
                m_cols = [row["name"] for row in await cursor.fetchall()]
                if "sync_decision" not in m_cols:
                    await db.execute("ALTER TABLE matches ADD COLUMN sync_decision TEXT DEFAULT 'REVIEW'")
                if "decision_reason" not in m_cols:
                    await db.execute("ALTER TABLE matches ADD COLUMN decision_reason TEXT")

            # Ensure Phase 10 source/integrity columns exist on sync_jobs
            async with db.execute("PRAGMA table_info(sync_jobs)") as cursor:
                sj_cols = [row["name"] for row in await cursor.fetchall()]
                columns_to_add = [
                    ("source_type", "TEXT DEFAULT 'ytm_upload'"),
                    ("source_id", "TEXT"),
                    ("source_url", "TEXT"),
                    ("expected_duration", "REAL"),
                    ("downloaded_source_id", "TEXT"),
                    ("verified", "BOOLEAN DEFAULT 0"),
                    ("verification_status", "TEXT DEFAULT 'PENDING'"),
                    ("verification_reason", "TEXT"),
                    ("original_file_hash", "TEXT"),
                    ("downloaded_file_hash", "TEXT"),
                    ("replacement_allowed", "BOOLEAN DEFAULT 0"),
                ]
                for col_name, col_def in columns_to_add:
                    if col_name not in sj_cols:
                        await db.execute(f"ALTER TABLE sync_jobs ADD COLUMN {col_name} {col_def}")

            # Ensure verification columns exist on music_files
            async with db.execute("PRAGMA table_info(music_files)") as cursor:
                mf_cols = [row["name"] for row in await cursor.fetchall()]
                if "verification_status" not in mf_cols:
                    await db.execute("ALTER TABLE music_files ADD COLUMN verification_status TEXT DEFAULT 'UNVERIFIED'")
                if "verification_reason" not in mf_cols:
                    await db.execute("ALTER TABLE music_files ADD COLUMN verification_reason TEXT")

            await db.commit()
        await self.reconcile_stuck_sync_jobs()

    async def reconcile_stuck_sync_jobs(self, max_attempts: int = 3) -> dict[str, int]:
        """Reconcile jobs left in 'uploading' or 'verifying' from a crash or SIGTERM.
        Resets jobs to 'queued' if attempts < max_attempts, or marks them 'failed' if attempts >= max_attempts.
        """
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE sync_jobs
                SET status = 'queued', error = 'Re-queued after server restart'
                WHERE status IN ('uploading', 'verifying') AND attempts < ?
                """,
                (max_attempts,)
            )
            requeued_count = cursor.rowcount

            cursor = await db.execute(
                """
                UPDATE sync_jobs
                SET status = 'failed', error = 'Exceeded retry limit (interrupted)'
                WHERE status IN ('uploading', 'verifying') AND attempts >= ?
                """,
                (max_attempts,)
            )
            failed_count = cursor.rowcount
            await db.commit()
            return {"requeued": requeued_count, "failed": failed_count}

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
            query = """
                SELECT 
                    mf.*,
                    (
                        CASE 
                            WHEN m.id IS NOT NULL THEN 'verified'
                            WHEN sj.status IS NOT NULL THEN sj.status
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
                WHERE mf.id = ?
            """
            async with db.execute(query, (file_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
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
                return item

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

        for attempt in range(5):
            try:
                async with self.get_connection() as db:
                    async with db.execute("SELECT duration FROM music_files WHERE id = ?", (file_id,)) as cursor:
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
                    break
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    await asyncio.sleep(0.3 * (attempt + 1))
                else:
                    raise

        return await self.get_music_file_by_id(file_id)

    async def get_all_local_songs(self) -> list[dict]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT path, title, artist, album, duration FROM music_files") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    # YTM Uploads
    async def upsert_ytm_upload(self, upload_info: Union[dict, Any]):
        if hasattr(upload_info, "model_dump"):
            upload_info = upload_info.model_dump()
        elif hasattr(upload_info, "dict"):
            upload_info = upload_info.dict()

        now = datetime.now(timezone.utc).isoformat()
        vid = upload_info.get("video_id") or upload_info.get("upload_video_id")
        upload_url = upload_info.get("upload_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
        source_type = upload_info.get("source_type") or "ytm_upload"

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO ytm_uploads (
                    entity_id, video_id, upload_video_id, upload_url, source_type,
                    title, artist, album, duration, like_status, thumbnail, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    video_id=excluded.video_id,
                    upload_video_id=excluded.upload_video_id,
                    upload_url=excluded.upload_url,
                    source_type=excluded.source_type,
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
                    vid,
                    vid,
                    upload_url,
                    source_type,
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

    async def get_ytm_upload_by_entity_id(self, entity_id: str) -> Optional[YtmUpload]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM ytm_uploads WHERE entity_id = ?", (entity_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return YtmUpload(**dict(row))
                return None

    async def get_ytm_upload_by_video_id(self, video_id: str) -> Optional[YtmUpload]:
        """Lookup an upload by its YouTube video_id."""
        if not video_id:
            return None
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM ytm_uploads WHERE video_id = ? OR upload_video_id = ? LIMIT 1", (video_id, video_id)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return YtmUpload(**dict(row))
                return None

    async def find_ytm_upload_by_title_artist(self, title: str, artist: Optional[str] = None) -> Optional[YtmUpload]:
        """Find an existing upload matching normalized title and artist."""
        from .normalizer import normalize_text
        clean_title = normalize_text(title)
        clean_artist = normalize_text(artist) if artist else ""
        if not clean_title:
            return None
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            # Search by title prefix for fast indexing
            async with db.execute("SELECT * FROM ytm_uploads WHERE title LIKE ? LIMIT 50", (f"%{title[:20]}%",)) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    u = YtmUpload(**dict(r))
                    u_title = normalize_text(u.title)
                    u_artist = normalize_text(u.artist) if u.artist else ""
                    if u_title == clean_title:
                        if not clean_artist or not u_artist or u_artist == clean_artist:
                            return u
        return None

    async def delete_ytm_upload_record(self, entity_id: str):
        async with self.get_connection() as db:
            async with db.execute("SELECT video_id FROM ytm_uploads WHERE entity_id = ?", (entity_id,)) as cur:
                row = await cur.fetchone()
                vid = row[0] if row else None
            await db.execute("DELETE FROM matches WHERE ytm_upload_id = ?", (entity_id,))
            await db.execute("DELETE FROM ytm_uploads WHERE entity_id = ?", (entity_id,))
            if vid:
                await db.execute("DELETE FROM ytm_uploads WHERE entity_id = ?", (f"up_{vid}",))
            await db.commit()

    async def prune_deleted_ytm_uploads(self, active_entity_ids: set[str], excluded_entity_ids: set[str]):
        """Prune uploads from the local database that no longer exist on YouTube Music."""
        async with self.get_connection() as db:
            # 1. Delete all excluded/blacklisted entity IDs
            for eid in excluded_entity_ids:
                await db.execute("DELETE FROM matches WHERE ytm_upload_id = ?", (eid,))
                await db.execute("DELETE FROM ytm_uploads WHERE entity_id = ?", (eid,))

            # 2. Prune local uploads that are no longer in active_entity_ids (including artificial up_% IDs)
            async with db.execute("SELECT entity_id FROM ytm_uploads") as cursor:
                rows = await cursor.fetchall()
                local_eids = {r[0] for r in rows}

            stale_eids = local_eids - active_entity_ids
            for s_eid in stale_eids:
                await db.execute("DELETE FROM matches WHERE ytm_upload_id = ?", (s_eid,))
                await db.execute("DELETE FROM ytm_uploads WHERE entity_id = ?", (s_eid,))

            await db.commit()

    async def update_ytm_upload(
        self,
        entity_id: str,
        title: str,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        thumbnail: Optional[str] = None
    ):
        """Update metadata for an existing YTM upload in the local database."""
        async with self.get_connection() as db:
            await db.execute(
                """
                UPDATE ytm_uploads
                SET title = ?, artist = ?, album = ?, thumbnail = ?
                WHERE entity_id = ?
                """,
                (title, artist, album, thumbnail, entity_id)
            )
            await db.commit()

    async def get_ytm_uploads_summary(self) -> dict:
        missing_condition = """
        (
            artist IS NULL OR artist = '' OR TRIM(LOWER(artist)) = 'unknown artist' OR TRIM(LOWER(artist)) = 'unknown'
            OR album IS NULL OR album = '' OR TRIM(LOWER(album)) = 'unknown album' OR TRIM(LOWER(album)) = 'unknown'
            OR thumbnail IS NULL OR thumbnail = ''
            OR title IS NULL OR title = ''
            OR title LIKE '%.mp3' OR title LIKE '%.flac' OR title LIKE '%.m4a' OR title LIKE '%.wav' OR title LIKE '%.opus' OR title LIKE '%.webm'
            OR title LIKE 'y2mate%' OR title LIKE 'snapsave%' OR title LIKE 'tuberipper%'
        )
        """

        skits_condition = """
        (
            (duration IS NOT NULL AND duration > 0 AND duration < 60)
            OR (duration IS NOT NULL AND duration < 90 AND (
                LOWER(title) LIKE '%skit%' 
                OR LOWER(title) LIKE '%interlude%' 
                OR LOWER(title) LIKE '%intro%'
                OR LOWER(title) LIKE '%outro%'
            ))
        )
        """

        duplicates_condition = """
        (
            (
                LOWER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title, '.mp3', ''), '.flac', ''), '.m4a', ''), '.wav', ''), '.opus', ''), '.webm', ''))),
                COALESCE(NULLIF(TRIM(LOWER(artist)), 'unknown artist'), '')
            ) IN (
                SELECT 
                    LOWER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title, '.mp3', ''), '.flac', ''), '.m4a', ''), '.wav', ''), '.opus', ''), '.webm', ''))),
                    COALESCE(NULLIF(TRIM(LOWER(artist)), 'unknown artist'), '')
                FROM ytm_uploads
                WHERE title IS NOT NULL AND TRIM(title) != ''
                GROUP BY 
                    LOWER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title, '.mp3', ''), '.flac', ''), '.m4a', ''), '.wav', ''), '.opus', ''), '.webm', ''))),
                    COALESCE(NULLIF(TRIM(LOWER(artist)), 'unknown artist'), '')
                HAVING COUNT(*) > 1
            )
            OR
            (video_id IS NOT NULL AND TRIM(video_id) != '' AND video_id IN (
                SELECT video_id
                FROM ytm_uploads
                WHERE video_id IS NOT NULL AND TRIM(video_id) != ''
                GROUP BY video_id
                HAVING COUNT(*) > 1
            ))
        )
        """

        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN {missing_condition} THEN 1 END) as missing_metadata,
                    COUNT(CASE WHEN {skits_condition} THEN 1 END) as skits,
                    COUNT(CASE WHEN {duplicates_condition} THEN 1 END) as duplicates
                FROM ytm_uploads
                """
            ) as cursor:
                row = await cursor.fetchone()
                total = row["total"] if row else 0
                missing = row["missing_metadata"] if row else 0
                skits = row["skits"] if row else 0
                dups_count = row["duplicates"] if row else 0

            return {
                "total": total,
                "missing_metadata": missing,
                "duplicates": dups_count,
                "skits": skits,
                "proper": max(0, total - missing)
            }

    async def get_ytm_uploads(
        self,
        filter_type: str = "all",
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 50
    ) -> dict:
        where_clauses = []
        params = []

        missing_condition = """
        (
            artist IS NULL OR artist = '' OR TRIM(LOWER(artist)) = 'unknown artist' OR TRIM(LOWER(artist)) = 'unknown'
            OR album IS NULL OR album = '' OR TRIM(LOWER(album)) = 'unknown album' OR TRIM(LOWER(album)) = 'unknown'
            OR thumbnail IS NULL OR thumbnail = ''
            OR title IS NULL OR title = ''
            OR title LIKE '%.mp3' OR title LIKE '%.flac' OR title LIKE '%.m4a' OR title LIKE '%.wav' OR title LIKE '%.opus' OR title LIKE '%.webm'
            OR title LIKE 'y2mate%' OR title LIKE 'snapsave%' OR title LIKE 'tuberipper%'
        )
        """

        skits_condition = """
        (
            (duration IS NOT NULL AND duration > 0 AND duration < 60)
            OR (duration IS NOT NULL AND duration < 90 AND (
                LOWER(title) LIKE '%skit%' 
                OR LOWER(title) LIKE '%interlude%' 
                OR LOWER(title) LIKE '%intro%'
                OR LOWER(title) LIKE '%outro%'
            ))
        )
        """

        duplicates_condition = """
        (
            (
                LOWER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title, '.mp3', ''), '.flac', ''), '.m4a', ''), '.wav', ''), '.opus', ''), '.webm', ''))),
                COALESCE(NULLIF(TRIM(LOWER(artist)), 'unknown artist'), '')
            ) IN (
                SELECT 
                    LOWER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title, '.mp3', ''), '.flac', ''), '.m4a', ''), '.wav', ''), '.opus', ''), '.webm', ''))),
                    COALESCE(NULLIF(TRIM(LOWER(artist)), 'unknown artist'), '')
                FROM ytm_uploads
                WHERE title IS NOT NULL AND TRIM(title) != ''
                GROUP BY 
                    LOWER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(title, '.mp3', ''), '.flac', ''), '.m4a', ''), '.wav', ''), '.opus', ''), '.webm', ''))),
                    COALESCE(NULLIF(TRIM(LOWER(artist)), 'unknown artist'), '')
                HAVING COUNT(*) > 1
            )
            OR
            (video_id IS NOT NULL AND TRIM(video_id) != '' AND video_id IN (
                SELECT video_id
                FROM ytm_uploads
                WHERE video_id IS NOT NULL AND TRIM(video_id) != ''
                GROUP BY video_id
                HAVING COUNT(*) > 1
            ))
        )
        """

        order_by = "first_seen DESC, title ASC"
        if filter_type == "missing_metadata":
            where_clauses.append(missing_condition)
        elif filter_type == "duplicates":
            where_clauses.append(duplicates_condition)
            order_by = "LOWER(TRIM(REPLACE(title, '.mp3', ''))) ASC, duration ASC, first_seen DESC"
        elif filter_type == "skits":
            where_clauses.append(skits_condition)
            order_by = "duration ASC, title ASC"
        elif filter_type == "proper":
            where_clauses.append(f"NOT {missing_condition}")

        if search:
            where_clauses.append("(title LIKE ? OR artist LIKE ? OR album LIKE ?)")
            s_param = f"%{search}%"
            params.extend([s_param, s_param, s_param])

        where_sql = ""
        if where_clauses:
            where_sql = "WHERE " + " AND ".join(where_clauses)

        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            count_query = f"SELECT COUNT(*) as cnt FROM ytm_uploads {where_sql}"
            async with db.execute(count_query, params) as cursor:
                row = await cursor.fetchone()
                total = row["cnt"] if row else 0

            offset = (page - 1) * page_size
            data_query = f"""
                SELECT * FROM ytm_uploads
                {where_sql}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """
            data_params = list(params) + [page_size, offset]
            async with db.execute(data_query, data_params) as cursor:
                rows = await cursor.fetchall()
                items = [YtmUpload(**dict(r)) for r in rows]

            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total > 0 else 1
            }

    # Matches
    async def save_match(
        self,
        file_id: int,
        ytm_upload_id: str,
        match_type: MatchType | str,
        score: float,
        sync_decision: Optional[str] = None,
        decision_reason: Optional[str] = None
    ):
        m_val = match_type.value if isinstance(match_type, MatchType) else str(match_type)
        decision_val = sync_decision or "REVIEW"
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO matches (music_file_id, ytm_upload_id, match_type, match_score, sync_decision, decision_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(music_file_id, ytm_upload_id) DO UPDATE SET
                    match_type=excluded.match_type,
                    match_score=excluded.match_score,
                    sync_decision=excluded.sync_decision,
                    decision_reason=excluded.decision_reason
                """,
                (file_id, ytm_upload_id, m_val, score, decision_val, decision_reason)
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

    async def get_local_filepath_for_upload(self, entity_id: str) -> Optional[str]:
        """Check if an upload entity is already matched to a local music file."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT mf.path 
                FROM matches m 
                JOIN music_files mf ON m.music_file_id = mf.id 
                WHERE m.ytm_upload_id = ? AND mf.path IS NOT NULL
                LIMIT 1
                """,
                (entity_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    # Sync Jobs & Queue
    def _row_to_sync_job(self, data: dict) -> SyncJob:
        mf_fields = ["path", "filename", "artist", "album", "title", "duration", "format", "file_size", "modified_time"]
        mf_data = {k: data[k] for k in mf_fields if k in data}
        mf_data["id"] = data.get("music_file_id")

        for k in [
            "source_type", "source_id", "source_url", "expected_duration",
            "downloaded_source_id", "verified", "verification_status",
            "verification_reason", "downloaded_file_hash", "replacement_allowed"
        ]:
            if k in data:
                mf_data[k] = data[k]

        status_val = data.get("status", "queued")
        try:
            status_enum = UploadStatus(status_val)
        except ValueError:
            status_enum = status_val

        return SyncJob(
            id=data.get("id"),
            music_file_id=data.get("music_file_id"),
            status=status_enum,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            attempts=data.get("attempts", 0),
            ytm_entity_id=data.get("ytm_upload_id"),
            source_type=data.get("source_type", "ytm_upload"),
            source_id=data.get("source_id"),
            source_url=data.get("source_url"),
            expected_duration=data.get("expected_duration"),
            downloaded_source_id=data.get("downloaded_source_id"),
            verified=bool(data.get("verified", 0)),
            verification_status=data.get("verification_status", "PENDING"),
            verification_reason=data.get("verification_reason"),
            original_file_hash=data.get("original_file_hash"),
            downloaded_file_hash=data.get("downloaded_file_hash"),
            replacement_allowed=bool(data.get("replacement_allowed", 0)),
            music_file=MusicFile(**mf_data)
        )

    async def create_sync_job(
        self,
        music_file_id: int,
        source_type: str = "ytm_upload",
        source_id: Optional[str] = None,
        source_url: Optional[str] = None,
        expected_duration: Optional[float] = None,
        original_file_hash: Optional[str] = None,
        replacement_allowed: bool = False,
        verification_status: str = "PENDING"
    ) -> int:
        async with self.get_connection() as db:
            async with db.execute(
                """
                INSERT INTO sync_jobs (
                    music_file_id, status, attempts,
                    source_type, source_id, source_url,
                    expected_duration, original_file_hash, replacement_allowed,
                    verification_status
                ) VALUES (?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    music_file_id, source_type, source_id, source_url,
                    expected_duration, original_file_hash, 1 if replacement_allowed else 0,
                    verification_status
                )
            ) as cursor:
                row = await cursor.fetchone()
                await db.commit()
                return row[0]

    async def update_sync_job(
        self,
        job_id: int,
        status: Union[UploadStatus, str],
        error: Optional[str] = None,
        increment_attempts: bool = False,
        downloaded_source_id: Optional[str] = None,
        verified: Optional[bool] = None,
        verification_status: Optional[str] = None,
        verification_reason: Optional[str] = None,
        downloaded_file_hash: Optional[str] = None
    ):
        now = datetime.now(timezone.utc).isoformat()
        status_val = status.value if hasattr(status, "value") else str(status)

        updates = ["status = ?", "error = ?"]
        params = [status_val, error]

        if increment_attempts:
            updates.append("attempts = attempts + 1")

        if status_val in ("uploading", "UPLOADING", "DOWNLOADING", "VERIFYING"):
            updates.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        elif status_val in ("verified", "VERIFIED", "failed", "FAILED", "BLOCKED"):
            updates.append("completed_at = ?")
            params.append(now)

        if downloaded_source_id is not None:
            updates.append("downloaded_source_id = ?")
            params.append(downloaded_source_id)
        if verified is not None:
            updates.append("verified = ?")
            params.append(1 if verified else 0)
        if verification_status is not None:
            updates.append("verification_status = ?")
            params.append(verification_status)
        if verification_reason is not None:
            updates.append("verification_reason = ?")
            params.append(verification_reason)
        if downloaded_file_hash is not None:
            updates.append("downloaded_file_hash = ?")
            params.append(downloaded_file_hash)

        params.append(job_id)
        query = f"UPDATE sync_jobs SET {', '.join(updates)} WHERE id = ?"

        async with self.get_connection() as db:
            await db.execute(query, tuple(params))
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
                return self._row_to_sync_job(dict(row))

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
                return self._row_to_sync_job(dict(row))

    async def retry_blocked_sync_job(self, job_id: int) -> Optional[SyncJob]:
        """
        Manually retry a BLOCKED sync job (Blocker 3).
        Safety Invariants:
        - Job must currently be in BLOCKED status.
        - Automatic workers ignore BLOCKED jobs.
        - Manual retry strictly locks to the EXACT same original source_type and source_id.
        - Resets status to 'queued' with attempts=0.
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sync_jobs WHERE id = ?", (job_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    raise FileNotFoundError(f"Sync job {job_id} not found")
                st = row["status"]
                v_st = row["verification_status"] if "verification_status" in row.keys() else None
                if st != "BLOCKED" and v_st != "BLOCKED":
                    raise ValueError(f"Job {job_id} is not BLOCKED (current status: {st})")

                orig_source_id = row["source_id"] if "source_id" in row.keys() else None

            await db.execute(
                """
                UPDATE sync_jobs
                SET status = 'queued',
                    verification_status = 'PENDING',
                    verification_reason = 'Manual retry requested by user for original source ID ' || COALESCE(source_id, ''),
                    attempts = 0,
                    error = NULL,
                    completed_at = NULL
                WHERE id = ?
                """,
                (job_id,)
            )
            await db.commit()
        return await self.get_sync_job_by_id(job_id)

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
                return [self._row_to_sync_job(dict(row)) for row in rows]

    async def get_active_or_queued_sync_jobs(self, limit: int = 100) -> list[SyncJob]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT sj.*, mf.path, mf.filename, mf.artist, mf.album, mf.title, mf.duration, mf.format, mf.file_size, mf.modified_time,
                       m.ytm_upload_id
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                LEFT JOIN matches m ON mf.id = m.music_file_id
                WHERE sj.status IN ('queued', 'uploading', 'verifying', 'PENDING', 'DOWNLOADING', 'VERIFYING')
                ORDER BY CASE WHEN sj.status IN ('uploading', 'DOWNLOADING', 'VERIFYING') THEN 0 ELSE 1 END, sj.id ASC LIMIT ?
                """,
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [self._row_to_sync_job(dict(row)) for row in rows]

    async def clear_queued_sync_jobs(self):
        async with self.get_connection() as db:
            await db.execute("DELETE FROM sync_jobs WHERE status = 'queued'")
            await db.commit()

    async def upsert_needs_help_track(
        self,
        video_id: str,
        title: str,
        artist: Optional[str] = None,
        album: Optional[str] = None,
        thumbnail: Optional[str] = None,
        source: Optional[str] = None,
        reason: Optional[str] = None
    ):
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO needs_help_tracks (video_id, title, artist, album, thumbnail, source, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(video_id) DO UPDATE SET
                    title = excluded.title,
                    artist = excluded.artist,
                    album = excluded.album,
                    thumbnail = excluded.thumbnail,
                    source = excluded.source,
                    reason = excluded.reason,
                    created_at = CURRENT_TIMESTAMP
                """,
                (video_id, title, artist, album, thumbnail, source, reason)
            )
            await db.commit()

    async def get_needs_help_tracks(self, limit: int = 200) -> list[dict]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM needs_help_tracks ORDER BY id DESC LIMIT ?",
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def delete_needs_help_track(self, video_id: str):
        async with self.get_connection() as db:
            await db.execute("DELETE FROM needs_help_tracks WHERE video_id = ?", (video_id,))
            await db.commit()

    async def count_needs_help_tracks(self) -> int:
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM needs_help_tracks") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

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

    async def record_file_replacement(
        self,
        original_path: str,
        original_sha256: str,
        original_size: int,
        original_mtime: float,
        replacement_source_id: str,
        replacement_path: Optional[str] = None,
        backup_path: Optional[str] = None
    ) -> int:
        """Record an audit trail entry for a replaced local audio file."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO file_replacements (
                    original_path, original_sha256, original_size, original_mtime,
                    replacement_source_id, replacement_path, backup_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    original_path, original_sha256, original_size, original_mtime,
                    replacement_source_id, replacement_path, backup_path
                )
            )
            await db.commit()
            return cursor.lastrowid

    def record_file_replacement_sync(
        self,
        original_path: str,
        original_sha256: str,
        original_size: int,
        original_mtime: float,
        replacement_source_id: str,
        replacement_path: Optional[str] = None,
        backup_path: Optional[str] = None
    ):
        """Synchronously record an audit trail entry using direct sqlite3 connection."""
        import sqlite3
        with sqlite3.connect(str(self.db_path), timeout=30.0) as conn:
            conn.execute(
                """
                INSERT INTO file_replacements (
                    original_path, original_sha256, original_size, original_mtime,
                    replacement_source_id, replacement_path, backup_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    original_path, original_sha256, original_size, original_mtime,
                    replacement_source_id, replacement_path, backup_path
                )
            )
            conn.commit()

    async def get_file_replacements(self, limit: int = 100) -> list[dict]:
        """Fetch historical audit log of replaced local files."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM file_replacements ORDER BY replacement_timestamp DESC LIMIT ?",
                (limit,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_replicated_playlists(self, enabled_only: bool = False) -> list[ReplicatedPlaylist]:
        """Fetch all configured replicated playlist watchers."""
        query = "SELECT * FROM replicated_playlists"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id ASC"
        async with self.get_connection() as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [ReplicatedPlaylist(**dict(row)) for row in rows]

    async def get_replicated_playlist(self, replicated_id: int) -> Optional[ReplicatedPlaylist]:
        """Fetch a specific replicated playlist watcher by ID."""
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM replicated_playlists WHERE id = ?", (replicated_id,)) as cursor:
                row = await cursor.fetchone()
                return ReplicatedPlaylist(**dict(row)) if row else None

    async def get_replicated_playlist_by_source_id(self, source_playlist_id: str) -> Optional[ReplicatedPlaylist]:
        """Fetch a replicated playlist watcher by its source YouTube Music playlist ID."""
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM replicated_playlists WHERE source_playlist_id = ?", (source_playlist_id,)) as cursor:
                row = await cursor.fetchone()
                return ReplicatedPlaylist(**dict(row)) if row else None

    async def create_replicated_playlist(
        self,
        source_playlist_id: str,
        source_playlist_name: str,
        destination_playlist_id: str,
        destination_playlist_name: str,
        enabled: bool = True,
        sync_interval_seconds: int = 300
    ) -> int:
        """Create a new replicated playlist configuration."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO replicated_playlists (
                    source_playlist_id, source_playlist_name,
                    destination_playlist_id, destination_playlist_name,
                    enabled, sync_interval_seconds
                ) VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    source_playlist_id, source_playlist_name,
                    destination_playlist_id, destination_playlist_name,
                    1 if enabled else 0, sync_interval_seconds
                )
            )
            row = await cursor.fetchone()
            await db.commit()
            return row["id"]

    async def update_replicated_playlist(self, replicated_id: int, **kwargs) -> Optional[ReplicatedPlaylist]:
        """Update fields of a replicated playlist configuration."""
        if not kwargs:
            return await self.get_replicated_playlist(replicated_id)

        set_clauses = []
        values = []
        for k, v in kwargs.items():
            if k in ("source_playlist_name", "destination_playlist_name", "last_source_revision", "last_sync_status", "last_sync_at"):
                set_clauses.append(f"{k} = ?")
                values.append(v)
            elif k in ("enabled",):
                set_clauses.append(f"{k} = ?")
                values.append(1 if v else 0)
            elif k in ("sync_interval_seconds",):
                set_clauses.append(f"{k} = ?")
                values.append(int(v))

        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        values.append(replicated_id)

        async with self.get_connection() as db:
            await db.execute(
                f"UPDATE replicated_playlists SET {', '.join(set_clauses)} WHERE id = ?",
                tuple(values)
            )
            await db.commit()
        return await self.get_replicated_playlist(replicated_id)

    async def delete_replicated_playlist(self, replicated_id: int) -> bool:
        """Delete a replicated playlist configuration."""
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM replicated_playlists WHERE id = ?", (replicated_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def record_replicated_playlist_event(
        self,
        replicated_playlist_id: int,
        action: str,
        source_track_id: Optional[str] = None,
        source_video_id: Optional[str] = None,
        locker_upload_id: Optional[str] = None,
        reason: Optional[str] = None
    ):
        """Record an audit event for playlist reconciliation (ADD, REMOVE, MOVE, NOOP, EXCLUDE)."""
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO replicated_playlist_events (
                    replicated_playlist_id, source_track_id, source_video_id,
                    locker_upload_id, action, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (replicated_playlist_id, source_track_id, source_video_id, locker_upload_id, action, reason)
            )
            await db.commit()

    async def get_replicated_playlist_events(self, replicated_playlist_id: int, limit: int = 100) -> list[dict]:
        """Fetch audit trail events for a replicated playlist."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM replicated_playlist_events WHERE replicated_playlist_id = ? ORDER BY timestamp DESC LIMIT ?",
                (replicated_playlist_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def save_replicated_playlist_snapshot(
        self,
        replicated_playlist_id: int,
        revision: str,
        tracks: list[dict]
    ) -> int:
        """Store source playlist snapshot (Section 4 of plan) to detect changes."""
        tracks_json = json.dumps(tracks)
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                INSERT INTO replicated_playlist_snapshots (
                    replicated_playlist_id, revision, track_count, tracks_json
                ) VALUES (?, ?, ?, ?)
                RETURNING id
                """,
                (replicated_playlist_id, revision, len(tracks), tracks_json)
            )
            row = await cursor.fetchone()
            await db.commit()
            return row["id"]

    async def get_latest_replicated_playlist_snapshot(self, replicated_playlist_id: int) -> Optional[dict]:
        """Get the most recent source playlist snapshot for a replica."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT * FROM replicated_playlist_snapshots
                WHERE replicated_playlist_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (replicated_playlist_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                try:
                    data["tracks"] = json.loads(data.get("tracks_json") or "[]")
                except Exception:
                    data["tracks"] = []
                return data

db = Database()

