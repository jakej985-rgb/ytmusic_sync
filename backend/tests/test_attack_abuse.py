import asyncio
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
    User, UserRole, UserCreate, ReplicatedPlaylistCreate
)
from ytm_service.auth_service import auth_service
from ytm_service.rate_limiter import limiter


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env(tmp_path: Path):
    settings.config_dir = tmp_path / "config"
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path = settings.config_dir / "test_attack.db"
    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    settings.allowed_fs_roots = [music_dir]
    db.db_path = settings.db_path
    await db.init_db()
    auth_service._sessions.clear()
    await limiter.reset()
    yield


async def _create_test_users():
    admin = await db.get_user_by_username("admin")
    admin_session = await db.create_app_session(admin.id)

    user_a = await db.create_user(UserCreate(username="attacker_a", password="password_a", role=UserRole.USER))
    session_a = await db.create_app_session(user_a.id)

    user_b = await db.create_user(UserCreate(username="victim_b", password="password_b", role=UserRole.USER))
    session_b = await db.create_app_session(user_b.id)

    return {
        "admin": (admin, admin_session.token),
        "user_a": (user_a, session_a.token),
        "user_b": (user_b, session_b.token),
    }


# ============================================================================
# Phase X — Explicit Attack and Abuse Tests
# ============================================================================

@pytest.mark.asyncio
async def test_attack_tamper_user_id_in_payload():
    """Attack vector: User A passes victim's user_id in payload to hijack ownership."""
    users = await _create_test_users()
    user_a, token_a = users["user_a"]
    user_b, _ = users["user_b"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/replicated-playlists",
            json={
                "source_playlist_id": "PL_tamper_1",
                "source_playlist_name": "Tamper Test",
                "destination_playlist_name": "Tamper Locker",
                "user_id": user_b.id,
            },
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp.status_code == 200
        pl = await db.get_replicated_playlist(resp.json()["id"])
        # Ownership must be bound to authenticated caller user_a, never victim user_b
        assert pl.user_id == user_a.id
        assert pl.user_id != user_b.id


@pytest.mark.asyncio
async def test_attack_change_playlist_id_to_victim_playlist():
    """Attack vector: User A tries to modify or delete User B's playlist."""
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, _ = users["user_b"]

    # Victim creates playlist
    pl_id = await db.create_replicated_playlist(
        source_playlist_id="PL_victim_pl",
        source_playlist_name="Victim Playlist",
        destination_playlist_id="PL_victim_dest",
        destination_playlist_name="Victim Locker",
        user_id=user_b.id
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # User A tries to update victim's playlist
        resp_put = await ac.put(
            f"/api/replicated-playlists/{pl_id}",
            json={"destination_playlist_name": "Hacked Name"},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_put.status_code == 404

        # User A tries to delete victim's playlist
        resp_del = await ac.delete(
            f"/api/replicated-playlists/{pl_id}",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp_del.status_code == 404

    # Ensure victim playlist is unmodified
    pl = await db.get_replicated_playlist(pl_id)
    assert pl.destination_playlist_name == "Victim Locker"


@pytest.mark.asyncio
async def test_attack_change_upload_id_to_victim_upload():
    """Attack vector: User A queries victim's upload or tries to replace it."""
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, _ = users["user_b"]

    await db.upsert_ytm_upload({
        "entity_id": "FEmusic_library_victim_upload",
        "video_id": "vid_victim_123",
        "title": "Victim Song",
        "artist": "Victim Artist",
        "user_id": user_b.id
    })

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Listing uploads as User A should not show victim's upload
        resp = await ac.get("/api/uploads", headers={"Authorization": f"Bearer {token_a}"})
        assert resp.status_code == 200
        uploads = resp.json()
        assert not any(u["entity_id"] == "FEmusic_library_victim_upload" for u in uploads)


@pytest.mark.asyncio
async def test_attack_tamper_auth_session_id():
    """Attack vector: Submitting credentials to a non-existent or manipulated session ID."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post(
            "/api/auth/callback",
            json={
                "session_id": "malicious_fake_session_123456",
                "raw_headers": "Cookie: foo=bar"
            }
        )
        assert resp.status_code in (400, 404)


@pytest.mark.asyncio
async def test_attack_reuse_authentication_token_after_revocation():
    """Attack vector: Reusing an app session token after user has logged out."""
    users = await _create_test_users()
    _, token_a = users["user_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Initial request succeeds
        r1 = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        assert r1.status_code == 200

        # Logout revokes session
        r_logout = await ac.post("/api/auth/logout", headers={"Authorization": f"Bearer {token_a}"})
        assert r_logout.status_code == 200

        # Replay attempt fails
        r2 = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token_a}"})
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_attack_reuse_callback_on_completed_session():
    """Attack vector: Replay attack on an auth session callback that has already completed."""
    users = await _create_test_users()
    user_a, token_a = users["user_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_resp = await ac.post(
            "/api/auth/start",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        mock_result = {
            "connected": True,
            "message": "Connected",
            "user_name": "Test User",
            "account_email": "test@example.com",
            "channel_id": "UC_test"
        }

        with patch("ytm_service.ytm_client.ytm_client.setup_auth", new_callable=AsyncMock) as mock_setup:
            mock_setup.return_value = mock_result
            # First completion succeeds
            cb1 = await ac.post(
                "/api/auth/callback",
                json={"session_id": session_id, "raw_headers": "Cookie: valid=123;"}
            )
            assert cb1.status_code == 200
            assert cb1.json()["status"] == "connected"

            # Replay callback attempt must be rejected (single-use constraint)
            cb2 = await ac.post(
                "/api/auth/callback",
                json={"session_id": session_id, "raw_headers": "Cookie: replay=456;"}
            )
            assert cb2.status_code == 400
            assert "already been used" in cb2.json()["detail"].lower() or "completed" in cb2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_attack_use_expired_session():
    """Attack vector: Attempting to complete an authentication session after it expires."""
    users = await _create_test_users()
    user_a, token_a = users["user_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        session = await auth_service.start_session(
            origin_url="http://test",
            client_ip="127.0.0.1",
            user_id=user_a.id,
            ttl_seconds=-10 # Already expired
        )
        resp = await ac.post(
            "/api/auth/callback",
            json={"session_id": session.session_id, "raw_headers": "Cookie: foo=bar;"}
        )
        assert resp.status_code == 400
        assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_attack_use_another_users_session_token():
    """Attack vector: User A steals or guesses User B's session token."""
    users = await _create_test_users()
    user_a, _ = users["user_a"]
    user_b, token_b = users["user_b"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Attacker uses token_b; server identifies caller as user_b, preventing privilege spoofing
        resp = await ac.get("/api/auth/me", headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 200
        assert resp.json()["id"] == user_b.id
        assert resp.json()["id"] != user_a.id


@pytest.mark.asyncio
async def test_attack_access_another_users_filesystem_path():
    """Attack vector: Path traversal targeting another user's encrypted credentials."""
    users = await _create_test_users()
    _, token_a = users["user_a"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Traversal attempts in folder scanning
        resp1 = await ac.post(
            "/api/scan",
            json={"folders": ["/config/users/victim/auth.json.enc"]},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp1.status_code == 400

        resp2 = await ac.post(
            "/api/scan",
            json={"folders": ["../../../../etc/shadow"]},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert resp2.status_code == 400


@pytest.mark.asyncio
async def test_attack_call_admin_endpoints_as_normal_user():
    """Attack vector: Regular user calling user creation, update, deletion, listing."""
    users = await _create_test_users()
    _, token_a = users["user_a"]
    user_b, _ = users["user_b"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # List users
        r1 = await ac.get("/api/admin/users", headers={"Authorization": f"Bearer {token_a}"})
        assert r1.status_code == 403

        # Create user
        r2 = await ac.post(
            "/api/admin/users",
            json={"username": "injected", "password": "pwd", "role": "admin"},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert r2.status_code == 403

        # Update user
        r3 = await ac.put(
            f"/api/admin/users/{user_b.id}",
            json={"is_active": False},
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert r3.status_code == 403

        # Delete user
        r4 = await ac.delete(
            f"/api/admin/users/{user_b.id}?confirm=true",
            headers={"Authorization": f"Bearer {token_a}"}
        )
        assert r4.status_code == 403
