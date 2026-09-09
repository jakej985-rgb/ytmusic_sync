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
    user_id: Optional[str] = None
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
    user_id: Optional[str] = None
    family_id: Optional[str] = None
    requested_by_user_id: Optional[str] = None
    destination_user_id: Optional[str] = None
    youtube_music_account_id: Optional[str] = None
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


class ReplicatedPlaylistCreate(BaseModel):
    source_playlist_id: str
    source_playlist_name: Optional[str] = None
    destination_playlist_id: Optional[str] = None
    destination_playlist_name: Optional[str] = None
    enabled: bool = True
    sync_interval_seconds: int = 300
    user_id: Optional[str] = None
    target_user_ids: Optional[list[str]] = None
    upload_missing_to_targets: bool = False


class PlaylistSyncMissingRequest(BaseModel):
    destination_dir: Optional[str] = None
    destination_user_ids: Optional[list[str]] = None


class ReplicatedPlaylistUpdate(BaseModel):
    destination_playlist_name: Optional[str] = None
    enabled: Optional[bool] = None
    sync_interval_seconds: Optional[int] = None


class ReplicatedPlaylist(BaseModel):
    id: int
    user_id: Optional[str] = None
    source_playlist_id: str
    source_playlist_name: str
    destination_playlist_id: str
    destination_playlist_name: str
    enabled: bool = True
    sync_interval_seconds: int = 300
    last_source_revision: Optional[str] = None
    last_sync_at: Optional[str] = None
    last_sync_status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ReplicatedPlaylistEvent(BaseModel):
    id: Optional[int] = None
    replicated_playlist_id: int
    source_track_id: Optional[str] = None
    source_video_id: Optional[str] = None
    locker_upload_id: Optional[str] = None
    action: str  # ADD, REMOVE, MOVE, NOOP, EXCLUDE
    reason: Optional[str] = None
    timestamp: Optional[str] = None


class SourcePlaylistTrackSnapshot(BaseModel):
    position: int
    video_id: str
    title: Optional[str] = None
    artist: Optional[str] = None
    set_video_id: Optional[str] = None


class SourcePlaylistSnapshot(BaseModel):
    id: Optional[int] = None
    replicated_playlist_id: int
    playlist_name: str
    revision: str
    track_count: int
    tracks: list[SourcePlaylistTrackSnapshot] = []
    created_at: Optional[str] = None


# --- Multi-User Architecture Models (Phases D through R) ---

class UserRole(str, Enum):
    ADMIN = "ADMIN"
    USER = "USER"


class User(BaseModel):
    id: str
    username: str
    password_hash: str
    role: UserRole = UserRole.USER
    is_active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login_at: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    username: str
    role: UserRole
    is_active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_login_at: Optional[str] = None


class UserCreate(BaseModel):
    username: str
    password: str
    role: UserRole = UserRole.USER
    is_active: bool = True


class UserUpdate(BaseModel):
    password: Optional[str] = None
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None


class UserLoginRequest(BaseModel):
    username: str
    password: str


class UserLoginResponse(BaseModel):
    token: str
    user: UserResponse
    expires_at: str


class AppSession(BaseModel):
    token: str
    user_id: str
    created_at: Optional[str] = None
    expires_at: str
    revoked_at: Optional[str] = None


class YtmAccountStatus(str, Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    EXPIRED = "EXPIRED"
    PENDING = "PENDING"


class YouTubeMusicAccount(BaseModel):
    id: str
    user_id: str
    account_name: Optional[str] = None
    account_identifier: Optional[str] = None
    auth_reference: Optional[str] = None
    encrypted_credentials: Optional[str] = None
    status: Union[YtmAccountStatus, str] = YtmAccountStatus.PENDING
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_verified_at: Optional[str] = None


class YouTubeMusicAccountResponse(BaseModel):
    id: str
    user_id: str
    account_name: Optional[str] = None
    account_identifier: Optional[str] = None
    status: Union[YtmAccountStatus, str]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_verified_at: Optional[str] = None


class UserSettings(BaseModel):
    user_id: str
    sync_enabled: bool = True
    sync_interval_seconds: int = 300
    auto_upload: bool = False
    download_location: Optional[str] = None
    music_folders: list[str] = []
    scan_interval_minutes: int = 15
    verify_uploads: bool = True
    allow_automatic_replacement: bool = False


class UserSettingsUpdate(BaseModel):
    sync_enabled: Optional[bool] = None
    sync_interval_seconds: Optional[int] = None
    auto_upload: Optional[bool] = None
    download_location: Optional[str] = None
    music_folders: Optional[list[str]] = None
    scan_interval_minutes: Optional[int] = None
    verify_uploads: Optional[bool] = None
    allow_automatic_replacement: Optional[bool] = None


# ==========================================
# Family Mode & Multi-Account Models
# ==========================================

class FamilyRole(str, Enum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"


class FamilyMemberStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"


class FamilyMember(BaseModel):
    id: str
    family_id: str
    user_id: str
    username: Optional[str] = None
    role: FamilyRole = FamilyRole.MEMBER
    status: FamilyMemberStatus = FamilyMemberStatus.ACTIVE
    show_account_in_family: bool = True
    allow_family_uploads: bool = True
    allow_family_playlists: bool = False
    allow_family_sync: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class Family(BaseModel):
    id: str
    name: str
    owner_user_id: str
    owner_username: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    members: list[FamilyMember] = []


class FamilyCreateRequest(BaseModel):
    name: str


class FamilyUpdateRequest(BaseModel):
    name: str


class FamilyMemberAddRequest(BaseModel):
    user_id: str
    role: FamilyRole = FamilyRole.MEMBER


class FamilyTransferOwnershipRequest(BaseModel):
    new_owner_user_id: str
    confirm: bool = False


class FamilyInvitation(BaseModel):
    id: str
    family_id: str
    token: str
    role: FamilyRole = FamilyRole.MEMBER
    created_by_user_id: str
    created_at: Optional[str] = None
    expires_at: str
    accepted_at: Optional[str] = None
    accepted_by_user_id: Optional[str] = None
    is_expired: bool = False


class FamilyInvitationCreateRequest(BaseModel):
    role: FamilyRole = FamilyRole.MEMBER
    ttl_hours: int = 48


class FamilyInvitationInfoResponse(BaseModel):
    family_id: str
    family_name: str
    role: str
    inviter_username: str
    expires_at: str


class FamilyMemberPrivacyUpdate(BaseModel):
    show_account_in_family: Optional[bool] = None
    allow_family_uploads: Optional[bool] = None
    allow_family_playlists: Optional[bool] = None
    allow_family_sync: Optional[bool] = None


class FamilyMemberRoleUpdate(BaseModel):
    role: FamilyRole


class FamilyDashboardMemberItem(BaseModel):
    user_id: str
    username: str
    role: str
    ytm_connected: bool
    account_name: Optional[str] = None
    uploads_count: Optional[int] = None
    allow_family_uploads: bool
    allow_family_playlists: bool
    allow_family_sync: bool


class FamilyDashboardResponse(BaseModel):
    family_id: str
    family_name: str
    my_role: str
    members: list[FamilyDashboardMemberItem]


class SelectableAccountItem(BaseModel):
    user_id: str
    account_id: Optional[str] = None
    username: str
    is_self: bool
    ytm_connected: bool
    account_name: Optional[str] = None
    allow_family_uploads: bool = True
    allow_family_playlists: bool = False
    allow_family_sync: bool = False


class UploadDestinationRequest(BaseModel):
    music_file_ids: list[int]
    destination_user_ids: list[str]
    family_id: Optional[str] = None


class UploadDestinationResponse(BaseModel):
    jobs_created: int
    job_ids: list[int]
    errors: list[str] = []


class FamilyQueueDestinationSubItem(BaseModel):
    job_id: int
    destination_user_id: str
    destination_username: str
    status: str
    attempts: int = 0
    error: Optional[str] = None


class FamilyQueueItem(BaseModel):
    music_file_id: int
    filename: str
    title: Optional[str] = None
    artist: Optional[str] = None
    destinations: list[FamilyQueueDestinationSubItem]


class TrackDestinationDuplicateStatus(BaseModel):
    destination_user_id: str
    destination_username: str
    is_uploaded: bool
    status: str
    error: Optional[str] = None


class FamilyMultiPlaylistRequest(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None
    destination_user_ids: Optional[list[str]] = None
    target_user_ids: Optional[list[str]] = None
    family_id: Optional[str] = None
    description: Optional[str] = None
    video_ids: Optional[list[str]] = None
    source_user_id: Optional[str] = None
    source_playlist_id: Optional[str] = None
    upload_missing_to_targets: bool = False

    @property
    def effective_name(self) -> str:
        return (self.name or self.title or "").strip()

    @property
    def effective_user_ids(self) -> list[str]:
        return self.destination_user_ids or self.target_user_ids or []


class FamilyUploadHistoryItem(BaseModel):
    id: int
    music_file_id: int
    filename: str
    title: Optional[str] = None
    artist: Optional[str] = None
    destination_user_id: str
    destination_username: str
    requested_by_user_id: Optional[str] = None
    requested_by_username: Optional[str] = None
    status: str
    error: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class FamilySyncResponse(BaseModel):
    family_id: str
    results: dict[str, dict]


class FamilyPlaylistItem(BaseModel):
    playlist_id: Union[str, int]
    destination_playlist_id: Optional[str] = None
    source_playlist_id: Optional[str] = None
    name: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    owner_user_id: str
    owner_username: str
    track_count: int = 0
