import pytest
import pytest_asyncio
from pathlib import Path
from httpx import AsyncClient, ASGITransport
from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.security import validate_youtube_url, validate_fs_path


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path: Path):
    settings.db_path = tmp_path / "test_sec.db"
    settings.auth_file = tmp_path / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()


@pytest.mark.asyncio
async def test_unauthenticated_api_request_rejected():
    """Unauthenticated requests to /api/* must return 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/status", headers={"X-No-Auth": "true"})
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers
        assert response.json()["detail"] == "Invalid or missing API key"


@pytest.mark.asyncio
async def test_invalid_api_key_rejected():
    """Requests with incorrect Bearer token or X-API-Key must return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res1 = await ac.get("/api/status", headers={"Authorization": "Bearer wrong-token-value"})
        assert res1.status_code == 401

        res2 = await ac.get("/api/status", headers={"X-API-Key": "wrong-token-value", "X-No-Auth": "true"})
        assert res2.status_code == 401


@pytest.mark.asyncio
async def test_valid_bearer_accepted_and_x_api_key_rejected():
    """Valid Bearer token grants access; deprecated X-API-Key alone returns 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res_bearer = await ac.get("/api/status", headers={"Authorization": f"Bearer {settings.api_key}"})
        assert res_bearer.status_code == 200

        res_x_key = await ac.get("/api/status", headers={"X-API-Key": settings.api_key, "X-No-Auth": "true"})
        assert res_x_key.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint_public_without_auth():
    """The /health endpoint must remain public for Docker and proxy probes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/health", headers={"X-No-Auth": "true"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "version" in data


@pytest.mark.asyncio
async def test_cors_restrictions():
    """CORS headers should reflect allowed origins and reject unconfigured origins."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Allowed origin
        res_allowed = await ac.get("/health", headers={"Origin": "http://localhost:8080"})
        assert res_allowed.headers.get("access-control-allow-origin") == "http://localhost:8080"

        # Disallowed origin
        res_disallowed = await ac.get("/health", headers={"Origin": "https://malicious.evil.com"})
        assert res_disallowed.headers.get("access-control-allow-origin") != "https://malicious.evil.com"


@pytest.mark.asyncio
async def test_fs_browse_path_traversal_and_confinement(tmp_path: Path):
    """Filesystem browsing must strictly reject traversal and paths outside approved roots."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Traversal outside roots
        res_etc = await ac.get("/api/fs/browse?path=/etc")
        assert res_etc.status_code == 403

        res_traversal = await ac.get(f"/api/fs/browse?path={tmp_path}/../../etc")
        assert res_traversal.status_code == 403

        # Legitimate path inside tmp_path / music
        music_dir = tmp_path / "music"
        music_dir.mkdir(parents=True, exist_ok=True)
        res_ok = await ac.get(f"/api/fs/browse?path={music_dir}")
        assert res_ok.status_code == 200
        assert res_ok.json()["current_path"] == str(music_dir)


@pytest.mark.asyncio
async def test_fs_browse_symlink_escape_blocked(tmp_path: Path):
    """Symlinks escaping the allowed root must be rejected."""
    music_dir = tmp_path / "music"
    music_dir.mkdir(parents=True, exist_ok=True)
    symlink_path = music_dir / "escape_link"
    try:
        symlink_path.symlink_to("/etc", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/fs/browse?path={symlink_path}")
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_destination_dir_validation():
    """Endpoints accepting destination_dir must reject paths outside approved roots."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Download playlist track endpoint
        res_track = await ac.post(
            "/api/ytm/playlists/download-track",
            json={
                "video_id": "test1234",
                "title": "Test Title",
                "destination_dir": "/etc/malicious"
            }
        )
        assert res_track.status_code == 400
        assert "destination_dir" in res_track.json()["detail"].lower()

        # Playlist sync endpoint
        res_sync = await ac.post(
            "/api/ytm/playlists/PL123/sync-missing?destination_dir=/etc/shadow"
        )
        assert res_sync.status_code == 400
        assert "destination_dir" in res_sync.json()["detail"].lower()

        # Needs help resolve endpoint
        res_help = await ac.post(
            "/api/needs-help/vid123/resolve",
            json={
                "title": "Track Title",
                "destination_dir": "/var/log"
            }
        )
        assert res_help.status_code == 400
        assert "destination_dir" in res_help.json()["detail"].lower()


@pytest.mark.asyncio
async def test_import_url_ssrf_and_domain_protection():
    """Import playlist URL endpoint must reject non-YouTube domains and SSRF vectors."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Localhost / internal
        res_local = await ac.post("/api/ytm/playlists/import-url", json={"url": "http://127.0.0.1:8080/api/status"})
        assert res_local.status_code == 400

        # Arbitrary domain
        res_evil = await ac.post("/api/ytm/playlists/import-url", json={"url": "https://evil-attacker.com/playlist"})
        assert res_evil.status_code == 400

        # Non-http scheme
        res_file = await ac.post("/api/ytm/playlists/import-url", json={"url": "file:///etc/passwd"})
        assert res_file.status_code == 400


def test_validate_youtube_url_unit():
    """Unit tests for validate_youtube_url helper."""
    # Valid YouTube URLs
    validate_youtube_url("https://www.youtube.com/playlist?list=PLtest123")
    validate_youtube_url("https://music.youtube.com/playlist?list=PLtest123")
    validate_youtube_url("https://youtu.be/dQw4w9WgXcQ")

    # Invalid domains
    with pytest.raises(ValueError, match="not an authorized YouTube domain"):
        validate_youtube_url("https://vimeo.com/12345")

    with pytest.raises(ValueError, match="not an authorized YouTube domain"):
        validate_youtube_url("https://attacker.com/fake?url=youtube.com")

    # Invalid scheme
    with pytest.raises(ValueError, match="Prohibited URL scheme"):
        validate_youtube_url("ftp://youtube.com/playlist")

    # Credentials in URL
    with pytest.raises(ValueError, match="embedded user credentials"):
        validate_youtube_url("https://admin:password@youtube.com/playlist")


def test_validate_fs_path_unit(tmp_path: Path):
    """Unit tests for validate_fs_path helper."""
    allowed = [tmp_path / "safe"]
    (tmp_path / "safe").mkdir()

    # Safe path inside allowed root
    p = validate_fs_path(tmp_path / "safe" / "subfolder", allowed_roots=allowed, allow_create_in_parent=True)
    assert p.is_relative_to((tmp_path / "safe").resolve())

    # Traversal escaping allowed root
    with pytest.raises(ValueError, match="outside approved"):
        validate_fs_path(tmp_path / "safe" / ".." / "other", allowed_roots=allowed)

    # Null byte attack
    with pytest.raises(ValueError, match="Null bytes"):
        validate_fs_path(f"{tmp_path}/safe\0evil", allowed_roots=allowed)
