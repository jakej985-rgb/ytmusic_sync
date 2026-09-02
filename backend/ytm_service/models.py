from datetime import datetime
from enum import Enum
from typing import Optional, Union
from pydantic import BaseModel, Field

class MatchType(str, Enum):
    EXACT = "exact"
    STRONG = "strong"
    WEAK = "weak"
    NONE = "none"

class SyncDecision(str, Enum):
    SAFE = "SAFE"
    REVIEW = "REVIEW"
    BLOCKED = "BLOCKED"

class VerificationStatus(str, Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"

class UploadStatus(str, Enum):
    NOT_UPLOADED = "not_uploaded"
    QUEUED = "queued"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    FAILED = "failed"
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"

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
    upload_status: Union[UploadStatus, str] = UploadStatus.NOT_UPLOADED
    matched_upload_id: Optional[str] = None
    match_score: Optional[float] = None
    # Source & integrity attributes (Phase 10)
    source_type: Optional[str] = None
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    expected_duration: Optional[float] = None
    downloaded_source_id: Optional[str] = None
    verified: bool = False
    verification_status: Optional[str] = None
    verification_reason: Optional[str] = None
    downloaded_file_hash: Optional[str] = None
    replacement_allowed: bool = False

class YtmUpload(BaseModel):
    id: Optional[int] = None
    entity_id: str
    video_id: Optional[str] = None
    upload_video_id: Optional[str] = None
    upload_url: Optional[str] = None
    source_type: str = "ytm_upload"
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
    status: Union[UploadStatus, str]
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    ytm_entity_id: Optional[str] = None
    # Phase 10 Source & Integrity Fields
    source_type: Optional[str] = "ytm_upload"
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    expected_duration: Optional[float] = None
    downloaded_source_id: Optional[str] = None
    verified: bool = False
    verification_status: Union[VerificationStatus, str] = VerificationStatus.PENDING
    verification_reason: Optional[str] = None
    original_file_hash: Optional[str] = None
    downloaded_file_hash: Optional[str] = None
    replacement_allowed: bool = False
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
    source: Optional[str] = "YouTube Music"
    score: int = 100

class PlaylistTrackDownloadRequest(BaseModel):
    video_id: str
    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    thumbnail: Optional[str] = None
    destination_dir: Optional[str] = None
    enrich_metadata: bool = True

class PlaylistImportRequest(BaseModel):
    url: str

