import asyncio
import os
import shutil
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.models import (
    User, UserRole, UserCreate, UserUpdate, UserSettingsUpdate,
    ReplicatedPlaylistCreate, ConnectionStatus
)
from ytm_service.auth_service import auth_service
from ytm_service.ytm_client import ytm_client


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env(tmp_path: Path):
    settings.config_dir = tmp_path / "config"
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path = settings.config_dir / "test_security.db"
    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    settings.allowed_fs_roots = [music_dir]
    db.db_path = settings.db_path
    await db.init_db()
    auth_service._sessions.clear()
    ytm_client.manager._clients.clear()
    from ytm_service.rate_limiter import limiter
    await limiter.reset()
    yield
    # Cleanup


async def _create_test_users():
    """Helper to create User A, User B, and an Admin with session tokens."""
    # Admin is created by init_db
    admin = await db.get_user_by_username("admin")
    admin_session = await db.create_app_session(admin.id)

    user_a = await db.create_user(UserCreate(username="user_a", password="password_a", role=UserRole.USER))
    session_a = await db.create_app_session(user_a.id)

    user_b = await db.create_user(UserCreate(username="user_b", password="password_b", role=UserRole.USER))
    session_b = await db.create_app_session(user_b.id)

    return {
        "admin": (admin, admin_session.token),
        "user_a": (user_a, session_a.token),
        "user_b": (user_b, session_b.token),
    }


# ============================================================================
# Phase W — Comprehensive Multi-User API Security Boundary Tests
# ============================================================================

@pytest.mark.asyncio
async def test_user_a_cannot_read_user_b_playlists():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User B creates a replicated playlist
        resp_b = await ac.post(
            "/api/replicated-playlists",
            json={
                "source_playlist_id": "PL_b_secret",
                "source_playlist_name": "User B Secret Playlist",
                "destination_playlist_name": "User B Locker",
                "enabled": True,
            },
            headers={"Authorization": f"Bearer {token_b}"}
        )
        assert resp_b.status_code == 200
        pl_b_id = resp_b.json()["id"]

        # User A lists replicated playlists - should NOT see User B's playlist
        resp_a_list = await ac.get(
            "/api/replicated-playlists",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a_list.status_code == 200
        playlists = resp_a_list.json()
        assert not any(p["id"] == pl_b_id for p in playlists)

        # User A tries to read User B's playlist directly - must return 404
        resp_a_get = await ac.get(
            f"/api/replicated-playlists/{pl_b_id}",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a_get.status_code == 404


@pytest.mark.asyncio
async def test_user_a_cannot_read_user_b_uploads():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    # Insert upload for User B
    await db.upsert_ytm_upload({
        "entity_id": "FEmusic_library_priv_b",
        "video_id": "vid_b_secret",
        "title": "Secret Track B",
        "artist": "Artist B",
        "user_id": user_b.id
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A lists uploads - should NOT contain User B's upload
        resp_a = await ac.get(
            "/api/uploads",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a.status_code == 200
        uploads_a = resp_a.json()
        assert not any(u["entity_id"] == "FEmusic_library_priv_b" for u in uploads_a)

        # User B lists uploads - should contain User B's upload
        resp_b = await ac.get(
            "/api/uploads",
            headers={"Authorization": f"Bearer {token_b}"}
        )
        assert resp_b.status_code == 200
        uploads_b = resp_b.json()
        assert any(u["entity_id"] == "FEmusic_library_priv_b" for u in uploads_b)


@pytest.mark.asyncio
async def test_user_a_cannot_read_user_b_sync_jobs():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    # Create dummy music file and sync job for User B
    from ytm_service.models import MusicFile
    mf = MusicFile(
        path=str(settings.allowed_fs_roots[0] / "song_b.mp3"),
        filename="song_b.mp3",
        artist="Artist B",
        title="Song B",
        format="mp3",
        file_size=1000,
        modified_time=time.time(),
    )
    mf_id = await db.upsert_music_file(mf.model_dump())
    job_id = await db.create_sync_job(mf_id, user_id=user_b.id)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A tries to read User B's job directly - must return 404
        resp_a_job = await ac.get(
            f"/api/jobs/{job_id}",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a_job.status_code == 404

        # User A checks history - should NOT contain User B's job
        resp_a_hist = await ac.get(
            "/api/history",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a_hist.status_code == 200
        history_a = resp_a_hist.json()
        assert not any(j["id"] == job_id for j in history_a)


@pytest.mark.asyncio
async def test_user_a_cannot_access_user_b_authentication():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    # User B links YTM account
    await db.create_or_update_ytm_account(
        user_id=user_b.id,
        account_name="User B YouTube",
        account_identifier="user_b@gmail.com",
        status="CONNECTED"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A checks /api/ytm/account - should not see User B's account
        resp_a = await ac.get(
            "/api/ytm/account",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a.status_code == 200
        assert resp_a.json() is None


@pytest.mark.asyncio
async def test_user_a_cannot_disconnect_user_b():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    # Both users have accounts
    user_a_id = users["user_a"][0].id
    await db.create_or_update_ytm_account(
        user_id=user_a_id,
        account_name="User A YouTube",
        account_identifier="user_a@gmail.com",
        status="CONNECTED"
    )
    await db.create_or_update_ytm_account(
        user_id=user_b.id,
        account_name="User B YouTube",
        account_identifier="user_b@gmail.com",
        status="CONNECTED"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A calls disconnect
        resp_a = await ac.post(
            "/api/ytm/disconnect",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a.status_code == 200

        # User B's account must still be CONNECTED
        acc_b = await db.get_ytm_account_by_user_id(user_b.id)
        assert acc_b is not None
        assert acc_b.status == "CONNECTED"


@pytest.mark.asyncio
async def test_user_a_cannot_modify_user_b_settings():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    # User B configures custom settings
    await db.update_user_settings(user_b.id, UserSettingsUpdate(auto_upload=True, scan_interval_minutes=15))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A updates settings
        resp_a = await ac.put(
            "/api/settings",
            json={"auto_upload": False, "scan_interval_minutes": 60},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a.status_code == 200

        # User B's settings remain untouched
        settings_b = await db.get_user_settings(user_b.id)
        assert settings_b.auto_upload is True
        assert settings_b.scan_interval_minutes == 15


@pytest.mark.asyncio
async def test_user_a_cannot_access_user_b_files():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, _ = users["user_b"]

    # Create secret credential file in User B's directory
    user_b_dir = settings.config_dir / "users" / user_b.id
    user_b_dir.mkdir(parents=True, exist_ok=True)
    secret_file = user_b_dir / "auth.json.enc"
    secret_file.write_text("SUPER_SECRET_PAYLOAD")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A tries to trigger scan on User B's config directory (path traversal attempt)
        resp_a = await ac.post(
            "/api/scan",
            json={"folders": [str(user_b_dir)]},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        # Should be rejected with 400 because config/user directory is not in allowed roots
        assert resp_a.status_code == 400


@pytest.mark.asyncio
async def test_user_a_cannot_create_playlist_for_user_b():
    users = await _create_test_users()
    user_a, token_a = users["user_a"]
    user_b, _ = users["user_b"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A includes user_b's user_id in payload
        resp = await ac.post(
            "/api/replicated-playlists",
            json={
                "source_playlist_id": "PL_tamper_user",
                "source_playlist_name": "Tamper Playlist",
                "destination_playlist_name": "Tamper Locker",
                "user_id": user_b.id,
            },
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp.status_code == 200
        data = resp.json()
        # Verify ownership is server-derived as User A, NOT User B
        created = await db.get_replicated_playlist(data["id"])
        assert created.user_id == user_a.id
        assert created.user_id != user_b.id


@pytest.mark.asyncio
async def test_user_a_cannot_trigger_user_b_sync():
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, token_b = users["user_b"]

    # User B creates replica
    replica_id = await db.create_replicated_playlist(
        source_playlist_id="PL_sync_b",
        source_playlist_name="Playlist B",
        destination_playlist_id="PL_dest_b",
        destination_playlist_name="Locker B",
        user_id=user_b.id
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A attempts to trigger sync on User B's replica -> 404
        resp_a = await ac.post(
            f"/api/replicated-playlists/{replica_id}/sync",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_a.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_escalate_to_admin():
    users = await _create_test_users()
    user_a, token_a = users["user_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Standard user attempts to list admin users
        resp1 = await ac.get("/api/admin/users", headers={"Authorization": f"Bearer {token_a}"})
        assert resp1.status_code == 403

        # Standard user attempts to create user via admin route
        resp2 = await ac.post(
            "/api/admin/users",
            json={"username": "hacker", "password": "password", "role": "admin"},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp2.status_code == 403

        # Standard user attempts to update own role to admin
        resp3 = await ac.put(
            f"/api/admin/users/{user_a.id}",
            json={"role": "admin"},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp3.status_code == 403


@pytest.mark.asyncio
async def test_invalid_sessions_are_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer totally_bogus_token"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_expired_sessions_are_rejected():
    users = await _create_test_users()
    user_a, _ = users["user_a"]

    # Create expired session
    expired_session = await db.create_app_session(user_a.id, duration_seconds=-10)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {expired_session.token}"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_tokens_cannot_be_reused_after_logout():
    users = await _create_test_users()
    _, token_a = users["user_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Verify initial valid call
        resp1 = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        assert resp1.status_code == 200

        # Logout
        resp_logout = await ac.post("/api/auth/logout", headers={"Authorization": f"Bearer {token_a}"})
        assert resp_logout.status_code == 200

        # Subsequent call with same token must fail
        resp2 = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        assert resp2.status_code == 401


# ============================================================================
# Phase U — Disconnect vs Delete Separation
# ============================================================================

@pytest.mark.asyncio
async def test_disconnect_ytm_retains_user_and_records():
    users = await _create_test_users()
    user_a, token_a = users["user_a"]

    # Setup YTM account and playlist for User A
    await db.create_or_update_ytm_account(
        user_id=user_a.id,
        account_name="User A YouTube",
        account_identifier="user_a@gmail.com",
        status="CONNECTED"
    )
    pl_id = await db.create_replicated_playlist(
        source_playlist_id="PL_retain_test",
        source_playlist_name="Retained Playlist",
        destination_playlist_id="PL_retain_dest",
        destination_playlist_name="Retained Locker",
        user_id=user_a.id
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Disconnect YTM
        resp = await ac.post("/api/ytm/disconnect", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200

        # User account is still valid and can query /api/auth/me
        me_resp = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        assert me_resp.status_code == 200

        # YTM account status is DISCONNECTED, not deleted
        acc = await db.get_ytm_account_by_user_id(user_a.id)
        assert acc is not None
        assert acc.status == "DISCONNECTED"

        # Replicated playlist is still retained
        pl = await db.get_replicated_playlist(pl_id, user_id=user_a.id)
        assert pl is not None


@pytest.mark.asyncio
async def test_delete_user_requires_confirmation_and_cascades():
    users = await _create_test_users()
    admin_user, admin_token = users["admin"]
    user_b, token_b = users["user_b"]

    user_b_dir = settings.config_dir / "users" / user_b.id
    user_b_dir.mkdir(parents=True, exist_ok=True)
    (user_b_dir / "dummy_file.txt").write_text("data")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Attempt delete without confirm=true -> 400 Bad Request
        resp_no_confirm = await ac.delete(
            f"/api/admin/users/{user_b.id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert resp_no_confirm.status_code == 400

        # Delete with confirm=true -> succeeds
        resp_confirmed = await ac.delete(
            f"/api/admin/users/{user_b.id}?confirm=true",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert resp_confirmed.status_code == 200

        # User is deleted from database
        assert await db.get_user_by_id(user_b.id) is None
        # Filesystem directory is removed
        assert not user_b_dir.exists()


@pytest.mark.asyncio
async def test_cannot_delete_last_admin():
    users = await _create_test_users()
    admin_user, admin_token = users["admin"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Self-delete admin -> 400 Cannot delete your own admin account
        resp = await ac.delete(
            f"/api/admin/users/{admin_user.id}?confirm=true",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert resp.status_code == 400


# ============================================================================
# Phase V — Migration From Single-User Installations
# ============================================================================

@pytest.mark.asyncio
async def test_phase_v_legacy_migration_flow(tmp_path: Path):
    migration_config_dir = tmp_path / "migration_config"
    migration_config_dir.mkdir(parents=True, exist_ok=True)
    legacy_auth_dir = migration_config_dir / "auth"
    legacy_auth_dir.mkdir(parents=True, exist_ok=True)
    legacy_file = legacy_auth_dir / "headers_auth.json"
    legacy_file.write_text('{"Cookie": "test_legacy_cookie=123;"}')

    settings.config_dir = migration_config_dir
    settings.auth_file = legacy_file
    settings.users_dir = migration_config_dir / "users"
    db.db_path = migration_config_dir / "migrated.db"

    # Mock test_connection to simulate successful connection
    with patch("ytm_service.ytm_client.ytm_client.test_connection", new_callable=AsyncMock) as mock_test:
        mock_test.return_value = {"connected": True, "message": "Connected", "user_name": "Migrated User"}
        await db.init_db()

    # Verify backup was created
    backup_file = legacy_auth_dir / "headers_auth.json.migrated_backup"
    assert backup_file.exists()

    # Verify legacy file was deprecated to .migrated
    depr_file = legacy_auth_dir / "headers_auth.json.migrated"
    assert depr_file.exists()

    # Verify admin user exists and has connected YTM account
    admin = await db.get_user_by_username("admin")
    assert admin is not None
    acc = await db.get_ytm_account_by_user_id(admin.id)
    assert acc is not None
    assert acc.status == "CONNECTED"
