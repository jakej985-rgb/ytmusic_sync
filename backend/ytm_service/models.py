from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class MatchType(str, Enum):
    EXACT = "exact"
    STRONG = "strong"
    WEAK = "weak"
    NONE = "none"

class UploadStatus(str, Enum):
    NOT_UPLOADED = "not_uploaded"
    QUEUED = "queued"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"

class MusicFile(BaseModel):
    id: Optional[int] = None
    path: str
    filename: str
    artist: Optional[str] = None
    album: Optional[str] = None
    title: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    duration: Optional[float] = None
    format: str
    file_size: int
    modified_time: float
    file_hash: Optional[str] = None
    metadata_hash: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # Joined or calculated attributes
    upload_status: UploadStatus = UploadStatus.NOT_UPLOADED
    matched_upload_id: Optional[str] = None
    match_score: Optional[float] = None

class YtmUpload(BaseModel):
    id: Optional[int] = None
    entity_id: str
    video_id: Optional[str] = None
    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    duration: Optional[float] = None
    like_status: Optional[str] = None
    thumbnail: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None

class MatchRecord(BaseModel):
    id: Optional[int] = None
    music_file_id: int
    ytm_upload_id: str
    match_type: MatchType
    match_score: float
    confirmed: bool = False
    created_at: Optional[str] = None

class SyncJob(BaseModel):
    id: Optional[int] = None
    music_file_id: int
    status: UploadStatus
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    ytm_entity_id: Optional[str] = None
    music_file: Optional[MusicFile] = None

class DashboardStats(BaseModel):
    ytm_connected: bool
    account_name: Optional[str] = None
    local_songs_count: int
    ytm_uploads_count: int
    missing_count: int
    uploaded_count: int
    failed_count: int
    in_queue_count: int
    is_scanning: bool
    is_uploading: bool

class ScanRequest(BaseModel):
    folders: Optional[list[str]] = None

class AuthSetupRequest(BaseModel):
    raw_headers: str

class ConnectionStatus(BaseModel):
    connected: bool
    message: str
    user_name: Optional[str] = None

class MusicBrainzMatch(BaseModel):
    mbid: str
    title: str
    primary_title: str
    artist: str
    featured_artists: Optional[str] = None
    album: Optional[str] = None
    track_number: Optional[int] = None
    release_date: Optional[str] = None
    cover_url: Optional[str] = None
    score: int = 100

