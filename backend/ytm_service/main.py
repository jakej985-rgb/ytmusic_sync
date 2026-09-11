import asyncio
import logging
import os
import shutil
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request, Depends
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .database import db
from .models import (
    MusicFile, YtmUpload, SyncJob, DashboardStats,
    ScanRequest, AuthSetupRequest, ConnectionStatus, MusicBrainzMatch,
    PlaylistTrackDownloadRequest, PlaylistImportRequest,
    ReplicatedPlaylist, ReplicatedPlaylistCreate, ReplicatedPlaylistUpdate,
    User, UserRole, UserResponse, UserCreate, UserUpdate, UserLoginRequest, UserLoginResponse,
    YouTubeMusicAccountResponse, UserSettings, UserSettingsUpdate,
    Family, FamilyRole, FamilyMemberStatus, FamilyMember,
    FamilyCreateRequest, FamilyUpdateRequest, FamilyMemberAddRequest,
    FamilyTransferOwnershipRequest, FamilyInvitation, FamilyInvitationCreateRequest,
    FamilyInvitationInfoResponse, FamilyMemberPrivacyUpdate, FamilyMemberRoleUpdate,
    FamilyDashboardMemberItem, FamilyDashboardResponse, SelectableAccountItem,
    UploadDestinationRequest, UploadDestinationResponse, FamilyQueueItem,
    TrackDestinationDuplicateStatus, FamilyMultiPlaylistRequest,
    FamilyUploadHistoryItem, FamilySyncResponse, FamilyPlaylistItem,
    PlaylistSyncMissingRequest
)
from .dependencies import require_authenticated_user, require_admin, get_optional_authenticated_user
from .rate_limiter import rate_limit_dependency
from .security import verify_password
from .scanner import scanner
from .ytm_client import ytm_client
from .auth_service import auth_service
from .auth_session import (
    AuthStartRequest,
    AuthStartResponse,
    AuthSessionResponse,
    AuthCompleteRequest,
    AuthCallbackRequest,
    AuthCancelRequest,
)
from .matcher import matcher
from .uploader import queue_manager
from .musicbrainz import musicbrainz_client
from .downloader import (
    download_upload,
    download_catalog_track,
    download_ytm_upload,
    extract_playlist_info,
    commit_staged_file_to_destination
)
from .scanner import write_metadata_tags
from .playlist_downloader import playlist_sync_manager, download_and_upload_playlist_track
from .queue_service import unified_queue_service
from .metadata_tracker import metadata_tracker
from .playlist_replicator import playlist_replicator
from .playlist_watcher import playlist_watcher
from .security import (
    verify_api_key_header,
    validate_fs_path,
    validate_youtube_url,
    validate_auth_origin_url,
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
    # Load persistent safety switch setting (Phase 13)
    saved_replacement_pref = await db.get_setting("allow_automatic_replacement", default=settings.allow_automatic_replacement)
    settings.allow_automatic_replacement = saved_replacement_pref
    # Recover interrupted upload jobs and resume worker if tasks are queued
    await queue_manager.reconcile_and_resume()
    # Start periodic background scanner
    background_scanner_task = asyncio.create_task(_periodic_scan_loop())
    # Start playlist watcher
    playlist_watcher.start()
    yield
    # Shutdown: Clean up background workers
    logger.info("Stopping background upload worker, periodic scanner, and playlist watcher...")
    playlist_watcher.stop()
    if background_scanner_task:
        background_scanner_task.cancel()
    await queue_manager.stop_worker()

app = FastAPI(
    title="Red Music Locker Backend Service",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)

# CORS configuration allowing local network origins, companion extension, and YouTube Music
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|music\.youtube\.com)(:\d+)?|chrome-extension://.*)$",
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

@app.middleware("http")
async def authenticate_api_requests(request: Request, call_next):
    path = request.url.path
    # Allow public health endpoint, application login, and static frontend files
    if (
        path == "/health"
        or path == "/api/auth/login"
        or not path.startswith("/api/")
    ):
        return await call_next(request)
    # Allow CORS preflight OPTIONS requests without credentials
    if request.method == "OPTIONS":
        return await call_next(request)

    auth_hdr = request.headers.get("Authorization")

    # 1. Full access with master API key (maps to admin user)
    if verify_api_key_header(auth_hdr):
        users = await db.list_users()
        admin_user = next((u for u in users if u.role == UserRole.ADMIN), None)
        if admin_user:
            request.state.user = admin_user
        return await call_next(request)

    # 2. Check Scoped Temporary Extension Token (Phase C 4.2)
    bearer_token = None
    if auth_hdr and auth_hdr.strip().lower().startswith("bearer "):
        bearer_token = auth_hdr.strip().split(" ", 1)[1].strip()

    if bearer_token and auth_service.is_valid_extension_token(bearer_token):
        # Scoped extension tokens are strictly restricted to auth callback/completion
        if path == "/api/auth/callback" or (path.startswith("/api/auth/session/") and path.endswith("/complete")):
            return await call_next(request)
        else:
            return JSONResponse(
                status_code=403,
                content={"detail": "Extension token is restricted to authentication callback endpoints only."},
            )

    # 3. Check Active Application Session Token (Phase E & F)
    if bearer_token:
        user = await db.get_user_by_session_token(bearer_token)
        if user:
            request.state.user = user
            return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Invalid or missing API key"},
        headers={"WWW-Authenticate": "Bearer"}
    )



# ============================================================================
# Application Authentication & User Management (Phases D, E, F, G, Q)
# ============================================================================

@app.post("/api/auth/login", response_model=UserLoginResponse, dependencies=[Depends(rate_limit_dependency(5, 60, "login"))])
async def login(req: UserLoginRequest):
    """Authenticate application user with username and password, returning a secure session token."""
    user = await db.get_user_by_username(req.username)
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    session = await db.create_app_session(user.id)
    await db.update_last_login(user.id)

    return UserLoginResponse(
        token=session.token,
        expires_at=session.expires_at,
        user=UserResponse(
            id=user.id,
            username=user.username,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ),
    )

@app.post("/api/auth/logout")
async def logout(request: Request, current_user: User = Depends(require_authenticated_user)):
    """Revoke active application session token."""
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.strip().lower().startswith("bearer "):
        token = auth_hdr.strip().split(" ", 1)[1].strip()
        await db.revoke_app_session(token)
    return {"status": "ok", "message": "Logged out successfully"}

@app.get("/api/auth/me", response_model=UserResponse)
async def get_current_user_profile(current_user: User = Depends(require_authenticated_user)):
    """Get profile of current authenticated user."""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        last_login_at=current_user.last_login_at,
    )

@app.get("/api/admin/users", response_model=list[UserResponse])
async def list_users_admin(admin: User = Depends(require_admin)):
    """Admin-only: list all application users."""
    users = await db.list_users()
    return [
        UserResponse(
            id=u.id,
            username=u.username,
            role=u.role,
            is_active=u.is_active,
            created_at=u.created_at,
            last_login_at=u.last_login_at,
        )
        for u in users
    ]

@app.post("/api/admin/users", response_model=UserResponse)
async def create_user_admin(req: UserCreate, admin: User = Depends(require_admin)):
    """Admin-only: create a new application user."""
    existing = await db.get_user_by_username(req.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"Username '{req.username}' already exists")
    user = await db.create_user(req)
    return UserResponse(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )

@app.put("/api/admin/users/{user_id}", response_model=UserResponse)
async def update_user_admin(user_id: str, req: UserUpdate, admin: User = Depends(require_admin)):
    """Admin-only: update user details, role, or active status."""
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    updated = await db.update_user(user_id, req)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse(
        id=updated.id,
        username=updated.username,
        role=updated.role,
        is_active=updated.is_active,
        created_at=updated.created_at,
        last_login_at=updated.last_login_at,
    )

class UserDeleteRequest(BaseModel):
    password: Optional[str] = None

@app.delete("/api/admin/users/{user_id}")
async def delete_user_admin(
    user_id: str,
    confirm: bool = Query(False),
    admin: User = Depends(require_admin)
):
    """Admin-only: delete an application user with required confirmation."""
    if not confirm:
        raise HTTPException(status_code=400, detail="User deletion must be confirmed with confirm=true")
    if admin.id == user_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own admin account")
    user = await db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.ADMIN:
        admin_count = await db.count_admins()
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last administrator")

    # Clean up user's local config folder
    user_dir = settings.config_dir / "users" / user_id
    if user_dir.exists():
        shutil.rmtree(user_dir, ignore_errors=True)

    await db.delete_user(user_id)
    ytm_client.disconnect_user(user_id)
    logger.info(f"User {user_id} deleted permanently by admin {admin.id}")
    return {"status": "ok", "message": f"User {user_id} deleted"}

@app.delete("/api/users/me")
async def delete_current_user(
    confirm: bool = Query(False),
    req: Optional[UserDeleteRequest] = None,
    current_user: User = Depends(require_authenticated_user)
):
    """Self-delete user account with explicit confirmation."""
    if not confirm:
        raise HTTPException(status_code=400, detail="User deletion must be confirmed with confirm=true")
    if current_user.role == UserRole.ADMIN:
        admin_count = await db.count_admins()
        if admin_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last administrator")
    if req and req.password:
        user_db = await db.get_user_by_id(current_user.id)
        if not user_db or not verify_password(req.password, user_db.password_hash):
            raise HTTPException(status_code=401, detail="Invalid password")

    user_id = current_user.id
    user_dir = settings.config_dir / "users" / user_id
    if user_dir.exists():
        shutil.rmtree(user_dir, ignore_errors=True)

    await db.delete_user(user_id)
    ytm_client.disconnect_user(user_id)
    logger.info(f"User {user_id} deleted their own account permanently")
    return {"status": "ok", "message": "Account deleted successfully"}


# ============================================================================
# Family Mode & Multi-Account Endpoints (Sections 1–40)
# ============================================================================

# --- Account Selector Endpoints (Sections 9, 23) ---

@app.get("/api/accounts", response_model=list[SelectableAccountItem])
async def list_selectable_accounts(current_user: User = Depends(require_authenticated_user)):
    """Return list of accounts available to the user (personal + permitted family accounts)."""
    items = await db.get_permitted_family_accounts(current_user.id)
    return [SelectableAccountItem(**item) for item in items]


@app.get("/api/accounts/{account_id}", response_model=SelectableAccountItem)
async def get_selectable_account(account_id: str, current_user: User = Depends(require_authenticated_user)):
    """Get selectable account info if permitted."""
    items = await db.get_permitted_family_accounts(current_user.id)
    for item in items:
        if item.get("account_id") == account_id or item.get("user_id") == account_id:
            return SelectableAccountItem(**item)
    raise HTTPException(status_code=404, detail="Account not found or access denied")


# --- Family Management Endpoints (Sections 1–8, 23, 27–31) ---

@app.post("/api/families", response_model=Family)
async def create_family(req: FamilyCreateRequest, current_user: User = Depends(require_authenticated_user)):
    """Create a new family. Caller is assigned the OWNER role."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Family name cannot be empty")
    return await db.create_family(name=req.name.strip(), owner_user_id=current_user.id)


@app.get("/api/families", response_model=list[Family])
async def list_user_families(current_user: User = Depends(require_authenticated_user)):
    """List all families the caller belongs to."""
    return await db.get_user_families(current_user.id)


@app.get("/api/families/{family_id}", response_model=Family)
async def get_family_details(family_id: str, current_user: User = Depends(require_authenticated_user)):
    """Get details and members of a family (requires membership)."""
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    member = await db.get_family_member(family_id, current_user.id)
    if not member:
        raise HTTPException(status_code=403, detail="You are not a member of this family")
    return fam


@app.patch("/api/families/{family_id}", response_model=Family)
@app.put("/api/families/{family_id}", response_model=Family)
async def update_family(family_id: str, req: FamilyUpdateRequest, current_user: User = Depends(require_authenticated_user)):
    """Update family name (requires OWNER or ADMIN)."""
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    member = await db.get_family_member(family_id, current_user.id)
    if not member or member.role not in (FamilyRole.OWNER, FamilyRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only family owners and admins can update family details")
    await db.update_family(family_id, req.name.strip())
    return await db.get_family_by_id(family_id)


@app.post("/api/families/{family_id}/transfer")
async def transfer_family_ownership(family_id: str, req: FamilyTransferOwnershipRequest, current_user: User = Depends(require_authenticated_user)):
    """Transfer family ownership to another member with explicit confirmation (Section 30)."""
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Ownership transfer must be confirmed with confirm=true")
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    if fam.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the family owner can transfer ownership")
    try:
        await db.transfer_family_ownership(family_id, req.new_owner_user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "message": f"Ownership transferred to {req.new_owner_user_id}"}


@app.post("/api/families/{family_id}/leave")
async def leave_family(family_id: str, current_user: User = Depends(require_authenticated_user)):
    """Leave family (Section 29: preserves all user data; owner cannot leave without transfer/delete)."""
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    try:
        await db.leave_family(family_id, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "message": "Left family successfully"}


@app.delete("/api/families/{family_id}")
async def delete_family(family_id: str, confirm: bool = Query(False), current_user: User = Depends(require_authenticated_user)):
    """Delete family with confirmation (Section 31: OWNER only; strictly preserves user accounts)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="Family deletion must be confirmed with confirm=true")
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    if fam.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the family owner can delete the family")
    await db.delete_family(family_id)
    return {"status": "success", "message": "Family deleted successfully"}


# --- Member Management & Invitations (Sections 27–28) ---

@app.post("/api/families/{family_id}/members", response_model=FamilyMember)
async def add_family_member_direct(family_id: str, req: FamilyMemberAddRequest, current_user: User = Depends(require_authenticated_user)):
    """Direct add member (Admin/Owner shortcut)."""
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    member = await db.get_family_member(family_id, current_user.id)
    if not member or member.role not in (FamilyRole.OWNER, FamilyRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only family owners and admins can add members")
    target_user = await db.get_user_by_id(req.user_id) or await db.get_user_by_username(req.user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Target user not found")
    existing = await db.get_family_member(family_id, target_user.id)
    if existing:
        raise HTTPException(status_code=409, detail="User is already a member of this family")
    return await db.add_family_member(family_id=family_id, user_id=target_user.id, role=req.role.value)


@app.delete("/api/families/{family_id}/members/{user_id}")
async def remove_family_member(family_id: str, user_id: str, current_user: User = Depends(require_authenticated_user)):
    """Remove member from family (Section 28: OWNER/ADMIN or self; preserves user data)."""
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    caller_mem = await db.get_family_member(family_id, current_user.id)
    if not caller_mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")
    if user_id != current_user.id:
        if caller_mem.role not in (FamilyRole.OWNER, FamilyRole.ADMIN):
            raise HTTPException(status_code=403, detail="Only owners and admins can remove other members")
        if fam.owner_user_id == user_id:
            raise HTTPException(status_code=400, detail="Cannot remove the family owner")
    else:
        if fam.owner_user_id == current_user.id:
            raise HTTPException(status_code=400, detail="Family owner cannot leave without transferring ownership or deleting the family")

    await db.remove_family_member(family_id, user_id)
    return {"status": "success", "message": "Member removed from family"}


@app.patch("/api/families/{family_id}/members/{user_id}/permissions")
@app.put("/api/families/{family_id}/members/{user_id}/privacy")
async def update_member_privacy(family_id: str, user_id: str, req: FamilyMemberPrivacyUpdate, current_user: User = Depends(require_authenticated_user)):
    """Update member privacy flags (Section 6, 26: SELF ONLY)."""
    if current_user.id != user_id:
        raise HTTPException(status_code=403, detail="You can only modify your own family privacy settings")
    member = await db.get_family_member(family_id, user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Family member not found")
    await db.update_family_member_privacy(
        family_id=family_id,
        user_id=user_id,
        show_account_in_family=req.show_account_in_family,
        allow_family_uploads=req.allow_family_uploads,
        allow_family_playlists=req.allow_family_playlists,
        allow_family_sync=req.allow_family_sync
    )
    return await db.get_family_member(family_id, user_id)


@app.put("/api/families/{family_id}/members/{user_id}/role", response_model=FamilyMember)
async def update_member_role(family_id: str, user_id: str, req: FamilyMemberRoleUpdate, current_user: User = Depends(require_authenticated_user)):
    """Update member role (OWNER ONLY; cannot demote owner without transfer)."""
    fam = await db.get_family_by_id(family_id)
    if not fam or fam.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the family owner can change member roles")
    if user_id == current_user.id and req.role != FamilyRole.OWNER:
        raise HTTPException(status_code=400, detail="Owner must use transfer endpoint to change ownership")
    await db.update_family_member_role(family_id, user_id, req.role.value)
    return await db.get_family_member(family_id, user_id)


# --- Invitation Tokens (Section 27) ---

@app.post("/api/families/{family_id}/invitations", response_model=FamilyInvitation)
async def create_family_invitation(family_id: str, req: FamilyInvitationCreateRequest, current_user: User = Depends(require_authenticated_user)):
    """Create short-lived single-use invitation token (OWNER/ADMIN)."""
    caller_mem = await db.get_family_member(family_id, current_user.id)
    if not caller_mem or caller_mem.role not in (FamilyRole.OWNER, FamilyRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only family owners and admins can invite members")
    return await db.create_family_invitation(
        family_id=family_id,
        created_by_user_id=current_user.id,
        role=req.role.value,
        ttl_hours=req.ttl_hours
    )


@app.get("/api/families/{family_id}/invitations", response_model=list[FamilyInvitation])
async def list_family_invitations(family_id: str, current_user: User = Depends(require_authenticated_user)):
    caller_mem = await db.get_family_member(family_id, current_user.id)
    if not caller_mem or caller_mem.role not in (FamilyRole.OWNER, FamilyRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only family owners and admins can view invitations")
    return await db.list_family_invitations(family_id)


@app.delete("/api/families/{family_id}/invitations/{invitation_id}")
async def revoke_family_invitation(family_id: str, invitation_id: str, current_user: User = Depends(require_authenticated_user)):
    caller_mem = await db.get_family_member(family_id, current_user.id)
    if not caller_mem or caller_mem.role not in (FamilyRole.OWNER, FamilyRole.ADMIN):
        raise HTTPException(status_code=403, detail="Only family owners and admins can revoke invitations")
    await db.revoke_family_invitation(family_id, invitation_id)
    return {"status": "success", "message": "Invitation revoked"}


@app.get("/api/invitations/{token}", response_model=FamilyInvitationInfoResponse)
async def inspect_invitation(token: str):
    """Public inspection of invitation without disclosing internal IDs."""
    inv = await db.get_family_invitation_by_token(token)
    if not inv or inv.is_expired:
        raise HTTPException(status_code=404, detail="Invitation is invalid or has expired")
    fam = await db.get_family_by_id(inv.family_id)
    inviter = await db.get_user_by_id(inv.created_by_user_id)
    return FamilyInvitationInfoResponse(
        family_id=inv.family_id,
        family_name=fam.name if fam else "Family",
        role=inv.role.value,
        inviter_username=inviter.username if inviter else "Admin",
        expires_at=inv.expires_at
    )


@app.post("/api/invitations/{token}/accept", response_model=FamilyMember)
async def accept_invitation(token: str, current_user: User = Depends(require_authenticated_user)):
    """Accept invitation and join family."""
    try:
        return await db.accept_family_invitation(token, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --- Family Dashboard (Section 8) ---

@app.get("/api/families/{family_id}/dashboard", response_model=FamilyDashboardResponse)
async def get_family_dashboard(family_id: str, current_user: User = Depends(require_authenticated_user)):
    """Family dashboard aggregating permitted member account statuses (Section 8)."""
    fam = await db.get_family_by_id(family_id)
    if not fam:
        raise HTTPException(status_code=404, detail="Family not found")
    caller_mem = await db.get_family_member(family_id, current_user.id)
    if not caller_mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")

    members = await db.get_family_members(family_id)
    member_items = []
    for m in members:
        is_self = (m.user_id == current_user.id)
        if not is_self and not m.show_account_in_family:
            # Privacy: hidden account
            member_items.append(FamilyDashboardMemberItem(
                user_id=m.user_id,
                username=m.username or "User",
                role=m.role.value,
                ytm_connected=False,
                account_name=None,
                uploads_count=None,
                allow_family_uploads=False,
                allow_family_playlists=False,
                allow_family_sync=False
            ))
        else:
            acc = await db.get_ytm_account(m.user_id)
            uploads_count = None
            if is_self or m.show_account_in_family:
                counts = await db.get_dashboard_counts(user_id=m.user_id)
                uploads_count = counts.get("ytm_uploads_count", 0)
            member_items.append(FamilyDashboardMemberItem(
                user_id=m.user_id,
                username=m.username or "User",
                role=m.role.value,
                ytm_connected=bool(acc and acc.status == "CONNECTED"),
                account_name=acc.account_name if acc else None,
                uploads_count=uploads_count,
                allow_family_uploads=m.allow_family_uploads,
                allow_family_playlists=m.allow_family_playlists,
                allow_family_sync=m.allow_family_sync
            ))

    return FamilyDashboardResponse(
        family_id=fam.id,
        family_name=fam.name,
        my_role=caller_mem.role.value,
        members=member_items
    )


# --- Multi-Account Uploads & Queue (Sections 10–16, 24, 32–34) ---

@app.post("/api/uploads/destinations", response_model=UploadDestinationResponse)
async def upload_to_destinations(req: UploadDestinationRequest, current_user: User = Depends(require_authenticated_user)):
    """Create independent upload jobs per selected destination account with strict validation (Sections 11, 12, 15, 24)."""
    if not req.music_file_ids:
        raise HTTPException(status_code=400, detail="No files provided for upload")
    if not req.destination_user_ids:
        raise HTTPException(status_code=400, detail="No destination accounts selected")

    created_job_ids = []
    errors = []

    for dest_id in req.destination_user_ids:
        allowed, reason = await db.validate_upload_destination_permission(current_user.id, dest_id)
        if not allowed:
            dest_user = await db.get_user_by_id(dest_id)
            name = dest_user.username if dest_user else dest_id
            errors.append(f"{name}: {reason}")
            continue

        dest_acc = await db.get_ytm_account(dest_id)
        acc_id = dest_acc.id if dest_acc else None

        for fid in req.music_file_ids:
            try:
                job_id = await db.create_sync_job(
                    music_file_id=fid,
                    user_id=dest_id,
                    destination_user_id=dest_id,
                    requested_by_user_id=current_user.id,
                    family_id=req.family_id,
                    youtube_music_account_id=acc_id
                )
                created_job_ids.append(job_id)
            except Exception as e:
                errors.append(f"File {fid} for user {dest_id}: {str(e)}")

    if not created_job_ids and errors:
        is_forbidden = any("not in your family" in err or "disabled family uploads" in err for err in errors)
        raise HTTPException(status_code=403 if is_forbidden else 400, detail="; ".join(errors))

    return UploadDestinationResponse(
        jobs_created=len(created_job_ids),
        job_ids=created_job_ids,
        errors=errors
    )


@app.get("/api/tracks/{file_id}/destinations-status", response_model=list[TrackDestinationDuplicateStatus])
async def get_track_destinations_status(file_id: int, current_user: User = Depends(require_authenticated_user)):
    """Check duplicate and upload status per destination account (Section 34)."""
    accounts = await db.get_permitted_family_accounts(current_user.id)
    results = []
    for acc in accounts:
        dest_uid = acc["user_id"]
        dup_info = await db.check_track_duplicate_for_user(file_id, dest_uid)
        results.append(TrackDestinationDuplicateStatus(
            destination_user_id=dest_uid,
            destination_username=acc["username"],
            is_uploaded=dup_info["is_uploaded"],
            status=dup_info["status"],
            error=dup_info.get("error")
        ))
    return results


@app.get("/api/families/{family_id}/queue", response_model=list[FamilyQueueItem])
async def get_family_queue(family_id: str, current_user: User = Depends(require_authenticated_user)):
    """Grouped multi-account upload queue (Section 33)."""
    mem = await db.get_family_member(family_id, current_user.id)
    if not mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")
    items = await db.get_family_queue_grouped(family_id, current_user.id)
    return [FamilyQueueItem(**item) for item in items]


@app.get("/api/families/{family_id}/history", response_model=list[FamilyUploadHistoryItem])
async def get_family_history(family_id: str, limit: int = Query(50), current_user: User = Depends(require_authenticated_user)):
    """Combined upload history identifying destination and requesting users (Section 17)."""
    mem = await db.get_family_member(family_id, current_user.id)
    if not mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")
    items = await db.get_family_upload_history(family_id, current_user.id, limit)
    return [FamilyUploadHistoryItem(**item) for item in items]


@app.post("/api/families/{family_id}/sync", response_model=FamilySyncResponse)
async def trigger_family_sync(family_id: str, current_user: User = Depends(require_authenticated_user)):
    """Trigger independent sync for each permitted family account with fault isolation (Section 18)."""
    mem = await db.get_family_member(family_id, current_user.id)
    if not mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")

    permitted = await db.get_family_permitted_sync_members(family_id)
    results = {}
    for p in permitted:
        try:
            counts = await db.get_dashboard_counts(user_id=p.user_id)
            results[p.username or p.user_id] = {
                "status": "started",
                "in_queue": counts.get("in_queue_count", 0),
                "error": None
            }
        except Exception as e:
            results[p.username or p.user_id] = {
                "status": "failed",
                "in_queue": 0,
                "error": str(e)
            }
    return FamilySyncResponse(family_id=family_id, results=results)


@app.get("/api/families/{family_id}/playlists", response_model=list[FamilyPlaylistItem])
async def list_family_shared_playlists(family_id: str, current_user: User = Depends(require_authenticated_user)):
    """Read-only view of playlists shared by family members (Section 19)."""
    mem = await db.get_family_member(family_id, current_user.id)
    if not mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")
    items = await db.get_family_permitted_playlists(family_id, current_user.id)
    return [FamilyPlaylistItem(**item) for item in items]


@app.post("/api/families/{family_id}/playlists/multi")
async def create_multi_account_playlists(family_id: str, req: FamilyMultiPlaylistRequest, current_user: User = Depends(require_authenticated_user)):
    """Create or clone independent playlist on each selected family account (Section 35)."""
    mem = await db.get_family_member(family_id, current_user.id)
    if not mem:
        raise HTTPException(status_code=403, detail="You are not a member of this family")

    user_ids = req.effective_user_ids
    if not user_ids:
        raise HTTPException(status_code=400, detail="At least one target user must be selected")

    # Flow A: Clone an existing YouTube Music playlist
    if req.source_playlist_id:
        source_uid = req.source_user_id or current_user.id
        if source_uid != current_user.id:
            permitted_accounts = await db.get_permitted_family_accounts(current_user.id)
            can_access = any(a.get("user_id") == source_uid and a.get("allow_family_playlists") for a in permitted_accounts)
            if not can_access:
                raise HTTPException(status_code=403, detail="Access to source family member's playlists is not permitted")

        try:
            details = await ytm_client.get_playlist_details(req.source_playlist_id, user_id=source_uid)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch source playlist details: {e}")

        source_title = details.get("title", f"Playlist {req.source_playlist_id}")
        name = req.effective_name or source_title
        tracks = details.get("tracks", [])

        permitted_accounts = await db.get_permitted_family_accounts(current_user.id)
        permitted_lookup = {a.get("user_id"): a for a in permitted_accounts}

        created_replicas = []
        for uid in user_ids:
            if uid != current_user.id:
                account_info = permitted_lookup.get(uid)
                if not account_info or not account_info.get("allow_family_playlists"):
                    logger.warning(f"User {uid} does not permit family playlists from {current_user.id}")
                    continue

            existing = await db.get_replicated_playlist_by_source_id(req.source_playlist_id, user_id=uid)
            if existing:
                if not existing.destination_playlist_id:
                    try:
                        ownership_desc = (
                            f"Automated 1:1 Locker-Only Replica of '{source_title}'. "
                            f"[managed_by=ytmusic_sync;replica_mode=locker_only;source_playlist_id={req.source_playlist_id}]"
                        )
                        dest_id = await ytm_client.create_playlist(
                            title=name,
                            description=ownership_desc,
                            user_id=uid
                        )
                        await db.update_replicated_playlist(existing.id, user_id=uid, destination_playlist_id=dest_id)
                        existing.destination_playlist_id = dest_id
                    except Exception as ex:
                        logger.warning(f"Could not create YTM destination playlist for {uid}: {ex}")
                created_replicas.append(existing)
            else:
                dest_id = ""
                try:
                    ownership_desc = (
                        f"Automated 1:1 Locker-Only Replica of '{source_title}'. "
                        f"[managed_by=ytmusic_sync;replica_mode=locker_only;source_playlist_id={req.source_playlist_id}]"
                    )
                    dest_id = await ytm_client.create_playlist(
                        title=name,
                        description=ownership_desc,
                        user_id=uid
                    )
                except Exception as ex:
                    logger.warning(f"Could not create YTM destination playlist for {uid}: {ex}")
                    dest_id = f"local_{secrets.token_hex(6)}"

                new_id = await db.create_replicated_playlist(
                    source_playlist_id=req.source_playlist_id,
                    source_playlist_name=source_title,
                    destination_playlist_id=dest_id,
                    destination_playlist_name=name,
                    enabled=True,
                    sync_interval_seconds=300,
                    user_id=uid
                )
                created = await db.get_replicated_playlist(new_id, user_id=uid)
                if created:
                    created_replicas.append(created)

        # Trigger immediate reconciliation for target accounts
        for rep in created_replicas:
            try:
                asyncio.create_task(playlist_replicator.reconcile_playlist(rep.id, dry_run=False))
            except Exception as ex:
                logger.warning(f"Could not trigger background reconciliation for replica {rep.id}: {ex}")

        # If user also requested uploading missing tracks to targets
        if req.upload_missing_to_targets and created_replicas:
            try:
                valid_uids = [r.user_id for r in created_replicas if r.user_id]
                playlist_sync_manager.start_sync(
                    playlist_id=req.source_playlist_id,
                    playlist_title=name,
                    tracks_to_sync=tracks,
                    destination_user_ids=valid_uids
                )
            except Exception as ex:
                logger.warning(f"Could not launch multi-account track upload sync: {ex}")

        return {
            "status": "success",
            "mode": "clone",
            "source_playlist_id": req.source_playlist_id,
            "created_count": len(created_replicas),
            "playlists": [
                {
                    "replicated_playlist_id": r.id,
                    "destination_user_id": r.user_id,
                    "name": r.destination_playlist_name
                }
                for r in created_replicas
            ]
        }

    # Flow B: Create empty shared multi-account playlist
    name = req.effective_name
    if not name:
        raise HTTPException(status_code=400, detail="Playlist name cannot be empty")

    created = await db.create_multi_account_playlists(
        playlist_name=name,
        destination_user_ids=user_ids,
        created_by_user_id=current_user.id,
        family_id=family_id
    )
    return {"status": "success", "mode": "create", "created_count": len(created), "playlists": created}


# ============================================================================
# YouTube Music Account Status & Settings (Phases H, Q, S, T)
# ============================================================================

@app.get("/api/ytm/account", response_model=Optional[YouTubeMusicAccountResponse])
async def get_ytm_account(current_user: User = Depends(require_authenticated_user)):
    """Get linked YouTube Music account for current user."""
    account = await db.get_ytm_account_by_user_id(current_user.id)
    if not account:
        return None
    return YouTubeMusicAccountResponse(
        id=account.id,
        user_id=account.user_id,
        account_name=account.account_name,
        account_identifier=account.account_identifier,
        status=account.status,
        created_at=account.created_at,
        updated_at=account.updated_at,
        last_verified_at=account.last_verified_at,
    )

@app.post("/api/ytm/disconnect")
async def disconnect_ytm_account(current_user: User = Depends(require_authenticated_user)):
    """Disconnect linked YouTube Music account for current user."""
    res = await auth_service.disconnect(user_id=current_user.id)
    return res

@app.get("/api/settings", response_model=UserSettings)
async def get_user_settings(current_user: User = Depends(require_authenticated_user)):
    """Get settings for current user."""
    return await db.get_user_settings(current_user.id)

@app.put("/api/settings", response_model=UserSettings)
async def update_user_settings_put(req: UserSettingsUpdate, current_user: User = Depends(require_authenticated_user)):
    """Update settings for current user."""
    return await db.update_user_settings(current_user.id, req)

@app.post("/api/settings")
async def update_user_settings_post(req: UserSettingsUpdate, current_user: User = Depends(require_authenticated_user)):
    """Update settings for current user via POST (backward compatible)."""
    await db.update_user_settings(current_user.id, req)
    return {"status": "success"}


@app.get("/api/status", response_model=DashboardStats)
async def get_dashboard_status(current_user: User = Depends(require_authenticated_user)):
    counts = await db.get_dashboard_counts(user_id=current_user.id)
    conn = await ytm_client.test_connection(user_id=current_user.id)
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
async def get_auth_status(current_user: User = Depends(require_authenticated_user)):
    res = await ytm_client.test_connection(user_id=current_user.id)
    return ConnectionStatus(
        connected=res["connected"],
        message=res["message"],
        user_name=res.get("user_name")
    )

@app.post("/api/auth/start", response_model=AuthStartResponse, dependencies=[Depends(rate_limit_dependency(10, 60, "auth_start"))])
async def start_auth_session(request: Request, req: Optional[AuthStartRequest] = None, current_user: User = Depends(require_authenticated_user)):
    """Start a new short-lived, single-use authentication session with validated origin."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host")) or request.url.netloc

    raw_origin = req.origin_url if (req and req.origin_url) else None
    if raw_origin:
        try:
            origin_url = validate_auth_origin_url(raw_origin, request_host=host, request_proto=proto)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid or unapproved origin URL: {e}")
    else:
        if host:
            origin_url = f"{proto}://{host}"
        else:
            origin_url = str(request.base_url).rstrip("/")

    client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else None)
    target_user_id = req.user_id if (req and req.user_id and current_user.role == UserRole.ADMIN) else current_user.id
    return await auth_service.start_session(origin_url=origin_url, client_ip=client_ip, user_id=target_user_id)

@app.get("/api/auth/session/{session_id}", response_model=AuthSessionResponse)
async def get_auth_session(session_id: str):
    """Check the status of an ongoing authentication session."""
    session = await auth_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Authentication session not found")
    return session

@app.post("/api/auth/session/{session_id}/complete", response_model=AuthSessionResponse, dependencies=[Depends(rate_limit_dependency(10, 60, "auth_complete"))])
async def complete_auth_session(session_id: str, req: AuthCompleteRequest):
    """Receive authentication headers from companion extension or helper and validate them."""
    if not req.raw_headers.strip():
        raise HTTPException(status_code=400, detail="Headers cannot be empty")
    try:
        session = await auth_service.complete_session(session_id, req.raw_headers)
        return session
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to complete authentication: {e}")

@app.post("/api/auth/callback", response_model=AuthSessionResponse, dependencies=[Depends(rate_limit_dependency(10, 60, "auth_callback"))])
async def auth_callback_post(req: AuthCallbackRequest):
    """Callback endpoint for companion extensions/helpers submitting credentials."""
    if not req.raw_headers.strip():
        raise HTTPException(status_code=400, detail="Headers cannot be empty")
    try:
        session = await auth_service.complete_session(req.session_id, req.raw_headers)
        return session
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to complete authentication: {e}")

@app.get("/api/auth/callback", response_class=HTMLResponse)
async def auth_callback_get(session_id: Optional[str] = None):
    """Friendly browser landing page after authentication callback."""
    if not session_id:
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>Red Music Locker - Missing Session</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #121212; color: #fff; }
.card { background: #1e1e1e; padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 420px; }
h2 { color: #f44336; margin-top: 0; }
p { color: #aaa; line-height: 1.5; }
</style>
</head>
<body>
<div class="card">
  <h2>Invalid Request</h2>
  <p>Missing session parameter. Please return to Red Music Locker.</p>
</div>
</body>
</html>""",
            status_code=400
        )

    session = await auth_service.get_session(session_id)
    if not session or not session.connected:
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head><title>Red Music Locker - Authentication Pending</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #121212; color: #fff; }
.card { background: #1e1e1e; padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 420px; }
h2 { color: #f44336; margin-top: 0; }
p { color: #aaa; line-height: 1.5; }
</style>
</head>
<body>
<div class="card">
  <h2>Authentication Pending or Incomplete</h2>
  <p>The authentication session has not completed or has expired. Please return to Red Music Locker and try again.</p>
</div>
</body>
</html>""",
            status_code=400
        )

    user_disp = session.user_name or "Connected Account"
    return HTMLResponse(
        content=f"""<!DOCTYPE html>
<html>
<head><title>Red Music Locker - Connected</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; background: #121212; color: #fff; }}
.card {{ background: #1e1e1e; padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 420px; }}
h2 {{ color: #4caf50; margin-top: 0; }}
p {{ color: #ccc; line-height: 1.5; }}
.user {{ font-weight: bold; color: #fff; }}
</style>
</head>
<body>
<div class="card">
  <h2>✓ YouTube Music Connected</h2>
  <p>Successfully linked account: <span class="user">{user_disp}</span></p>
  <p>You can now safely close this window and return to Red Music Locker.</p>
</div>
</body>
</html>""",
        status_code=200
    )

@app.post("/api/auth/session/{session_id}/cancel", response_model=AuthSessionResponse)
async def cancel_auth_session(session_id: str):
    """Cancel an active authentication session."""
    session = await auth_service.cancel_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Authentication session not found")
    return session

@app.post("/api/auth/cancel", response_model=AuthSessionResponse)
async def cancel_auth_post(req: AuthCancelRequest):
    """Cancel an active authentication session via POST /api/auth/cancel."""
    session = await auth_service.cancel_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Authentication session not found")
    return session

@app.post("/api/auth/disconnect", response_model=ConnectionStatus)
async def disconnect_auth(current_user: User = Depends(require_authenticated_user)):
    """Safely disconnect YouTube Music account and remove stored credentials."""
    res = await auth_service.disconnect(user_id=current_user.id)
    return ConnectionStatus(
        connected=res["connected"],
        message=res["message"],
        user_name=res.get("user_name")
    )

@app.post("/api/auth/setup", response_model=ConnectionStatus)
async def setup_auth(req: AuthSetupRequest, current_user: User = Depends(require_authenticated_user)):
    """Direct/Developer setup: Parse raw headers and store credentials."""
    if not req.raw_headers.strip():
        raise HTTPException(status_code=400, detail="Headers cannot be empty")
    try:
        res = await ytm_client.setup_auth(req.raw_headers, user_id=current_user.id)
        if res.get("connected"):
            await db.create_or_update_ytm_account(
                user_id=current_user.id,
                account_name=res.get("user_name"),
                status="ACTIVE"
            )
        return ConnectionStatus(
            connected=res["connected"],
            message=res["message"],
            user_name=res.get("user_name")
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to setup authentication: {e}")

@app.post("/api/auth/test", response_model=ConnectionStatus)
async def test_auth(current_user: User = Depends(require_authenticated_user)):
    res = await ytm_client.test_connection(user_id=current_user.id)
    return ConnectionStatus(
        connected=res["connected"],
        message=res["message"],
        user_name=res.get("user_name")
    )

@app.get("/api/ytm/playlists")
async def get_ytm_playlists(user_id: Optional[str] = None, current_user: User = Depends(require_authenticated_user)):
    target_uid = user_id or current_user.id
    if target_uid != current_user.id:
        permitted_accounts = await db.get_permitted_family_accounts(current_user.id)
        permitted = any(a.get("user_id") == target_uid and a.get("allow_family_playlists") for a in permitted_accounts)
        if not permitted:
            raise HTTPException(status_code=403, detail="Access to this family member's playlists is not permitted")

    if not ytm_client.is_auth_configured(user_id=target_uid):
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated for this account")
    try:
        playlists = await ytm_client.get_playlists(user_id=target_uid)
        return playlists
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlists: {e}")

@app.get("/api/ytm/playlists/sync-status")
async def get_playlist_sync_status():
    """Get active playlist download/sync progress."""
    return playlist_sync_manager.status

@app.post("/api/ytm/playlists/cancel-sync")
async def cancel_playlist_sync():
    """Cancel any active background playlist download/sync."""
    return playlist_sync_manager.cancel_sync()

@app.post("/api/ytm/playlists/download-track")
async def download_playlist_track_endpoint(
    req: PlaylistTrackDownloadRequest,
    current_user: User = Depends(require_authenticated_user),
):
    """Download, tag, and upload a single playlist track to YouTube Music locker."""
    dest_path = None
    if req.destination_dir:
        try:
            dest_path = validate_fs_path(req.destination_dir, allow_create_in_parent=True)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid destination_dir: {e}")

    if not ytm_client.is_auth_configured(user_id=current_user.id):
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
async def import_playlist_url_endpoint(
    req: PlaylistImportRequest,
    current_user: User = Depends(require_authenticated_user),
):
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
async def get_ytm_playlist_details(
    playlist_id: str,
    refresh: bool = False,
    current_user: User = Depends(require_authenticated_user),
):
    if not ytm_client.is_auth_configured(user_id=current_user.id):
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")
    try:
        if refresh:
            try:
                await ytm_client.fetch_and_cache_uploads(user_id=current_user.id)
            except Exception as ex:
                logger.warning(f"Failed to refresh uploads from YTM: {ex}")
        details = await ytm_client.get_playlist_details(playlist_id, user_id=current_user.id)
        return details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch playlist details: {e}")

@app.post("/api/ytm/playlists/{playlist_id}/sync-missing")
async def sync_missing_playlist_tracks(
    playlist_id: str,
    destination_dir: Optional[str] = None,
    body: Optional[PlaylistSyncMissingRequest] = None,
    current_user: User = Depends(require_authenticated_user),
):
    """Start background sync for tracks in a playlist missing from uploads (supports multi-account destination)."""
    dest_path = None
    target_dir = (body.destination_dir if body and body.destination_dir else destination_dir)
    if target_dir:
        try:
            dest_path = str(validate_fs_path(target_dir, allow_create_in_parent=True))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid destination_dir: {e}")

    if not ytm_client.is_auth_configured(user_id=current_user.id):
        raise HTTPException(status_code=400, detail="YouTube Music not authenticated")

    try:
        details = await ytm_client.get_playlist_details(playlist_id, user_id=current_user.id)
        tracks = details.get("tracks", [])

        # Validate destination user IDs
        raw_target_uids = (body.destination_user_ids if body and body.destination_user_ids else [current_user.id])
        target_uids = []
        permitted_accounts = await db.get_permitted_family_accounts(current_user.id)
        permitted_lookup = {a.get("user_id"): a for a in permitted_accounts}

        for uid in raw_target_uids:
            if uid == current_user.id:
                target_uids.append(uid)
            else:
                acc = permitted_lookup.get(uid)
                if acc and (acc.get("allow_family_uploads") or acc.get("allow_family_sync")):
                    target_uids.append(uid)

        if not target_uids:
            target_uids = [current_user.id]

        # Check which tracks are missing from ANY of the target users' lockers
        missing = []
        for t in tracks:
            if t.get("is_duplicate"):
                continue
            vid = t.get("video_id")
            tit = t.get("title")
            art = t.get("artist")
            is_missing = False
            for uid in target_uids:
                if uid == current_user.id and t.get("in_uploads"):
                    has_it = True
                else:
                    has_it = await db.get_ytm_upload_by_video_id(vid, user_id=uid) or await db.find_ytm_upload_by_title_artist(tit, art, user_id=uid)
                if not has_it:
                    is_missing = True
                    break
            if is_missing:
                missing.append(t)

        if not missing:
            return {"status": "ok", "message": "All tracks in this playlist are already in the cloud uploads of the selected accounts!", "queued": 0}

        status = playlist_sync_manager.start_sync(
            playlist_id=playlist_id,
            playlist_title=details.get("title", "Playlist"),
            tracks_to_sync=missing,
            destination_dir=dest_path,
            destination_user_ids=target_uids
        )
        return {
            "status": "started",
            "message": f"Started syncing {len(missing)} missing tracks from '{details.get('title')}' to {len(target_uids)} account(s)",
            "queued": len(missing),
            "target_user_ids": target_uids,
            "sync_status": status
        }
    except RuntimeError as re:
        raise HTTPException(status_code=409, detail=str(re))
    except Exception as e:
        logger.exception(f"Failed to sync missing playlist tracks: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start playlist sync: {e}")


# ============================================================================
# Replicated Playlists (1:1 Locker-Only Replica Engine)
# ============================================================================

@app.get("/api/replicated-playlists")
async def list_replicated_playlists(current_user: User = Depends(require_authenticated_user)):
    """List all configured replicated playlist watchers with current status."""
    try:
        replicas = await db.get_replicated_playlists(user_id=current_user.id)
        results = []
        for r in replicas:
            results.append(r.model_dump())
        return results
    except Exception as e:
        logger.exception(f"Failed to list replicated playlists: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list replicated playlists: {e}")


@app.post("/api/replicated-playlists", dependencies=[Depends(rate_limit_dependency(20, 60, "playlist_create", use_user_id=True))])
async def create_replicated_playlist(req: ReplicatedPlaylistCreate, current_user: User = Depends(require_authenticated_user)):
    """Configure a new replicated playlist watcher (supports multi-account target selection)."""
    try:
        # If source name not supplied, fetch it from YouTube Music
        source_name = req.source_playlist_name
        if not source_name:
            try:
                details = await ytm_client.get_playlist_details(req.source_playlist_id, user_id=current_user.id)
                source_name = details.get("title", f"Playlist {req.source_playlist_id}")
            except Exception:
                source_name = f"Playlist {req.source_playlist_id}"

        dest_name = req.destination_playlist_name or f"{source_name} - Locker"
        dest_id = req.destination_playlist_id or ""

        # Determine target user IDs (ignore spoofed/payload user_id; multi-target replication uses target_user_ids)
        raw_target_uids = req.target_user_ids if req.target_user_ids else [current_user.id]
        created_replicas = []

        permitted_accounts = await db.get_permitted_family_accounts(current_user.id)
        permitted_lookup = {a.get("user_id"): a for a in permitted_accounts}

        for uid in raw_target_uids:
            # Validate permissions if target is another user
            if uid != current_user.id:
                account_info = permitted_lookup.get(uid)
                if not account_info or not account_info.get("allow_family_playlists"):
                    logger.warning(f"User {uid} does not permit family playlists from {current_user.id}")
                    continue

            existing = await db.get_replicated_playlist_by_source_id(req.source_playlist_id, user_id=uid)
            if existing:
                created_replicas.append(existing)
                continue

            new_id = await db.create_replicated_playlist(
                source_playlist_id=req.source_playlist_id,
                source_playlist_name=source_name,
                destination_playlist_id=dest_id,
                destination_playlist_name=dest_name,
                enabled=req.enabled,
                sync_interval_seconds=req.sync_interval_seconds,
                user_id=uid
            )
            created = await db.get_replicated_playlist(new_id, user_id=uid)
            if created:
                created_replicas.append(created)

        # Trigger immediate reconciliation for target accounts
        for rep in created_replicas:
            try:
                asyncio.create_task(playlist_replicator.reconcile_playlist(rep.id, dry_run=False))
            except Exception as ex:
                logger.warning(f"Could not trigger background reconciliation for replica {rep.id}: {ex}")

        # If user also requested uploading missing tracks to targets
        if req.upload_missing_to_targets and created_replicas:
            try:
                details = await ytm_client.get_playlist_details(req.source_playlist_id, user_id=current_user.id)
                tracks = details.get("tracks", [])
                valid_uids = [r.user_id for r in created_replicas if r.user_id]
                playlist_sync_manager.start_sync(
                    playlist_id=req.source_playlist_id,
                    playlist_title=source_name,
                    tracks_to_sync=tracks,
                    destination_user_ids=valid_uids
                )
            except Exception as ex:
                logger.warning(f"Could not launch multi-account track upload sync: {ex}")

        if not created_replicas:
            raise HTTPException(status_code=400, detail="No replicated playlists could be created for the selected accounts")

        # Return primary replica for current user if present, else first created
        primary = next((r for r in created_replicas if r.user_id == current_user.id), created_replicas[0])
        res = primary.model_dump()
        res["created_replicas_count"] = len(created_replicas)
        res["target_user_ids"] = [r.user_id for r in created_replicas]
        return res
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to create replicated playlist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create replicated playlist: {e}")


@app.get("/api/replicated-playlists/{replicated_id}")
async def get_replicated_playlist(replicated_id: int, current_user: User = Depends(require_authenticated_user)):
    """Get details, current configuration, and status of a replicated playlist."""
    config = await db.get_replicated_playlist(replicated_id)
    if not config or (current_user.role != UserRole.ADMIN and config.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Replicated playlist not found")

    # Run preview/dry-run to return stats (source count, locker matches, excluded count)
    try:
        preview = await playlist_replicator.reconcile_playlist(replicated_id, dry_run=True, config=config)
    except Exception as e:
        logger.warning(f"Could not calculate preview for replica {replicated_id}: {e}")
        preview = None

    res = dict(preview) if preview else {}
    res["config"] = config.model_dump()
    res["preview"] = preview
    return res


@app.put("/api/replicated-playlists/{replicated_id}")
async def update_replicated_playlist(replicated_id: int, req: ReplicatedPlaylistUpdate, current_user: User = Depends(require_authenticated_user)):
    """Update settings for an existing replicated playlist watcher."""
    config = await db.get_replicated_playlist(replicated_id)
    if not config or (current_user.role != UserRole.ADMIN and config.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Replicated playlist not found")

    update_dict = {k: v for k, v in req.model_dump().items() if v is not None}
    target_user_id = None if current_user.role == UserRole.ADMIN else current_user.id
    updated = await db.update_replicated_playlist(replicated_id, user_id=target_user_id, **update_dict)
    return updated.model_dump() if updated else {}


@app.delete("/api/replicated-playlists/{replicated_id}")
async def delete_replicated_playlist(replicated_id: int, current_user: User = Depends(require_authenticated_user)):
    """Delete a replicated playlist watcher configuration."""
    target_user_id = None if current_user.role == UserRole.ADMIN else current_user.id
    deleted = await db.delete_replicated_playlist(replicated_id, user_id=target_user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Replicated playlist not found")
    return {"status": "ok", "message": f"Deleted replica watcher {replicated_id}"}


@app.post("/api/replicated-playlists/{replicated_id}/sync", dependencies=[Depends(rate_limit_dependency(10, 60, "playlist_sync", use_user_id=True))])
async def sync_replicated_playlist(replicated_id: int, current_user: User = Depends(require_authenticated_user)):
    """Trigger immediate reconciliation of a replicated playlist."""
    config = await db.get_replicated_playlist(replicated_id)
    if not config or (current_user.role != UserRole.ADMIN and config.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Replicated playlist not found")
    try:
        res = await playlist_replicator.reconcile_playlist(replicated_id, dry_run=False, config=config)
        return res
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.exception(f"Reconciliation failed for replica {replicated_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Reconciliation failed: {e}")


@app.post("/api/replicated-playlists/{replicated_id}/dry-run")
async def dry_run_replicated_playlist(replicated_id: int, current_user: User = Depends(require_authenticated_user)):
    """Preview reconciliation actions (add, remove, move, exclude) without modifying YouTube Music."""
    config = await db.get_replicated_playlist(replicated_id)
    if not config or (current_user.role != UserRole.ADMIN and config.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Replicated playlist not found")
    try:
        res = await playlist_replicator.reconcile_playlist(replicated_id, dry_run=True, config=config)
        return res
    except ValueError as ve:
        raise HTTPException(status_code=404, detail=str(ve))
    except Exception as e:
        logger.exception(f"Dry-run failed for replica {replicated_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Dry-run failed: {e}")


@app.get("/api/replicated-playlists/{replicated_id}/events")
async def get_replicated_playlist_events(replicated_id: int, limit: int = 100, current_user: User = Depends(require_authenticated_user)):
    """Get audit trail of reconciliation actions (ADD, REMOVE, MOVE, NOOP, EXCLUDE)."""
    config = await db.get_replicated_playlist(replicated_id)
    if not config or (current_user.role != UserRole.ADMIN and config.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Replicated playlist not found")
    events = await db.get_replicated_playlist_events(replicated_id, limit=limit)
    return events


@app.get("/api/replicated-playlists/{replicated_id}/snapshots/latest")
async def get_latest_playlist_snapshot(replicated_id: int, current_user: User = Depends(require_authenticated_user)):
    """Get the most recent SourcePlaylistSnapshot for a replica (Section 4 of plan)."""
    config = await db.get_replicated_playlist(replicated_id)
    if not config or (current_user.role != UserRole.ADMIN and config.user_id != current_user.id):
        raise HTTPException(status_code=404, detail="Replicated playlist not found")
    snapshot = await db.get_latest_replicated_playlist_snapshot(replicated_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="No snapshot found for this replicated playlist")
    return snapshot


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

@app.post("/api/scan", dependencies=[Depends(rate_limit_dependency(10, 60, "scan", use_user_id=True))])
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

@app.post("/api/queue/jobs/{job_id}/retry-blocked")
async def retry_blocked_queue_job(job_id: int):
    """
    Manually retry a BLOCKED job (Blocker 3).
    Guarantees that retry can only occur with the original upload source ID,
    never converting into a catalog search.
    """
    try:
        updated_job = await db.retry_blocked_sync_job(job_id)
        if not updated_job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "status": "success",
            "message": f"Job {job_id} re-queued for original upload source",
            "job": updated_job
        }
    except ValueError as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    except FileNotFoundError as ex:
        raise HTTPException(status_code=404, detail=str(ex))

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
async def get_uploads(current_user: Optional[User] = Depends(get_optional_authenticated_user)) -> list[YtmUpload]:
    if current_user and current_user.role != UserRole.ADMIN:
        return await db.get_all_ytm_uploads(user_id=current_user.id)
    return await db.get_all_ytm_uploads()

@app.get("/api/jobs/{job_id}", response_model=SyncJob)
async def get_job_by_id(job_id: int, current_user: Optional[User] = Depends(get_optional_authenticated_user)) -> SyncJob:
    job = await db.get_sync_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Sync job not found")
    if current_user and current_user.role != UserRole.ADMIN:
        if job.user_id and job.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Sync job not found")
    return job

@app.get("/api/history")
async def get_history(limit: int = 100, current_user: Optional[User] = Depends(get_optional_authenticated_user)) -> list[SyncJob]:
    if current_user and current_user.role != UserRole.ADMIN:
        return await db.get_user_sync_history(user_id=current_user.id, limit=limit)
    return await db.get_sync_history(limit=limit)

@app.get("/api/logs")
async def get_recent_logs(lines: int = Query(100, ge=1, le=1000)) -> list[str]:
    if not log_file.exists():
        return []
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
        return [l.rstrip("\r\n") for l in all_lines[-lines:]]

class FileReplacePreviewRequest(BaseModel):
    music_file_id: Optional[int] = None
    local_path: Optional[str] = None
    upload_entity_id: Optional[str] = None
    upload_video_id: Optional[str] = None

class FileReplaceExecuteRequest(BaseModel):
    music_file_id: Optional[int] = None
    local_path: Optional[str] = None
    upload_entity_id: Optional[str] = None
    upload_video_id: Optional[str] = None
    confirm: bool = False

@app.post("/api/files/replace/preview")
async def preview_file_replacement(req: FileReplacePreviewRequest):
    local_p: Optional[Path] = None
    local_mf = None
    if req.music_file_id:
        local_mf = await db.get_music_file_by_id(req.music_file_id)
        if local_mf:
            local_p = Path(local_mf.path)
    elif req.local_path:
        local_p = Path(req.local_path)

    if not local_p or not local_p.exists():
        raise HTTPException(status_code=404, detail=f"Local music file not found: {req.local_path or req.music_file_id}")

    upload = None
    if req.upload_entity_id:
        upload = await db.get_ytm_upload_by_entity_id(req.upload_entity_id)
    elif req.upload_video_id:
        upload = await db.get_ytm_upload_by_video_id(req.upload_video_id)

    if not upload:
        raise HTTPException(status_code=404, detail="Upload record not found")

    import hashlib
    h = hashlib.sha256()
    with open(local_p, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    local_sha256 = h.hexdigest()
    local_size = local_p.stat().st_size
    local_duration = local_mf.duration if local_mf else None

    upload_id = upload.video_id or upload.upload_video_id or upload.entity_id
    id_matches = bool(upload_id and len(upload_id) >= 11)
    duration_matches = True
    if local_duration and upload.duration:
        duration_matches = abs(local_duration - upload.duration) <= 4.0

    return {
        "local_file": {
            "path": str(local_p),
            "sha256": local_sha256,
            "size": local_size,
            "duration": local_duration
        },
        "new_source": {
            "source_type": "ytm_upload",
            "upload_id": upload_id,
            "title": upload.title,
            "artist": upload.artist,
            "expected_duration": upload.duration
        },
        "verification": {
            "upload_id_matches": id_matches,
            "duration_matches": duration_matches,
            "audio_validation_passed": id_matches and duration_matches,
            "status": "PASS" if (id_matches and duration_matches) else "REVIEW_REQUIRED"
        },
        "settings": {
            "automatic_replacement_enabled": settings.allow_automatic_replacement,
            "manual_confirmation_required": True
        }
    }

@app.post("/api/files/replace/execute")
async def execute_manual_file_replacement(req: FileReplaceExecuteRequest):
    if not req.confirm:
        raise HTTPException(
            status_code=400,
            detail="Explicit confirmation (confirm=true) is required to replace an existing local file."
        )

    local_p: Optional[Path] = None
    if req.music_file_id:
        mf = await db.get_music_file_by_id(req.music_file_id)
        if mf:
            local_p = Path(mf.path)
    elif req.local_path:
        local_p = Path(req.local_path)

    if not local_p or not local_p.exists():
        raise HTTPException(status_code=404, detail="Local music file not found on disk")

    upload = None
    if req.upload_entity_id:
        upload = await db.get_ytm_upload_by_entity_id(req.upload_entity_id)
    elif req.upload_video_id:
        upload = await db.get_ytm_upload_by_video_id(req.upload_video_id)

    if not upload:
        raise HTTPException(status_code=404, detail="Upload record not found")

    upload_id = upload.video_id or upload.upload_video_id or upload.entity_id

    staged = await download_upload(upload)

    try:
        committed_file = commit_staged_file_to_destination(
            staged_file=staged,
            destination_file=local_p,
            allow_overwrite=True,
            replacement_source_id=upload_id,
            authorized_manual_action=True
        )
    except Exception as ex:
        logger.error(f"Manual replacement failed: {ex}")
        raise HTTPException(status_code=500, detail=str(ex))
    finally:
        if staged.exists():
            try:
                staged.unlink()
            except Exception:
                pass

    return {
        "status": "success",
        "message": f"Successfully replaced {committed_file.name} with verified upload {upload_id}",
        "destination_file": str(committed_file),
        "source_id": upload_id
    }

class RestoreFileRequest(BaseModel):
    path: str

@app.get("/api/recovery/suspicious-files")
async def get_suspicious_files():
    from .recovery import audit_and_flag_suspicious_files
    files = await audit_and_flag_suspicious_files(database=db)
    return {"suspicious_files": files, "count": len(files)}

@app.post("/api/recovery/audit")
async def run_recovery_audit():
    from .recovery import audit_and_flag_suspicious_files
    files = await audit_and_flag_suspicious_files(database=db)
    return {"status": "success", "flagged_count": len(files), "flagged_files": files}

@app.post("/api/recovery/restore")
async def restore_suspicious_file(req: RestoreFileRequest):
    from .recovery import restore_corrupted_file
    try:
        res = await restore_corrupted_file(req.path, database=db)
        return res
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restore file: {str(e)}")

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

        # 1. Download audio file from YTM upload locker (strictly exact upload identity; NO search fallback)
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
