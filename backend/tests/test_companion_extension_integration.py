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
from ytm_service.models import User, UserRole, UserCreate
from ytm_service.auth_service import auth_service
from ytm_service.rate_limiter import limiter


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env(tmp_path: Path):
    settings.config_dir = tmp_path / "config"
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path = settings.config_dir / "test_ext.db"
    settings.auth_file = settings.config_dir / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()
    auth_service._sessions.clear()
    await limiter.reset()
    yield


# ============================================================================
# Phase Y — Companion Extension Integration End-to-End Tests
# ============================================================================

@pytest.mark.asyncio
async def test_companion_extension_end_to_end_flow():
    """
    End-to-end simulation of Flutter client triggering auth,
    companion extension capturing mocked credentials, submitting to callback,
    and verifying state transition and single-use invalidation.
    """
    admin = await db.get_user_by_username("admin")
    user = await db.create_user(UserCreate(username="companion_user", password="secret_password", role=UserRole.USER))
    session = await db.create_app_session(user.id)
    token = session.token

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Flutter app calls /api/auth/start on behalf of authenticated user
        start_resp = await ac.post(
            "/api/auth/start",
            json={"origin_url": "http://localhost:6969"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert start_resp.status_code == 200
        start_data = start_resp.json()

        session_id = start_data["session_id"]
        auth_url = start_data["auth_url"]
        assert "ytm_sync_session=" in auth_url
        assert "callback=" in auth_url

        # Verify correct user owns the session
        auth_sess = await auth_service.get_session(session_id)
        assert auth_sess is not None
        assert auth_sess.user_id == user.id
        assert auth_sess.status == "pending"

        # 2. Companion extension polls /api/auth/session/{session_id}
        poll_resp = await ac.get(f"/api/auth/session/{session_id}")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["status"] == "pending"

        # 3. Companion extension captures headers and posts to /api/auth/callback
        raw_captured_headers = (
            "User-Agent: Mozilla/5.0 ...\n"
            "Cookie: SAPISID=mock_sapisid_token; HSID=mock_hsid_token; __Secure-3PAPISID=mock_3papisid;\n"
            "Authorization: SAPISIDHASH 1234567890_abcdef\n"
            "X-Goog-AuthUser: 0\n"
        )

        mock_ytm_result = {
            "connected": True,
            "message": "Connected to YouTube Music successfully.",
            "user_name": "Companion User",
            "account_email": "companion@gmail.com",
            "channel_id": "UC_companion_channel_123"
        }

        with patch("ytm_service.ytm_client.ytm_client.setup_auth", new_callable=AsyncMock) as mock_setup:
            mock_setup.return_value = mock_ytm_result

            cb_resp = await ac.post(
                "/api/auth/callback",
                json={
                    "session_id": session_id,
                    "raw_headers": raw_captured_headers
                }
            )
            assert cb_resp.status_code == 200
            cb_data = cb_resp.json()
            assert cb_data["status"] == "connected"
            assert cb_data["user_name"] == "Companion User"

        # 4. Verify YouTube Music connection is recorded in DB for this user
        ytm_acc = await db.get_ytm_account_by_user_id(user.id)
        assert ytm_acc is not None
        assert ytm_acc.status == "CONNECTED"
        assert ytm_acc.account_name == "Companion User"
        assert ytm_acc.account_identifier == "companion@gmail.com"

        # 5. Verify the session becomes unusable afterward (single-use constraint)
        reuse_resp = await ac.post(
            "/api/auth/callback",
            json={
                "session_id": session_id,
                "raw_headers": raw_captured_headers
            }
        )
        assert reuse_resp.status_code == 400

        # 6. Another user cannot complete or reuse this session
        attacker = await db.create_user(UserCreate(username="attacker", password="password", role=UserRole.USER))
        attacker_session = await db.create_app_session(attacker.id)
        attacker_token = attacker_session.token

        attacker_resp = await ac.post(
            "/api/auth/callback",
            json={
                "session_id": session_id,
                "raw_headers": raw_captured_headers
            },
            headers={"Authorization": f"Bearer {attacker_token}"}
        )
        assert attacker_resp.status_code == 400
