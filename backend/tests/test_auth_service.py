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
from ytm_service.auth_service import auth_service
from ytm_service.auth_session import AuthSessionStatus


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env(tmp_path: Path):
    settings.db_path = tmp_path / "test_auth.db"
    settings.auth_file = tmp_path / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()
    # Reset in-memory sessions and rate limiter
    auth_service._sessions.clear()
    from ytm_service.rate_limiter import limiter
    await limiter.reset()


@pytest.mark.asyncio
async def test_start_auth_session():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/auth/start", json={"origin_url": "http://localhost:6969"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["status"] == "pending"
        assert "ytm_sync_session=" in data["auth_url"]
        assert "callback=" in data["auth_url"]
        assert data["expires_at"] > time.time()


@pytest.mark.asyncio
async def test_get_auth_session_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/auth/session/non_existent_session_id")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_complete_auth_session_success():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Start session
        start_resp = await ac.post("/api/auth/start")
        session_id = start_resp.json()["session_id"]

        # Mock setup_auth to return success
        mock_result = {
            "connected": True,
            "message": "Connected to YouTube Music successfully.",
            "user_name": "Test User"
        }
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            comp_resp = await ac.post(
                f"/api/auth/session/{session_id}/complete",
                json={"raw_headers": "Cookie: foo=bar\nAuthorization: SAPISIDHASH 123"}
            )
            assert comp_resp.status_code == 200
            data = comp_resp.json()
            assert data["status"] == "connected"
            assert data["connected"] is True
            assert data["user_name"] == "Test User"

        # Verify polling endpoint returns connected
        get_resp = await ac.get(f"/api/auth/session/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "connected"
        assert get_resp.json()["connected"] is True


@pytest.mark.asyncio
async def test_complete_auth_session_failure():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_resp = await ac.post("/api/auth/start")
        session_id = start_resp.json()["session_id"]

        mock_result = {
            "connected": False,
            "message": "Invalid credentials provided.",
            "user_name": None
        }
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            comp_resp = await ac.post(
                f"/api/auth/session/{session_id}/complete",
                json={"raw_headers": "bad_headers"}
            )
            assert comp_resp.status_code == 200
            data = comp_resp.json()
            assert data["status"] == "failed"
            assert data["connected"] is False
            assert "Invalid credentials" in data["error_message"]


@pytest.mark.asyncio
async def test_cancel_auth_session():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_resp = await ac.post("/api/auth/start")
        session_id = start_resp.json()["session_id"]

        cancel_resp = await ac.post(f"/api/auth/session/{session_id}/cancel")
        assert cancel_resp.status_code == 200
        assert cancel_resp.json()["status"] == "cancelled"

        # Further complete attempts should be rejected
        comp_resp = await ac.post(
            f"/api/auth/session/{session_id}/complete",
            json={"raw_headers": "Cookie: test"}
        )
        assert comp_resp.status_code == 400

        # Also test POST /api/auth/cancel endpoint directly
        start_resp2 = await ac.post("/api/auth/start")
        session_id2 = start_resp2.json()["session_id"]
        cancel_resp2 = await ac.post("/api/auth/cancel", json={"session_id": session_id2})
        assert cancel_resp2.status_code == 200
        assert cancel_resp2.json()["status"] == "cancelled"



@pytest.mark.asyncio
async def test_session_expiration():
    start = await auth_service.start_session()
    session_id = start.session_id

    # Artificially expire the session
    auth_service._sessions[session_id].expires_at = time.time() - 10

    session = await auth_service.get_session(session_id)
    assert session.status == AuthSessionStatus.EXPIRED
    assert "expired" in session.error_message.lower()


@pytest.mark.asyncio
async def test_disconnect_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create a dummy auth file
        settings.auth_file.write_text('{"dummy": "auth"}')
        assert settings.auth_file.exists()

        resp = await ac.post("/api/auth/disconnect")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is False
        assert not settings.auth_file.exists()


@pytest.mark.asyncio
async def test_auth_callback_post_and_get():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Start session
        start_resp = await ac.post("/api/auth/start")
        session_id = start_resp.json()["session_id"]

        # 2. Before completion, GET callback should indicate pending
        get_before = await ac.get(f"/api/auth/callback?session_id={session_id}")
        assert get_before.status_code == 400
        assert "Authentication Pending" in get_before.text

        # 3. POST /api/auth/callback with valid credentials
        mock_result = {
            "connected": True,
            "message": "Connected successfully",
            "user_name": "Jake User"
        }
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            post_resp = await ac.post(
                "/api/auth/callback",
                json={"session_id": session_id, "raw_headers": "Cookie: test=1"}
            )
            assert post_resp.status_code == 200
            assert post_resp.json()["connected"] is True
            assert post_resp.json()["user_name"] == "Jake User"

        # 4. After completion, GET callback returns success page
        get_after = await ac.get(f"/api/auth/callback?session_id={session_id}")
        assert get_after.status_code == 200
        assert "YouTube Music Connected" in get_after.text
        assert "Jake User" in get_after.text


@pytest.mark.asyncio
async def test_security_credentials_never_leaked_in_errors():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_resp = await ac.post("/api/auth/start")
        session_id = start_resp.json()["session_id"]

        # Simulate a backend error with sensitive cookie content
        async def mock_failing_setup(raw_headers):
            raise RuntimeError("Fatal parser crash on Cookie: SAPISID=SECRET_PASS_TOKEN; SSID=SECRET_SSID")

        with patch("ytm_service.auth_service.ytm_client.setup_auth", side_effect=mock_failing_setup):
            resp = await ac.post(
                f"/api/auth/session/{session_id}/complete",
                json={"raw_headers": "Cookie: SAPISID=SECRET_PASS_TOKEN"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["connected"] is False
            # Verify sensitive tokens are strictly scrubbed
            assert "SECRET_PASS_TOKEN" not in data["error_message"]
            assert "SECRET_SSID" not in data["error_message"]
            assert "Failed to parse YouTube Music credentials" in data["error_message"]


@pytest.mark.asyncio
async def test_secure_auth_file_and_directory_permissions(tmp_path: Path):
    from ytm_service.ytm_client import ytm_client
    import os

    auth_dir = tmp_path / "secure_auth_test"
    auth_file = auth_dir / "headers_auth.json"
    settings.auth_file = auth_file

    def mock_setup(filepath, headers_raw):
        Path(filepath).write_text('{"cookie": "test"}')
        return {"cookie": "test"}

    with patch("ytm_service.ytm_client.setup", side_effect=mock_setup):
        with patch.object(ytm_client, "test_connection", new=AsyncMock(return_value={"connected": True, "message": "OK", "user_name": "Test"})):
            res = await ytm_client.setup_auth("Cookie: test=1")
            assert res["connected"] is True

            # Verify directory is 0700
            dir_mode = os.stat(str(auth_dir)).st_mode & 0o777
            assert dir_mode == 0o700

            # Verify file is 0600
            file_mode = os.stat(str(auth_file)).st_mode & 0o777
            assert file_mode == 0o600


@pytest.mark.asyncio
async def test_session_reuse_prevention():
    """Verify single-use session guarantee: completed session cannot be reused."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_resp = await ac.post("/api/auth/start")
        session_id = start_resp.json()["session_id"]

        mock_result = {"connected": True, "message": "OK", "user_name": "Test User"}
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            # First completion succeeds
            c1 = await ac.post(
                f"/api/auth/session/{session_id}/complete",
                json={"raw_headers": "Cookie: valid=1"}
            )
            assert c1.status_code == 200

            # Second completion attempt MUST be rejected (single-use)
            c2 = await ac.post(
                f"/api/auth/session/{session_id}/complete",
                json={"raw_headers": "Cookie: valid=2"}
            )
            assert c2.status_code == 400
            assert "already been used" in c2.json()["detail"].lower()


@pytest.mark.asyncio
async def test_reverse_proxy_origin_detection():
    """Verify Docker / reverse proxy headers (X-Forwarded-Proto, X-Forwarded-Host) are respected."""
    transport = ASGITransport(app=app)
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "ytm.example.com",
        "x-forwarded-for": "192.168.1.50",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/auth/start", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "callback=https%3A%2F%2Fytm.example.com" in data["auth_url"]


@pytest.mark.asyncio
async def test_legacy_developer_setup_retained():
    """Verify legacy manual developer endpoint /api/auth/setup still functions."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        mock_result = {"connected": True, "message": "OK", "user_name": "Dev User"}
        with patch("ytm_service.main.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            resp = await ac.post("/api/auth/setup", json={"raw_headers": "Cookie: manual=1"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["connected"] is True
            assert data["user_name"] == "Dev User"


@pytest.mark.asyncio
async def test_auth_status_and_test_endpoints():
    """Verify GET /api/auth/status and POST /api/auth/test."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        mock_status = {"connected": True, "message": "All good", "user_name": "Status User"}
        with patch("ytm_service.main.ytm_client.test_connection", new=AsyncMock(return_value=mock_status)):
            status_resp = await ac.get("/api/auth/status")
            assert status_resp.status_code == 200
            assert status_resp.json()["connected"] is True
            assert status_resp.json()["user_name"] == "Status User"

            test_resp = await ac.post("/api/auth/test")
            assert test_resp.status_code == 200
            assert test_resp.json()["connected"] is True
            assert test_resp.json()["user_name"] == "Status User"


@pytest.mark.asyncio
async def test_origin_validation_blocks_arbitrary_untrusted_origins():
    """Phase B (3.1): Arbitrary attacker-supplied origin URLs must be rejected with 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Untrusted attacker domain
        resp = await ac.post("/api/auth/start", json={"origin_url": "https://evil.attacker.com"})
        assert resp.status_code == 400
        assert "unapproved" in resp.json()["detail"].lower()

        # Embedded credentials
        resp_creds = await ac.post("/api/auth/start", json={"origin_url": "http://user:pass@localhost:8080"})
        assert resp_creds.status_code == 400

        # Disallowed scheme
        resp_scheme = await ac.post("/api/auth/start", json={"origin_url": "javascript:alert(1)"})
        assert resp_scheme.status_code == 400


@pytest.mark.asyncio
async def test_origin_validation_allows_approved_and_lan_origins():
    """Phase B (3.1): Approved origins, localhost, and LAN addresses are accepted."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Localhost with port
        resp_local = await ac.post("/api/auth/start", json={"origin_url": "http://localhost:6969"})
        assert resp_local.status_code == 200

        # LAN IP (RFC 1918)
        resp_lan = await ac.post("/api/auth/start", json={"origin_url": "http://192.168.1.50:8080"})
        assert resp_lan.status_code == 200


@pytest.mark.asyncio
async def test_auth_session_user_id_and_consumed_at_lifecycle():
    """Phase B (3.2): AuthSession tracks user_id, callback_origin, and consumed_at."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Start session with user_id
        start_resp = await ac.post(
            "/api/auth/start",
            json={"origin_url": "http://localhost:8080", "user_id": "user_42"}
        )
        assert start_resp.status_code == 200
        start_data = start_resp.json()
        assert start_data["user_id"] == "user_42"
        session_id = start_data["session_id"]

        # Check status before completion
        status_resp = await ac.get(f"/api/auth/session/{session_id}")
        assert status_resp.status_code == 200
        session_info = status_resp.json()
        assert session_info["user_id"] == "user_42"
        assert session_info["callback_origin"] == "http://localhost:8080"
        assert session_info["consumed_at"] is None

        # Complete session
        mock_result = {"connected": True, "message": "OK", "user_name": "User 42"}
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            comp_resp = await ac.post(
                f"/api/auth/session/{session_id}/complete",
                json={"raw_headers": "Cookie: foo=bar\nAuthorization: SAPISIDHASH 123"}
            )
            assert comp_resp.status_code == 200
            comp_data = comp_resp.json()
            assert comp_data["user_id"] == "user_42"
            assert comp_data["consumed_at"] is not None
            assert comp_data["consumed_at"] > 0


@pytest.mark.asyncio
async def test_temporary_extension_token_scoped_to_auth_endpoints():
    """Phase C (4.2): Extension token permits auth callback but strictly rejects non-auth endpoints with 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Start session and get extension_token
        start_resp = await ac.post("/api/auth/start", json={"origin_url": "http://localhost:8080"})
        assert start_resp.status_code == 200
        start_data = start_resp.json()
        ext_token = start_data.get("extension_token")
        session_id = start_data["session_id"]
        assert ext_token is not None

        # 1. Calling non-auth endpoints with extension token MUST return 403 Forbidden
        headers = {"Authorization": f"Bearer {ext_token}"}
        blocked_endpoints = [
            ("GET", "/api/status"),
            ("GET", "/api/settings"),
            ("GET", "/api/ytm/playlists"),
            ("GET", "/api/ytm/uploads"),
        ]
        for method, endpoint in blocked_endpoints:
            res = await ac.request(method, endpoint, headers=headers)
            assert res.status_code == 403, f"Expected 403 for {method} {endpoint} with extension token, got {res.status_code}"
            assert "restricted" in res.json()["detail"].lower()

        # 2. Calling auth callback endpoint with extension token MUST succeed
        mock_result = {"connected": True, "message": "OK", "user_name": "Ext User"}
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            callback_resp = await ac.post(
                "/api/auth/callback",
                headers={"Authorization": f"Bearer {ext_token}"},
                json={
                    "session_id": session_id,
                    "token": ext_token,
                    "raw_headers": "Cookie: ext=1\nAuthorization: SAPISIDHASH 999",
                }
            )
            assert callback_resp.status_code == 200
            assert callback_resp.json()["connected"] is True


@pytest.mark.asyncio
async def test_temporary_extension_token_single_use_invalidated():
    """Phase C (4.2): Once the session completes, the extension token is immediately invalidated."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        start_resp = await ac.post("/api/auth/start")
        start_data = start_resp.json()
        ext_token = start_data["extension_token"]
        session_id = start_data["session_id"]

        mock_result = {"connected": True, "message": "OK", "user_name": "Ext User"}
        with patch("ytm_service.auth_service.ytm_client.setup_auth", new=AsyncMock(return_value=mock_result)):
            # First callback completes session
            res1 = await ac.post(
                "/api/auth/callback",
                headers={"Authorization": f"Bearer {ext_token}"},
                json={
                    "session_id": session_id,
                    "token": ext_token,
                    "raw_headers": "Cookie: ext=1",
                }
            )
            assert res1.status_code == 200

            # Second callback with same token is rejected (401 because token is no longer active)
            res2 = await ac.post(
                "/api/auth/callback",
                headers={"Authorization": f"Bearer {ext_token}"},
                json={
                    "session_id": session_id,
                    "token": ext_token,
                    "raw_headers": "Cookie: ext=1",
                }
            )
            assert res2.status_code == 401



