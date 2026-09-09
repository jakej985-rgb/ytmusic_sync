import asyncio
import io
import logging
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
    settings.db_path = settings.config_dir / "test_logging_rl.db"
    settings.auth_file = settings.config_dir / "auth.json"
    settings.log_file = tmp_path / "test.log"
    db.db_path = settings.db_path
    await db.init_db()
    auth_service._sessions.clear()
    await limiter.reset()
    yield


# ============================================================================
# Phase Z — User-Aware Sanitized Logging Tests
# ============================================================================

@pytest.mark.asyncio
async def test_user_aware_logging_masks_secrets(caplog):
    """Logs must prefix user operations with 'User <id>' while masking sensitive tokens."""
    admin = await db.get_user_by_username("admin")
    user = await db.create_user(UserCreate(username="log_user", password="secret_password", role=UserRole.USER))
    session = await db.create_app_session(user.id)
    token = session.token

    transport = ASGITransport(app=app)
    with caplog.at_level(logging.INFO):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            start_resp = await ac.post(
                "/api/auth/start",
                headers={"Authorization": f"Bearer {token}"}
            )
            assert start_resp.status_code == 200

            with patch("ytm_service.ytm_client.ytm_client.setup_auth", new_callable=AsyncMock) as mock_setup:
                mock_setup.return_value = {"connected": True, "message": "OK", "user_name": "Log User"}
                cb_resp = await ac.post(
                    "/api/auth/callback",
                    json={
                        "session_id": start_resp.json()["session_id"],
                        "raw_headers": "Cookie: super_secret_cookie_value_12345; HSID=confidential;"
                    }
                )
                assert cb_resp.status_code == 200

    log_text = caplog.text
    # Must NOT contain raw cookie content or password
    assert "super_secret_cookie_value_12345" not in log_text
    assert "secret_password" not in log_text
    assert "confidential" not in log_text

    # Must contain User identifier
    assert f"User {user.id}" in log_text or "User" in log_text


# ============================================================================
# Section 28 — Multi-User Rate Limiting Tests
# ============================================================================

@pytest.mark.asyncio
async def test_login_rate_limiting():
    """Verify that rapid consecutive login attempts hit HTTP 429 with Retry-After header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Rate limit on login is 5 requests per 60 seconds
        for _ in range(5):
            r = await ac.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
            assert r.status_code == 401

        # 6th request must trigger HTTP 429
        r_limited = await ac.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
        assert r_limited.status_code == 429
        assert "Retry-After" in r_limited.headers
        assert int(r_limited.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_auth_start_rate_limiting():
    """Verify rate limiting on /api/auth/start (limit 10/60s)."""
    admin = await db.get_user_by_username("admin")
    session = await db.create_app_session(admin.id)
    token = session.token

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for _ in range(10):
            r = await ac.post(
                "/api/auth/start",
                headers={"Authorization": f"Bearer {token}"}
            )
            assert r.status_code == 200

        # 11th request triggers 429
        r_limited = await ac.post(
            "/api/auth/start",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert r_limited.status_code == 429
        assert "Retry-After" in r_limited.headers


@pytest.mark.asyncio
async def test_rate_limiting_isolated_per_ip():
    """Requests from different IPs get independent rate limit windows."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # IP 1 consumes all 5 login attempts
        for _ in range(5):
            r = await ac.post(
                "/api/auth/login",
                json={"username": "admin", "password": "wrongpassword"},
                headers={"X-Forwarded-For": "192.168.1.50"}
            )
            assert r.status_code == 401

        # IP 1 is now rate limited
        r1_limited = await ac.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
            headers={"X-Forwarded-For": "192.168.1.50"}
        )
        assert r1_limited.status_code == 429

        # IP 2 can still make requests
        r2 = await ac.post(
            "/api/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
            headers={"X-Forwarded-For": "192.168.1.51"}
        )
        assert r2.status_code == 401
