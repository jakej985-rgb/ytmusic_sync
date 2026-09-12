import aiosqlite
import asyncio
import json
import logging
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Any, Union
from .config import settings, AUTH_DIR, DEFAULT_DATA_DIR, USERS_DIR
from .models import (
    MusicFile, YtmUpload, MatchRecord, SyncJob, UploadStatus, MatchType,
    ReplicatedPlaylist, ReplicatedPlaylistEvent, SourcePlaylistSnapshot, SourcePlaylistTrackSnapshot,
    User, UserCreate, UserUpdate, UserRole, AppSession, YouTubeMusicAccount, YtmAccountStatus, UserSettings, UserSettingsUpdate,
    Family, FamilyMember, FamilyInvitation, FamilyRole, FamilyMemberStatus
)
from .security import hash_password, verify_password, validate_user_id, get_user_subpath, encrypt_auth_data

logger = logging.getLogger("ytm_sync.database")

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
    user_id TEXT,
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
    user_id TEXT,
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
    user_id TEXT,
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
    user_id TEXT,
    source_playlist_id TEXT NOT NULL,
    source_playlist_name TEXT NOT NULL,
    destination_playlist_id TEXT NOT NULL,
    destination_playlist_name TEXT NOT NULL,
    enabled BOOLEAN DEFAULT 1,
    sync_interval_seconds INTEGER DEFAULT 300,
    replica_mode TEXT DEFAULT '1to1_youtube',
    last_source_revision TEXT,
    last_sync_at TIMESTAMP,
    last_sync_status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, source_playlist_id, destination_playlist_id)
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

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'USER',
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_sessions (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    revoked_at TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ytm_accounts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    account_name TEXT,
    account_identifier TEXT,
    auth_reference TEXT,
    encrypted_credentials TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_verified_at TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY(user_id, key),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS families (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS family_members (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'MEMBER',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    show_account_in_family BOOLEAN NOT NULL DEFAULT 1,
    allow_family_uploads BOOLEAN NOT NULL DEFAULT 1,
    allow_family_playlists BOOLEAN NOT NULL DEFAULT 0,
    allow_family_sync BOOLEAN NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(family_id, user_id),
    FOREIGN KEY(family_id) REFERENCES families(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS family_invitations (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    token TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL DEFAULT 'MEMBER',
    created_by_user_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    accepted_at TIMESTAMP,
    accepted_by_user_id TEXT,
    FOREIGN KEY(family_id) REFERENCES families(id) ON DELETE CASCADE,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE CASCADE
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
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_app_sessions_token ON app_sessions(token);
CREATE INDEX IF NOT EXISTS idx_app_sessions_user_id ON app_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_ytm_accounts_user_id ON ytm_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_user_settings_user_id ON user_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_families_owner ON families(owner_user_id);
CREATE INDEX IF NOT EXISTS idx_family_members_user_id ON family_members(user_id);
CREATE INDEX IF NOT EXISTS idx_family_members_family_id ON family_members(family_id);
CREATE INDEX IF NOT EXISTS idx_family_invitations_token ON family_invitations(token);
CREATE INDEX IF NOT EXISTS idx_family_invitations_family_id ON family_invitations(family_id);
"""

class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self._custom_db_path = db_path

    @property
    def db_path(self) -> Path:
        return self._custom_db_path or settings.db_path

    @db_path.setter
    def db_path(self, val: Optional[Path]):
        self._custom_db_path = val

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

            # Ensure user_id ownership columns exist on tables (Phase L)
            tables_needing_user_id = ["replicated_playlists", "ytm_uploads", "sync_jobs", "needs_help_tracks"]
            for tbl in tables_needing_user_id:
                async with db.execute(f"PRAGMA table_info({tbl})") as cursor:
                    t_cols = [row["name"] for row in await cursor.fetchall()]
                    if "user_id" not in t_cols:
                        await db.execute(f"ALTER TABLE {tbl} ADD COLUMN user_id TEXT")

            # Ensure family/multi-account columns exist on sync_jobs (Section 36)
            async with db.execute("PRAGMA table_info(sync_jobs)") as cursor:
                sj_cols = [row["name"] for row in await cursor.fetchall()]
                family_job_cols = [
                    ("family_id", "TEXT"),
                    ("requested_by_user_id", "TEXT"),
                    ("destination_user_id", "TEXT"),
                    ("youtube_music_account_id", "TEXT"),
                ]
                for col_name, col_def in family_job_cols:
                    if col_name not in sj_cols:
                        await db.execute(f"ALTER TABLE sync_jobs ADD COLUMN {col_name} {col_def}")

            # Migrate replicated_playlists constraint if it has old 2-column unique constraint
            async with db.execute("SELECT sql FROM sqlite_master WHERE name = 'replicated_playlists'") as cursor:
                rp_row = await cursor.fetchone()
                if rp_row and "UNIQUE(source_playlist_id, destination_playlist_id)" in rp_row["sql"]:
                    logger.info("Migrating replicated_playlists table constraint to include user_id...")
                    await db.execute("PRAGMA foreign_keys = OFF;")
                    await db.execute("""
                        CREATE TABLE replicated_playlists_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id TEXT,
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
                            UNIQUE(user_id, source_playlist_id, destination_playlist_id)
                        );
                    """)
                    await db.execute("""
                        INSERT INTO replicated_playlists_new (
                            id, user_id, source_playlist_id, source_playlist_name,
                            destination_playlist_id, destination_playlist_name,
                            enabled, sync_interval_seconds, last_source_revision,
                            last_sync_at, last_sync_status, created_at, updated_at
                        )
                        SELECT
                            id, user_id, source_playlist_id, source_playlist_name,
                            destination_playlist_id, destination_playlist_name,
                            enabled, sync_interval_seconds, last_source_revision,
                            last_sync_at, last_sync_status, created_at, updated_at
                        FROM replicated_playlists;
                    """)
                    await db.execute("DROP TABLE replicated_playlists;")
                    await db.execute("ALTER TABLE replicated_playlists_new RENAME TO replicated_playlists;")
                    await db.execute("PRAGMA foreign_keys = ON;")

            await db.execute("CREATE INDEX IF NOT EXISTS idx_replicated_playlists_user_id ON replicated_playlists(user_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_ytm_uploads_user_id ON ytm_uploads(user_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sync_jobs_user_id ON sync_jobs(user_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sync_jobs_family_id ON sync_jobs(family_id);")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sync_jobs_destination_user_id ON sync_jobs(destination_user_id);")

            await db.commit()

        # Bootstrap initial admin user & single-user installation migration if no users exist
        await self._ensure_admin_bootstrapped()
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
                    title, artist, album, duration, like_status, thumbnail, last_seen, user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    last_seen=excluded.last_seen,
                    user_id=COALESCE(excluded.user_id, ytm_uploads.user_id)
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
                    upload_info.get("user_id"),
                )
            )
            await db.commit()

    async def get_all_ytm_uploads(self, user_id: Optional[str] = None) -> list[YtmUpload]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            if user_id:
                async with db.execute("SELECT * FROM ytm_uploads WHERE user_id = ?", (user_id,)) as cursor:
                    rows = await cursor.fetchall()
                    return [YtmUpload(**dict(r)) for r in rows]
            else:
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

    async def get_ytm_upload_by_video_id(self, video_id: str, user_id: Optional[str] = None) -> Optional[YtmUpload]:
        """Lookup an upload by its YouTube video_id."""
        if not video_id:
            return None
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            if user_id:
                async with db.execute(
                    "SELECT * FROM ytm_uploads WHERE (video_id = ? OR upload_video_id = ?) AND user_id = ? LIMIT 1",
                    (video_id, video_id, user_id)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return YtmUpload(**dict(row))
            else:
                async with db.execute("SELECT * FROM ytm_uploads WHERE video_id = ? OR upload_video_id = ? LIMIT 1", (video_id, video_id)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return YtmUpload(**dict(row))
                return None

    async def find_ytm_upload_by_title_artist(self, title: str, artist: Optional[str] = None, user_id: Optional[str] = None) -> Optional[YtmUpload]:
        """Find an existing upload matching normalized title and artist."""
        from .normalizer import normalize_text
        clean_title = normalize_text(title)
        clean_artist = normalize_text(artist) if artist else ""
        if not clean_title:
            return None
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            # Search by title prefix for fast indexing
            if user_id:
                query = "SELECT * FROM ytm_uploads WHERE title LIKE ? AND user_id = ? LIMIT 50"
                params = (f"%{title[:20]}%", user_id)
            else:
                query = "SELECT * FROM ytm_uploads WHERE title LIKE ? LIMIT 50"
                params = (f"%{title[:20]}%",)
            async with db.execute(query, params) as cursor:
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
            user_id=data.get("user_id"),
            family_id=data.get("family_id"),
            requested_by_user_id=data.get("requested_by_user_id"),
            destination_user_id=data.get("destination_user_id") or data.get("user_id"),
            youtube_music_account_id=data.get("youtube_music_account_id"),
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
        verification_status: str = "PENDING",
        user_id: Optional[str] = None,
        family_id: Optional[str] = None,
        requested_by_user_id: Optional[str] = None,
        destination_user_id: Optional[str] = None,
        youtube_music_account_id: Optional[str] = None
    ) -> int:
        target_user = destination_user_id or user_id
        async with self.get_connection() as db:
            async with db.execute(
                """
                INSERT INTO sync_jobs (
                    music_file_id, status, attempts,
                    source_type, source_id, source_url,
                    expected_duration, original_file_hash, replacement_allowed,
                    verification_status, user_id, family_id, requested_by_user_id,
                    destination_user_id, youtube_music_account_id
                ) VALUES (?, 'queued', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
                """,
                (
                    music_file_id, source_type, source_id, source_url,
                    expected_duration, original_file_hash, 1 if replacement_allowed else 0,
                    verification_status, target_user, family_id, requested_by_user_id,
                    target_user, youtube_music_account_id
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

    async def get_user_sync_history(self, user_id: str, limit: int = 100) -> list[SyncJob]:
        """Fetch sync history strictly filtered to a specific user (Phase W)."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT sj.*, mf.path, mf.filename, mf.artist, mf.album, mf.title, mf.duration, mf.format, mf.file_size, mf.modified_time,
                       m.ytm_upload_id
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                LEFT JOIN matches m ON mf.id = m.music_file_id
                WHERE sj.user_id = ?
                ORDER BY sj.id DESC LIMIT ?
                """,
                (user_id, limit)
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
    async def get_dashboard_counts(self, user_id: Optional[str] = None) -> dict:
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM music_files") as c:
                local_count = (await c.fetchone())[0]

            if user_id:
                async with db.execute("SELECT COUNT(*) FROM ytm_uploads WHERE user_id = ?", (user_id,)) as c:
                    ytm_count = (await c.fetchone())[0]
            else:
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

            failed_sql = """
                SELECT COUNT(*) FROM sync_jobs s1
                WHERE s1.status = 'failed' 
                AND id = (SELECT MAX(id) FROM sync_jobs s2 WHERE s2.music_file_id = s1.music_file_id)
            """
            failed_params = ()
            if user_id:
                failed_sql += " AND s1.user_id = ?"
                failed_params = (user_id,)
            async with db.execute(failed_sql, failed_params) as c:
                failed_count = (await c.fetchone())[0]

            queue_sql = "SELECT COUNT(*) FROM sync_jobs WHERE status IN ('queued', 'uploading', 'verifying')"
            queue_params = ()
            if user_id:
                queue_sql += " AND user_id = ?"
                queue_params = (user_id,)
            async with db.execute(queue_sql, queue_params) as c:
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

    async def get_replicated_playlists(self, user_id: Optional[str] = None, enabled_only: bool = False) -> list[ReplicatedPlaylist]:
        """Fetch configured replicated playlist watchers, optionally filtered by user_id."""
        conditions = []
        params = []
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)
        if enabled_only:
            conditions.append("enabled = 1")

        query = "SELECT * FROM replicated_playlists"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id ASC"

        async with self.get_connection() as db:
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [ReplicatedPlaylist(**dict(row)) for row in rows]

    async def get_replicated_playlist(self, replicated_id: int, user_id: Optional[str] = None) -> Optional[ReplicatedPlaylist]:
        """Fetch a specific replicated playlist watcher by ID, optionally verifying ownership."""
        query = "SELECT * FROM replicated_playlists WHERE id = ?"
        params = [replicated_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        async with self.get_connection() as db:
            async with db.execute(query, tuple(params)) as cursor:
                row = await cursor.fetchone()
                return ReplicatedPlaylist(**dict(row)) if row else None

    async def get_replicated_playlist_by_source_id(self, source_playlist_id: str, user_id: Optional[str] = None) -> Optional[ReplicatedPlaylist]:
        """Fetch a replicated playlist watcher by its source YouTube Music playlist ID."""
        query = "SELECT * FROM replicated_playlists WHERE source_playlist_id = ?"
        params = [source_playlist_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        async with self.get_connection() as db:
            async with db.execute(query, tuple(params)) as cursor:
                row = await cursor.fetchone()
                return ReplicatedPlaylist(**dict(row)) if row else None

    async def create_replicated_playlist(
        self,
        source_playlist_id: str,
        source_playlist_name: str,
        destination_playlist_id: str,
        destination_playlist_name: str,
        enabled: bool = True,
        sync_interval_seconds: int = 300,
        user_id: Optional[str] = None,
        replica_mode: Optional[str] = None
    ) -> int:
        """Create a new replicated playlist configuration owned by user_id."""
        async with self.get_connection() as db:
            if not replica_mode:
                if "locker" in destination_playlist_name.lower() or "upload" in destination_playlist_name.lower():
                    mode_val = "locker_only"
                else:
                    mode_val = "1to1_youtube"
            else:
                mode_val = replica_mode

            cursor = await db.execute(
                """
                INSERT INTO replicated_playlists (
                    user_id, source_playlist_id, source_playlist_name,
                    destination_playlist_id, destination_playlist_name,
                    enabled, sync_interval_seconds, replica_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    user_id, source_playlist_id, source_playlist_name,
                    destination_playlist_id, destination_playlist_name,
                    1 if enabled else 0, sync_interval_seconds, mode_val
                )
            )
            row = await cursor.fetchone()
            await db.commit()
            return row["id"]

    async def update_replicated_playlist(self, replicated_id: int, user_id: Optional[str] = None, **kwargs) -> Optional[ReplicatedPlaylist]:
        """Update fields of a replicated playlist configuration, verifying user_id if provided."""
        if not kwargs:
            return await self.get_replicated_playlist(replicated_id, user_id=user_id)

        set_clauses = []
        values = []
        for k, v in kwargs.items():
            if k in ("destination_playlist_id", "source_playlist_name", "destination_playlist_name", "last_source_revision", "last_sync_status", "last_sync_at", "replica_mode"):
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

        where_clause = "WHERE id = ?"
        if user_id is not None:
            where_clause += " AND user_id = ?"
            values.append(user_id)

        async with self.get_connection() as db:
            await db.execute(
                f"UPDATE replicated_playlists SET {', '.join(set_clauses)} {where_clause}",
                tuple(values)
            )
            await db.commit()
        return await self.get_replicated_playlist(replicated_id, user_id=user_id)

    async def delete_replicated_playlist(self, replicated_id: int, user_id: Optional[str] = None) -> bool:
        """Delete a replicated playlist configuration, verifying user_id if provided."""
        query = "DELETE FROM replicated_playlists WHERE id = ?"
        params = [replicated_id]
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)

        async with self.get_connection() as db:
            cursor = await db.execute(query, tuple(params))
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

    # --- Multi-User Management Methods (Phases D through Q) ---

    async def _ensure_admin_bootstrapped(self):
        """Bootstrap default admin account and migrate single-user installation if no users exist."""
        count = await self.count_users()
        if count > 0:
            return

        admin_id = str(uuid.uuid4())
        admin_username = os.environ.get("YTM_SYNC_ADMIN_USER", "admin").strip()
        admin_password = os.environ.get("YTM_SYNC_ADMIN_PASSWORD", "").strip()

        if not admin_password:
            key_file = AUTH_DIR / "admin_password.txt"
            if key_file.exists():
                admin_password = key_file.read_text(encoding="utf-8").strip()
            if not admin_password:
                admin_password = secrets.token_urlsafe(12)
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                fd = os.open(str(key_file), flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(admin_password + "\n")
                try:
                    os.chmod(str(key_file), 0o600)
                except OSError:
                    pass
                logger.info(f"Generated initial admin credentials in {key_file}")

        pwd_hash = hash_password(admin_password)
        admin_user = await self.create_user(
            username=admin_username,
            password_hash=pwd_hash,
            role=UserRole.ADMIN,
            is_active=True,
            user_id=admin_id
        )
        logger.info(f"Bootstrapped default admin user '{admin_username}' (ID: {admin_id})")

        # Backfill existing single-user records to this admin
        async with self.get_connection() as db:
            await db.execute("UPDATE replicated_playlists SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            await db.execute("UPDATE ytm_uploads SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            await db.execute("UPDATE sync_jobs SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            await db.execute("UPDATE needs_help_tracks SET user_id = ? WHERE user_id IS NULL", (admin_id,))
            await db.commit()

        # Migrate existing global authentication if present (Phase V)
        legacy_auth = settings.auth_file
        if not legacy_auth.exists() and settings.auth_dir == AUTH_DIR:
            fallback = DEFAULT_DATA_DIR / "headers_auth.json"
            if fallback.exists():
                legacy_auth = fallback

        if legacy_auth.exists() and legacy_auth.stat().st_size > 10:
            backup_file = legacy_auth.with_name(legacy_auth.name + ".migrated_backup")
            deprecate_file = legacy_auth.with_name(legacy_auth.name + ".migrated")
            try:
                import shutil
                # Step 1: Create recoverable backup before touching
                shutil.copy2(str(legacy_auth), str(backup_file))
                logger.info(f"Phase V: Created safety backup of legacy authentication at {backup_file}")

                # Step 2: Move/copy credentials into user-specific storage
                user_auth_dir = get_user_subpath(admin_id, "auth")
                user_auth_file = user_auth_dir / "headers_auth.json"
                raw_bytes = legacy_auth.read_bytes()
                
                # Encrypt into user storage
                enc_data = encrypt_auth_data(raw_bytes)
                flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                fd = os.open(str(user_auth_file), flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(enc_data)
                try:
                    os.chmod(str(user_auth_file), 0o600)
                except OSError:
                    pass

                # Step 3: Verify connection
                from .ytm_client import ytm_client
                conn_test = await ytm_client.test_connection(user_id=admin_id)
                
                if conn_test.get("connected"):
                    acc_name = conn_test.get("user_name") or "Migrated Account"
                    await self.create_or_update_ytm_account(
                        user_id=admin_id,
                        account_name=acc_name,
                        auth_reference=str(user_auth_file),
                        status=YtmAccountStatus.CONNECTED
                    )
                    # Step 4: Deprecate old global authentication by renaming to .migrated (not deleted silently!)
                    shutil.move(str(legacy_auth), str(deprecate_file))
                    logger.info(
                        f"Phase V: Successfully verified and migrated legacy global authentication to user '{admin_id}' ({acc_name}). "
                        f"Old global authentication preserved at {deprecate_file}."
                    )
                else:
                    await self.create_or_update_ytm_account(
                        user_id=admin_id,
                        account_name="Unverified Migrated Account",
                        auth_reference=str(user_auth_file),
                        status=YtmAccountStatus.DISCONNECTED
                    )
                    logger.warning(
                        f"Phase V: Legacy authentication copied to user '{admin_id}', but connection test failed: {conn_test.get('message')}. "
                        f"Preserving original global file at {legacy_auth}."
                    )
            except Exception as e:
                logger.error(f"Phase V: Failed during legacy authentication migration: {e}", exc_info=True)

    async def create_user(
        self,
        username: Union[str, UserCreate],
        password_hash: Optional[str] = None,
        role: Union[UserRole, str] = UserRole.USER,
        is_active: bool = True,
        user_id: Optional[str] = None
    ) -> User:
        if isinstance(username, UserCreate):
            req = username
            clean_username = req.username.strip()
            password_hash = hash_password(req.password)
            role = req.role
        else:
            clean_username = str(username).strip()
            if not password_hash:
                raise ValueError("password_hash is required when username is a string")
        uid = user_id or str(uuid.uuid4())
        role_str = role.value if isinstance(role, UserRole) else str(role).upper()

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO users (id, username, password_hash, role, is_active)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uid, clean_username, password_hash, role_str, 1 if is_active else 0)
            )
            await db.commit()
        return await self.get_user_by_id(uid)

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["is_active"] = bool(data.get("is_active", 1))
                return User(**data)

    async def get_user_by_username(self, username: str) -> Optional[User]:
        clean = username.strip()
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (clean,)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["is_active"] = bool(data.get("is_active", 1))
                return User(**data)

    async def list_users(self) -> list[User]:
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM users ORDER BY created_at ASC") as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    data = dict(row)
                    data["is_active"] = bool(data.get("is_active", 1))
                    results.append(User(**data))
                return results

    async def update_user(
        self,
        user_id: str,
        update_or_role: Optional[Union[UserUpdate, UserRole, str]] = None,
        is_active: Optional[bool] = None,
        password_hash: Optional[str] = None,
        role: Optional[Union[UserRole, str]] = None
    ) -> Optional[User]:
        if isinstance(update_or_role, UserUpdate):
            req = update_or_role
            if req.role is not None:
                role = req.role
            if req.is_active is not None:
                is_active = req.is_active
            if req.password is not None:
                password_hash = hash_password(req.password)
        elif isinstance(update_or_role, (UserRole, str)):
            role = update_or_role

        clauses = []
        params = []
        if role is not None:
            clauses.append("role = ?")
            params.append(role.value if isinstance(role, UserRole) else str(role).upper())
        if is_active is not None:
            clauses.append("is_active = ?")
            params.append(1 if is_active else 0)
        if password_hash is not None:
            clauses.append("password_hash = ?")
            params.append(password_hash)

        if not clauses:
            return await self.get_user_by_id(user_id)

        clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.append(user_id)

        async with self.get_connection() as db:
            await db.execute(f"UPDATE users SET {', '.join(clauses)} WHERE id = ?", tuple(params))
            await db.commit()
        return await self.get_user_by_id(user_id)

    async def update_last_login(self, user_id: str):
        now = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            await db.execute("UPDATE users SET last_login_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (now, user_id))
            await db.commit()

    async def delete_user(self, user_id: str) -> bool:
        """Permanently delete user and all user-owned database records (Phase U)."""
        async with self.get_connection() as db:
            # 1. Clean up replicated playlist snapshots and events for playlists owned by this user
            await db.execute(
                """
                DELETE FROM replicated_playlist_snapshots 
                WHERE replicated_playlist_id IN (SELECT id FROM replicated_playlists WHERE user_id = ?)
                """,
                (user_id,)
            )
            await db.execute(
                """
                DELETE FROM replicated_playlist_events 
                WHERE replicated_playlist_id IN (SELECT id FROM replicated_playlists WHERE user_id = ?)
                """,
                (user_id,)
            )
            await db.execute("DELETE FROM replicated_playlists WHERE user_id = ?", (user_id,))
            
            # 2. Clean up uploads, sync jobs, needs_help_tracks owned by this user
            await db.execute("DELETE FROM ytm_uploads WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM sync_jobs WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM needs_help_tracks WHERE user_id = ?", (user_id,))

            # 3. Clean up user settings, ytm accounts, sessions, and family data
            await db.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM ytm_accounts WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM app_sessions WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM family_members WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM family_invitations WHERE created_by_user_id = ? OR accepted_by_user_id = ?", (user_id, user_id))
            await db.execute("DELETE FROM families WHERE owner_user_id = ?", (user_id,))

            # 4. Delete user record
            cursor = await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def count_users(self) -> int:
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM users") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def count_admins(self) -> int:
        """Count active administrator accounts."""
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM users WHERE role = 'ADMIN' AND is_active = 1") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    # --- Session Management (Phase F) ---

    async def create_app_session(self, user_id: str, duration_seconds: int = 7 * 86400) -> AppSession:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=duration_seconds)).isoformat()
        now_str = now.isoformat()

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO app_sessions (token, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (token, user_id, now_str, expires_at)
            )
            await db.commit()
        return AppSession(token=token, user_id=user_id, created_at=now_str, expires_at=expires_at)

    async def get_app_session(self, token: str) -> Optional[AppSession]:
        if not token:
            return None
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM app_sessions WHERE token = ?", (token,)) as cursor:
                row = await cursor.fetchone()
                return AppSession(**dict(row)) if row else None

    async def get_user_by_session_token(self, token: str) -> Optional[User]:
        if not token:
            return None
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT u.* FROM users u
                INNER JOIN app_sessions s ON u.id = s.user_id
                WHERE s.token = ? AND s.revoked_at IS NULL AND s.expires_at > ? AND u.is_active = 1
                """,
                (token, now_iso)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                data["is_active"] = bool(data.get("is_active", 1))
                return User(**data)

    async def revoke_app_session(self, token: str) -> bool:
        if not token:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            cursor = await db.execute("UPDATE app_sessions SET revoked_at = ? WHERE token = ?", (now_iso, token))
            await db.commit()
            return cursor.rowcount > 0

    async def revoke_all_user_sessions(self, user_id: str):
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            await db.execute("UPDATE app_sessions SET revoked_at = ? WHERE user_id = ?", (now_iso, user_id))
            await db.commit()

    # --- YouTube Music Account Management (Phase H & J) ---

    async def create_or_update_ytm_account(
        self,
        user_id: str,
        account_name: Optional[str] = None,
        account_identifier: Optional[str] = None,
        auth_reference: Optional[str] = None,
        encrypted_credentials: Optional[str] = None,
        status: Union[YtmAccountStatus, str] = YtmAccountStatus.CONNECTED
    ) -> YouTubeMusicAccount:
        now_iso = datetime.now(timezone.utc).isoformat()
        status_str = status.value if isinstance(status, YtmAccountStatus) else str(status).upper()

        existing = await self.get_ytm_account_by_user_id(user_id)
        async with self.get_connection() as db:
            if existing:
                await db.execute(
                    """
                    UPDATE ytm_accounts
                    SET account_name = COALESCE(?, account_name),
                        account_identifier = COALESCE(?, account_identifier),
                        auth_reference = COALESCE(?, auth_reference),
                        encrypted_credentials = COALESCE(?, encrypted_credentials),
                        status = ?,
                        updated_at = CURRENT_TIMESTAMP,
                        last_verified_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (account_name, account_identifier, auth_reference, encrypted_credentials, status_str, user_id)
                )
                account_id = existing.id
            else:
                account_id = str(uuid.uuid4())
                await db.execute(
                    """
                    INSERT INTO ytm_accounts (
                        id, user_id, account_name, account_identifier,
                        auth_reference, encrypted_credentials, status,
                        created_at, updated_at, last_verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (account_id, user_id, account_name, account_identifier, auth_reference, encrypted_credentials, status_str)
                )
            await db.commit()
        return await self.get_ytm_account_by_user_id(user_id)

    async def get_ytm_account_by_user_id(self, user_id: str) -> Optional[YouTubeMusicAccount]:
        async with self.get_connection() as db:
            async with db.execute("SELECT * FROM ytm_accounts WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return YouTubeMusicAccount(**dict(row)) if row else None

    get_ytm_account = get_ytm_account_by_user_id

    async def delete_ytm_account_for_user(self, user_id: str) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM ytm_accounts WHERE user_id = ?", (user_id,))
            await db.commit()
            return cursor.rowcount > 0

    # --- User-Specific Settings Management (Phase Q) ---

    async def get_user_setting(self, user_id: str, key: str, default: Any = None) -> Any:
        async with self.get_connection() as db:
            async with db.execute("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (user_id, key)) as cursor:
                row = await cursor.fetchone()
                if row:
                    try:
                        return json.loads(row["value"])
                    except Exception:
                        return row["value"]
                return default

    async def set_user_setting(self, user_id: str, key: str, value: Any):
        val_str = json.dumps(value) if not isinstance(value, str) else value
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO user_settings (user_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value
                """,
                (user_id, key, val_str)
            )
            await db.commit()

    async def get_user_settings(self, user_id: str) -> UserSettings:
        sync_enabled = await self.get_user_setting(user_id, "sync_enabled", default=True)
        sync_interval = await self.get_user_setting(user_id, "sync_interval_seconds", default=300)
        auto_upload = await self.get_user_setting(user_id, "auto_upload", default=False)
        download_location = await self.get_user_setting(user_id, "download_location", default=None)
        music_folders = await self.get_user_setting(user_id, "music_folders", default=[])
        scan_interval = await self.get_user_setting(user_id, "scan_interval_minutes", default=15)
        verify_uploads = await self.get_setting("verify_uploads", default=True)
        allow_automatic_replacement = await self.get_setting("allow_automatic_replacement", default=settings.allow_automatic_replacement)
        return UserSettings(
            user_id=user_id,
            sync_enabled=bool(sync_enabled),
            sync_interval_seconds=int(sync_interval),
            auto_upload=bool(auto_upload),
            download_location=download_location,
            music_folders=list(music_folders),
            scan_interval_minutes=int(scan_interval),
            verify_uploads=bool(verify_uploads),
            allow_automatic_replacement=bool(allow_automatic_replacement)
        )

    async def update_user_settings(self, user_id: str, updates: Union[UserSettingsUpdate, dict[str, Any]]) -> UserSettings:
        items = updates.model_dump(exclude_unset=True) if hasattr(updates, "model_dump") else dict(updates)
        for k, v in items.items():
            if v is not None:
                if k == "allow_automatic_replacement":
                    await self.set_setting("allow_automatic_replacement", v)
                    settings.allow_automatic_replacement = v
                elif k == "verify_uploads":
                    await self.set_setting("verify_uploads", v)
                else:
                    await self.set_user_setting(user_id, k, v)
        return await self.get_user_settings(user_id)

    # --- User-Scoped Queries (Phase M) ---

    async def get_user_sync_jobs(self, user_id: str, limit: int = 50) -> list[SyncJob]:
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM sync_jobs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [SyncJob(**dict(row)) for row in rows]

    async def get_user_uploads(self, user_id: str, limit: int = 50) -> list[YtmUpload]:
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM ytm_uploads WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [YtmUpload(**dict(row)) for row in rows]


    # ==========================================
    # Family Mode & Multi-Account Methods (Sections 1-40)
    # ==========================================

    async def create_family(self, name: str, owner_user_id: str) -> Family:
        family_id = f"family_{secrets.token_hex(8)}"
        member_id = f"fmem_{secrets.token_hex(8)}"
        async with self.get_connection() as db:
            await db.execute(
                "INSERT INTO families (id, name, owner_user_id) VALUES (?, ?, ?)",
                (family_id, name, owner_user_id)
            )
            await db.execute(
                """
                INSERT INTO family_members (
                    id, family_id, user_id, role, status,
                    show_account_in_family, allow_family_uploads, allow_family_playlists, allow_family_sync
                ) VALUES (?, ?, ?, 'OWNER', 'ACTIVE', 1, 1, 0, 0)
                """,
                (member_id, family_id, owner_user_id)
            )
            await db.commit()
        fam = await self.get_family_by_id(family_id)
        if not fam:
            raise ValueError("Failed to create family")
        return fam

    async def get_family_by_id(self, family_id: str) -> Optional[Family]:
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT f.*, u.username as owner_username
                FROM families f
                LEFT JOIN users u ON f.owner_user_id = u.id
                WHERE f.id = ?
                """,
                (family_id,)
            ) as cursor:
                f_row = await cursor.fetchone()
                if not f_row:
                    return None
                f_dict = dict(f_row)

        members = await self.get_family_members(family_id)
        return Family(
            id=f_dict["id"],
            name=f_dict["name"],
            owner_user_id=f_dict["owner_user_id"],
            owner_username=f_dict.get("owner_username"),
            created_at=f_dict.get("created_at"),
            updated_at=f_dict.get("updated_at"),
            members=members
        )

    async def get_user_families(self, user_id: str) -> list[Family]:
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT DISTINCT f.id
                FROM families f
                JOIN family_members fm ON f.id = fm.family_id
                WHERE fm.user_id = ? AND fm.status = 'ACTIVE'
                ORDER BY f.created_at ASC
                """,
                (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                family_ids = [row["id"] for row in rows]

        families = []
        for fid in family_ids:
            fam = await self.get_family_by_id(fid)
            if fam:
                families.append(fam)
        return families

    async def update_family(self, family_id: str, name: str) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute(
                "UPDATE families SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name, family_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def transfer_family_ownership(self, family_id: str, new_owner_user_id: str) -> bool:
        """Transfer family ownership to another active member (Section 30)."""
        async with self.get_connection() as db:
            # Validate target is an active member
            async with db.execute(
                "SELECT role FROM family_members WHERE family_id = ? AND user_id = ? AND status = 'ACTIVE'",
                (family_id, new_owner_user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    raise ValueError("Target user is not an active member of this family")

            # Update family owner
            await db.execute(
                "UPDATE families SET owner_user_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_owner_user_id, family_id)
            )
            # Demote old owner to ADMIN
            await db.execute(
                "UPDATE family_members SET role = 'ADMIN', updated_at = CURRENT_TIMESTAMP WHERE family_id = ? AND role = 'OWNER'",
                (family_id,)
            )
            # Promote new owner to OWNER
            await db.execute(
                "UPDATE family_members SET role = 'OWNER', updated_at = CURRENT_TIMESTAMP WHERE family_id = ? AND user_id = ?",
                (family_id, new_owner_user_id)
            )
            await db.commit()
            return True

    async def delete_family(self, family_id: str) -> bool:
        """Delete family and relationships without deleting users or their YTM data (Section 31)."""
        async with self.get_connection() as db:
            await db.execute("DELETE FROM family_invitations WHERE family_id = ?", (family_id,))
            await db.execute("DELETE FROM family_members WHERE family_id = ?", (family_id,))
            cursor = await db.execute("DELETE FROM families WHERE id = ?", (family_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def leave_family(self, family_id: str, user_id: str) -> bool:
        """Allow a member to leave family, strictly preserving personal data (Section 29)."""
        fam = await self.get_family_by_id(family_id)
        if not fam:
            raise ValueError("Family not found")
        if fam.owner_user_id == user_id:
            raise ValueError("Family owner cannot leave without transferring ownership or deleting the family.")

        return await self.remove_family_member(family_id, user_id)

    async def add_family_member(
        self,
        family_id: str,
        user_id: str,
        role: str = "MEMBER",
        show_account_in_family: bool = True,
        allow_family_uploads: bool = True,
        allow_family_playlists: bool = False,
        allow_family_sync: bool = False
    ) -> FamilyMember:
        member_id = f"fmem_{secrets.token_hex(8)}"
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO family_members (
                    id, family_id, user_id, role, status,
                    show_account_in_family, allow_family_uploads, allow_family_playlists, allow_family_sync
                ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?)
                """,
                (
                    member_id, family_id, user_id, role,
                    1 if show_account_in_family else 0,
                    1 if allow_family_uploads else 0,
                    1 if allow_family_playlists else 0,
                    1 if allow_family_sync else 0
                )
            )
            await db.commit()

        mem = await self.get_family_member(family_id, user_id)
        if not mem:
            raise ValueError("Failed to add family member")
        return mem

    async def get_family_member(self, family_id: str, user_id: str) -> Optional[FamilyMember]:
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT fm.*, u.username
                FROM family_members fm
                JOIN users u ON fm.user_id = u.id
                WHERE fm.family_id = ? AND fm.user_id = ?
                """,
                (family_id, user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return FamilyMember(
                    id=row["id"],
                    family_id=row["family_id"],
                    user_id=row["user_id"],
                    username=row["username"],
                    role=FamilyRole(row["role"]),
                    status=FamilyMemberStatus(row["status"]),
                    show_account_in_family=bool(row["show_account_in_family"]),
                    allow_family_uploads=bool(row["allow_family_uploads"]),
                    allow_family_playlists=bool(row["allow_family_playlists"]),
                    allow_family_sync=bool(row["allow_family_sync"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"]
                )

    async def get_family_members(self, family_id: str) -> list[FamilyMember]:
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT fm.*, u.username
                FROM family_members fm
                JOIN users u ON fm.user_id = u.id
                WHERE fm.family_id = ?
                ORDER BY CASE WHEN fm.role = 'OWNER' THEN 0 WHEN fm.role = 'ADMIN' THEN 1 ELSE 2 END, fm.created_at ASC
                """,
                (family_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    FamilyMember(
                        id=row["id"],
                        family_id=row["family_id"],
                        user_id=row["user_id"],
                        username=row["username"],
                        role=FamilyRole(row["role"]),
                        status=FamilyMemberStatus(row["status"]),
                        show_account_in_family=bool(row["show_account_in_family"]),
                        allow_family_uploads=bool(row["allow_family_uploads"]),
                        allow_family_playlists=bool(row["allow_family_playlists"]),
                        allow_family_sync=bool(row["allow_family_sync"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"]
                    )
                    for row in rows
                ]

    async def update_family_member_privacy(
        self,
        family_id: str,
        user_id: str,
        show_account_in_family: Optional[bool] = None,
        allow_family_uploads: Optional[bool] = None,
        allow_family_playlists: Optional[bool] = None,
        allow_family_sync: Optional[bool] = None
    ) -> bool:
        clauses = []
        params = []
        if show_account_in_family is not None:
            clauses.append("show_account_in_family = ?")
            params.append(1 if show_account_in_family else 0)
        if allow_family_uploads is not None:
            clauses.append("allow_family_uploads = ?")
            params.append(1 if allow_family_uploads else 0)
        if allow_family_playlists is not None:
            clauses.append("allow_family_playlists = ?")
            params.append(1 if allow_family_playlists else 0)
        if allow_family_sync is not None:
            clauses.append("allow_family_sync = ?")
            params.append(1 if allow_family_sync else 0)

        if not clauses:
            return True

        clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([family_id, user_id])

        async with self.get_connection() as db:
            cursor = await db.execute(
                f"UPDATE family_members SET {', '.join(clauses)} WHERE family_id = ? AND user_id = ?",
                tuple(params)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update_family_member_role(self, family_id: str, user_id: str, role: str) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute(
                "UPDATE family_members SET role = ?, updated_at = CURRENT_TIMESTAMP WHERE family_id = ? AND user_id = ?",
                (role, family_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    async def remove_family_member(self, family_id: str, user_id: str) -> bool:
        """Remove member from family without deleting user or user's YTM data (Section 28)."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                "DELETE FROM family_members WHERE family_id = ? AND user_id = ?",
                (family_id, user_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    # --- Family Invitations (Section 27) ---

    async def create_family_invitation(
        self,
        family_id: str,
        created_by_user_id: str,
        role: str = "MEMBER",
        ttl_hours: int = 48
    ) -> FamilyInvitation:
        token = secrets.token_urlsafe(32)
        inv_id = f"finv_{secrets.token_hex(8)}"
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=ttl_hours)).isoformat()

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO family_invitations (id, family_id, token, role, created_by_user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (inv_id, family_id, token, role, created_by_user_id, now.isoformat(), expires_at)
            )
            await db.commit()

        return FamilyInvitation(
            id=inv_id,
            family_id=family_id,
            token=token,
            role=FamilyRole(role),
            created_by_user_id=created_by_user_id,
            created_at=now.isoformat(),
            expires_at=expires_at,
            is_expired=False
        )

    async def get_family_invitation_by_token(self, token: str) -> Optional[FamilyInvitation]:
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM family_invitations WHERE token = ?",
                (token,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                data = dict(row)
                now_iso = datetime.now(timezone.utc).isoformat()
                is_exp = bool(data.get("accepted_at")) or (data["expires_at"] < now_iso)
                return FamilyInvitation(
                    id=data["id"],
                    family_id=data["family_id"],
                    token=data["token"],
                    role=FamilyRole(data["role"]),
                    created_by_user_id=data["created_by_user_id"],
                    created_at=data.get("created_at"),
                    expires_at=data["expires_at"],
                    accepted_at=data.get("accepted_at"),
                    accepted_by_user_id=data.get("accepted_by_user_id"),
                    is_expired=is_exp
                )

    async def accept_family_invitation(self, token: str, user_id: str) -> FamilyMember:
        inv = await self.get_family_invitation_by_token(token)
        if not inv:
            raise ValueError("Invalid or expired invitation token.")
        if inv.is_expired:
            raise ValueError("Invitation has expired or has already been used.")

        # Check if already a member
        existing = await self.get_family_member(inv.family_id, user_id)
        if existing:
            raise ValueError("You are already a member of this family.")

        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE family_invitations SET accepted_at = ?, accepted_by_user_id = ? WHERE id = ?",
                (now_iso, user_id, inv.id)
            )
            await db.commit()

        return await self.add_family_member(
            family_id=inv.family_id,
            user_id=user_id,
            role=inv.role.value
        )

    async def list_family_invitations(self, family_id: str) -> list[FamilyInvitation]:
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT * FROM family_invitations WHERE family_id = ? AND accepted_at IS NULL AND expires_at >= ? ORDER BY created_at DESC",
                (family_id, now_iso)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    FamilyInvitation(
                        id=row["id"],
                        family_id=row["family_id"],
                        token=row["token"],
                        role=FamilyRole(row["role"]),
                        created_by_user_id=row["created_by_user_id"],
                        created_at=row["created_at"],
                        expires_at=row["expires_at"],
                        accepted_at=row["accepted_at"],
                        accepted_by_user_id=row["accepted_by_user_id"],
                        is_expired=False
                    )
                    for row in rows
                ]

    async def revoke_family_invitation(self, family_id: str, invitation_id: str) -> bool:
        async with self.get_connection() as db:
            cursor = await db.execute(
                "DELETE FROM family_invitations WHERE family_id = ? AND id = ?",
                (family_id, invitation_id)
            )
            await db.commit()
            return cursor.rowcount > 0

    # --- Account Selection & Authorization (Sections 9, 23, 24) ---

    async def get_permitted_family_accounts(self, user_id: str) -> list[dict]:
        """Return list of accounts the caller can select or view (Section 9, 23)."""
        items = []
        async with self.get_connection() as db:
            # 1. Caller's own account
            async with db.execute(
                """
                SELECT u.id as user_id, u.username, y.id as account_id, y.account_name, y.status as ytm_status
                FROM users u
                LEFT JOIN ytm_accounts y ON u.id = y.user_id
                WHERE u.id = ?
                """,
                (user_id,)
            ) as cursor:
                self_row = await cursor.fetchone()
                if self_row:
                    items.append({
                        "user_id": self_row["user_id"],
                        "account_id": self_row["account_id"],
                        "username": self_row["username"],
                        "is_self": True,
                        "ytm_connected": self_row["ytm_status"] == "CONNECTED",
                        "account_name": self_row["account_name"],
                        "allow_family_uploads": True,
                        "allow_family_playlists": True,
                        "allow_family_sync": True
                    })

            # 2. Permitted family accounts
            async with db.execute(
                """
                SELECT DISTINCT fm.user_id, u.username, y.id as account_id, y.account_name, y.status as ytm_status,
                       fm.allow_family_uploads, fm.allow_family_playlists, fm.allow_family_sync
                FROM family_members fm
                JOIN users u ON fm.user_id = u.id
                LEFT JOIN ytm_accounts y ON u.id = y.user_id
                WHERE fm.family_id IN (SELECT family_id FROM family_members WHERE user_id = ? AND status = 'ACTIVE')
                  AND fm.user_id != ?
                  AND fm.show_account_in_family = 1
                  AND fm.status = 'ACTIVE'
                """,
                (user_id, user_id)
            ) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    items.append({
                        "user_id": row["user_id"],
                        "account_id": row["account_id"],
                        "username": row["username"],
                        "is_self": False,
                        "ytm_connected": row["ytm_status"] == "CONNECTED",
                        "account_name": row["account_name"],
                        "allow_family_uploads": bool(row["allow_family_uploads"]),
                        "allow_family_playlists": bool(row["allow_family_playlists"]),
                        "allow_family_sync": bool(row["allow_family_sync"])
                    })
        return items

    async def validate_upload_destination_permission(self, caller_user_id: str, destination_user_id: str) -> tuple[bool, Optional[str]]:
        """Strict server-side validation of upload destination permissions (Sections 15, 24)."""
        if caller_user_id == destination_user_id:
            acc = await self.get_ytm_account(caller_user_id)
            if not acc or acc.status != "CONNECTED":
                return False, "Your YouTube Music account is not connected."
            return True, None

        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT fm_dest.allow_family_uploads, y.status as ytm_status
                FROM family_members fm_caller
                JOIN family_members fm_dest ON fm_caller.family_id = fm_dest.family_id
                LEFT JOIN ytm_accounts y ON fm_dest.user_id = y.user_id
                WHERE fm_caller.user_id = ? AND fm_dest.user_id = ?
                  AND fm_caller.status = 'ACTIVE' AND fm_dest.status = 'ACTIVE'
                LIMIT 1
                """,
                (caller_user_id, destination_user_id)
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False, "Destination user is not in your family."
                if not row["allow_family_uploads"]:
                    return False, "Destination user has disabled family uploads."
                if row["ytm_status"] != "CONNECTED":
                    return False, "Destination YouTube Music account is not connected."
                return True, None

    async def check_track_duplicate_for_user(self, music_file_id: int, user_id: str) -> dict:
        """Duplicate detection strictly isolated to destination user (Section 34)."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT 1 FROM sync_jobs WHERE music_file_id = ? AND user_id = ? AND status IN ('completed', 'verified', 'VERIFIED')
                LIMIT 1
                """,
                (music_file_id, user_id)
            ) as cursor:
                matched = (await cursor.fetchone()) is not None

            async with db.execute(
                "SELECT status, error FROM sync_jobs WHERE music_file_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
                (music_file_id, user_id)
            ) as cursor:
                job_row = await cursor.fetchone()
                status = "already_uploaded" if matched else (job_row["status"] if job_row else "not_uploaded")
                err = job_row["error"] if job_row else None

            return {
                "is_uploaded": matched or (status in ("completed", "verified", "VERIFIED")),
                "status": status,
                "error": err
            }

    async def get_family_upload_history(self, family_id: str, caller_user_id: str, limit: int = 50) -> list[dict]:
        """Combined family upload history respecting member privacy (Section 17)."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT sj.id, sj.music_file_id, mf.filename, mf.title, mf.artist,
                       sj.user_id as destination_user_id, u_dest.username as destination_username,
                       sj.requested_by_user_id, u_req.username as requested_by_username,
                       sj.status, sj.error, sj.started_at as created_at, sj.completed_at
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                JOIN users u_dest ON sj.user_id = u_dest.id
                LEFT JOIN users u_req ON sj.requested_by_user_id = u_req.id
                WHERE (sj.family_id = ? OR sj.user_id IN (
                    SELECT user_id FROM family_members WHERE family_id = ? AND show_account_in_family = 1
                ))
                ORDER BY sj.id DESC LIMIT ?
                """,
                (family_id, family_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_family_queue_grouped(self, family_id: str, caller_user_id: str) -> list[dict]:
        """Group upload queue jobs by track and destination (Section 33)."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT sj.id as job_id, sj.music_file_id, mf.filename, mf.title, mf.artist,
                       sj.user_id as destination_user_id, u_dest.username as destination_username,
                       sj.status, sj.attempts, sj.error
                FROM sync_jobs sj
                JOIN music_files mf ON sj.music_file_id = mf.id
                JOIN users u_dest ON sj.user_id = u_dest.id
                WHERE (sj.family_id = ? OR sj.user_id IN (
                    SELECT user_id FROM family_members WHERE family_id = ? AND show_account_in_family = 1
                ))
                AND sj.status IN ('queued', 'uploading', 'verifying', 'PENDING', 'DOWNLOADING', 'VERIFYING')
                ORDER BY sj.id ASC
                """,
                (family_id, family_id)
            ) as cursor:
                rows = await cursor.fetchall()
                grouped = {}
                for r in rows:
                    fid = r["music_file_id"]
                    if fid not in grouped:
                        grouped[fid] = {
                            "music_file_id": fid,
                            "filename": r["filename"],
                            "title": r["title"],
                            "artist": r["artist"],
                            "destinations": []
                        }
                    grouped[fid]["destinations"].append({
                        "job_id": r["job_id"],
                        "destination_user_id": r["destination_user_id"],
                        "destination_username": r["destination_username"],
                        "status": r["status"],
                        "attempts": r["attempts"],
                        "error": r["error"]
                    })
                return list(grouped.values())

    async def get_family_permitted_sync_members(self, family_id: str) -> list[FamilyMember]:
        """Find members permitted to participate in Family Sync (Section 18)."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT fm.*, u.username
                FROM family_members fm
                JOIN users u ON fm.user_id = u.id
                JOIN ytm_accounts y ON u.id = y.user_id
                WHERE fm.family_id = ? AND fm.allow_family_sync = 1 AND fm.status = 'ACTIVE' AND y.status = 'CONNECTED'
                """,
                (family_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [
                    FamilyMember(
                        id=row["id"],
                        family_id=row["family_id"],
                        user_id=row["user_id"],
                        username=row["username"],
                        role=FamilyRole(row["role"]),
                        status=FamilyMemberStatus(row["status"]),
                        show_account_in_family=bool(row["show_account_in_family"]),
                        allow_family_uploads=bool(row["allow_family_uploads"]),
                        allow_family_playlists=bool(row["allow_family_playlists"]),
                        allow_family_sync=bool(row["allow_family_sync"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"]
                    )
                    for row in rows
                ]

    async def get_family_permitted_playlists(self, family_id: str, caller_user_id: str) -> list[dict]:
        """Read-only view of shared family playlists (Section 19)."""
        async with self.get_connection() as db:
            async with db.execute(
                """
                SELECT CAST(rp.id AS TEXT) as playlist_id,
                       rp.destination_playlist_id,
                       rp.source_playlist_id,
                       COALESCE(rp.destination_playlist_name, rp.source_playlist_name) as name,
                       COALESCE(rp.destination_playlist_name, rp.source_playlist_name) as title,
                       rp.user_id as owner_user_id,
                       u.username as owner_username,
                       (SELECT COUNT(*) FROM replicated_playlist_events WHERE replicated_playlist_id = rp.id) as track_count
                FROM replicated_playlists rp
                JOIN users u ON rp.user_id = u.id
                WHERE rp.user_id IN (
                    SELECT user_id FROM family_members WHERE family_id = ? AND allow_family_playlists = 1 AND status = 'ACTIVE'
                )
                ORDER BY rp.created_at DESC
                """,
                (family_id,)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def create_multi_account_playlists(
        self,
        playlist_name: str,
        destination_user_ids: list[str],
        created_by_user_id: str,
        family_id: Optional[str] = None
    ) -> list[dict]:
        """Create independent playlist on each permitted destination account (Section 35)."""
        results = []
        for dest_id in destination_user_ids:
            allowed, _ = await self.validate_upload_destination_permission(created_by_user_id, dest_id)
            if not allowed:
                continue
            rep = await self.create_replicated_playlist(
                name=playlist_name,
                source_type="uploaded_only",
                source_playlist_id=f"family_{family_id or 'shared'}_{secrets.token_hex(4)}",
                destination_playlist_id=f"ytm_{secrets.token_hex(6)}",
                user_id=dest_id
            )
            results.append({
                "destination_user_id": dest_id,
                "replicated_playlist_id": rep.id,
                "name": rep.name
            })
        return results


db = Database()



