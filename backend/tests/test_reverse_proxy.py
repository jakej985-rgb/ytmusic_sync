import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.models import UserRole, UserCreate
from ytm_service.auth_service import auth_service
from ytm_service.rate_limiter import limiter


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env(tmp_path: Path):
    settings.config_dir = tmp_path / "config"
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path = settings.config_dir / "test_proxy.db"
    settings.auth_file = settings.config_dir / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()
    auth_service._sessions.clear()
    await limiter.reset()
    yield


# ============================================================================
# Section 30 — Reverse Proxy and Traefik Header Handling Tests
# ============================================================================

@pytest.mark.asyncio
async def test_reverse_proxy_https_callback_url_generation():
    """
    When behind Traefik / Nginx terminating TLS, X-Forwarded-Proto and
    X-Forwarded-Host must be respected to construct https:// URLs.
    """
    admin = await db.get_user_by_username("admin")
    session = await db.create_app_session(admin.id)
    token = session.token

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "music.example.com",
            "X-Forwarded-For": "203.0.113.195",
        }
        resp = await ac.post("/api/auth/start", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        # The returned auth URL contains the URL-encoded HTTPS callback URL
        assert "callback=https%3A%2F%2Fmusic.example.com" in data["auth_url"]


@pytest.mark.asyncio
async def test_reverse_proxy_cors_preflight():
    """CORS preflight requests must allow configured origins with necessary headers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.options(
            "/api/auth/login",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            }
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers


@pytest.mark.asyncio
async def test_reverse_proxy_traefik_client_ip_forwarding():
    """Client IP extraction properly prioritizes the first entry in X-Forwarded-For."""
    from ytm_service.rate_limiter import get_client_ip
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (b"x-forwarded-for", b"198.51.100.42, 10.0.0.1"),
        ],
    }
    req = Request(scope)
    ip = get_client_ip(req)
    assert ip == "198.51.100.42"
