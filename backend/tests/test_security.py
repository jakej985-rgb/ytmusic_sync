import pytest
import pytest_asyncio
import socket
import re
from pathlib import Path
from unittest.mock import patch
from fastapi.routing import APIRoute
from httpx import AsyncClient, ASGITransport

from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.security import (
    verify_api_key_header,
    validate_fs_path,
    validate_network_url,
    validate_youtube_url,
    get_allowed_roots,
)
from ytm_service.scanner import fetch_cover_image_bytes


@pytest_asyncio.fixture(autouse=True)
async def setup_test_security_env(tmp_path: Path):
    settings.db_path = tmp_path / "test_sec.db"
    settings.auth_file = tmp_path / "auth.json"
    settings.data_dir = tmp_path / "data"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.db_path = settings.db_path
    await db.init_db()


# ============================================================================
# 1. AUTHENTICATION & API KEY TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_auth_missing_header_rejected():
    """Requests with no Authorization header to /api/* must return 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/status", headers={"X-No-Auth": "true"})
        assert res.status_code == 401
        assert res.headers.get("www-authenticate") == "Bearer"
        assert res.json() == {"detail": "Invalid or missing API key"}


@pytest.mark.asyncio
async def test_auth_invalid_and_malformed_headers():
    """Various malformed and invalid Authorization headers must all be rejected with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        test_headers = [
            {"Authorization": ""},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer wrong_secret_token"},
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "Token 12345"},
            {"Authorization": f"bearer {settings.api_key}extra"},
            # Deprecated X-API-Key alone
            {"X-API-Key": settings.api_key, "X-No-Auth": "true"},
        ]
        for hdr in test_headers:
            res = await ac.get("/api/status", headers=hdr)
            assert res.status_code == 401, f"Failed to reject invalid header: {hdr}"


@pytest.mark.asyncio
async def test_auth_valid_bearer_accepted():
    """Valid Bearer token must grant access to /api/*."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(
            "/api/status",
            headers={"Authorization": f"Bearer {settings.api_key}"}
        )
        assert res.status_code == 200
        assert "local_songs_count" in res.json()


def test_verify_api_key_unit():
    """Unit test for verify_api_key_header helper function."""
    assert not verify_api_key_header(None)
    assert not verify_api_key_header("")
    assert not verify_api_key_header("Bearer")
    assert not verify_api_key_header("Bearer ")
    assert not verify_api_key_header("Bearer wrong")
    assert not verify_api_key_header("Basic 12345")
    assert verify_api_key_header(f"Bearer {settings.api_key}")
    assert verify_api_key_header(f"bearer {settings.api_key}")  # Case-insensitive scheme


# ============================================================================
# 2. COMPLETE PROTECTED ROUTE COVERAGE
# ============================================================================

@pytest.mark.asyncio
async def test_all_api_routes_require_authentication():
    """
    Every registered route starting with /api/* must return 401 Unauthorized
    when called without valid credentials.
    """
    transport = ASGITransport(app=app)
    api_routes: list[tuple[str, str]] = []

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api/"):
            for method in route.methods:
                if method != "OPTIONS":
                    api_routes.append((method, route.path))

    assert len(api_routes) >= 25, f"Expected at least 25 API routes, found {len(api_routes)}"

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for method, path in api_routes:
            # Substitute any path parameters with dummy placeholders (e.g. {file_id} -> 1)
            concrete_path = re.sub(r"\{[a-zA-Z0-9_]+\}", "1", path)
            res = await ac.request(
                method=method,
                url=concrete_path,
                headers={"X-No-Auth": "true"}
            )
            assert res.status_code == 401, (
                f"Route {method} {concrete_path} did not return 401 when unauthenticated! "
                f"Got status {res.status_code}: {res.text}"
            )


# ============================================================================
# 3. PUBLIC ENDPOINTS & DOCS EXPOSURE
# ============================================================================

@pytest.mark.asyncio
async def test_health_endpoint_is_public():
    """The /health endpoint must remain public without credentials."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/health", headers={"X-No-Auth": "true"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "version" in data


@pytest.mark.asyncio
async def test_api_docs_disabled_by_default():
    """API docs (/docs, /redoc, /openapi.json) must be disabled in production by default."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        for doc_path in ("/docs", "/redoc", "/openapi.json"):
            res = await ac.get(doc_path, headers={"X-No-Auth": "true"})
            # Disabled routes return 404
            assert res.status_code == 404, f"{doc_path} should return 404 when docs are disabled"


# ============================================================================
# 4. CORS RESTRICTIONS
# ============================================================================

@pytest.mark.asyncio
async def test_cors_policy_enforcement():
    """CORS must allow configured origins and withhold headers from unauthorized origins."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Allowed origin
        allowed_res = await ac.get("/health", headers={"Origin": "http://localhost:8080"})
        assert allowed_res.headers.get("access-control-allow-origin") == "http://localhost:8080"

        # Disallowed external origin
        disallowed_res = await ac.get("/health", headers={"Origin": "https://malicious-site.com"})
        assert disallowed_res.headers.get("access-control-allow-origin") is None


# ============================================================================
# 5. FILESYSTEM CONFINEMENT & TRAVERSAL TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_fs_browse_confinement_and_traversal(tmp_path: Path):
    """Browse endpoint must block traversal, absolute escapes, and files."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Escape via relative traversal
        res1 = await ac.get(f"/api/fs/browse?path={tmp_path}/../../etc")
        assert res1.status_code == 403

        # 2. Escape via absolute unapproved path
        res2 = await ac.get("/api/fs/browse?path=/etc")
        assert res2.status_code == 403

        res3 = await ac.get("/api/fs/browse?path=/root")
        assert res3.status_code == 403

        # 3. Path pointing to a file instead of a directory
        test_file = tmp_path / "song.mp3"
        test_file.write_text("dummy")
        res_file = await ac.get(f"/api/fs/browse?path={test_file}")
        assert res_file.status_code == 400
        assert "not a directory" in res_file.json()["detail"].lower()

        # 4. Default browse without path
        res_default = await ac.get("/api/fs/browse")
        assert res_default.status_code == 200
        assert "current_path" in res_default.json()
        assert "allowed_roots" in res_default.json()


@pytest.mark.asyncio
async def test_fs_browse_symlink_escape(tmp_path: Path):
    """Symlinks escaping the allowed directory roots must be blocked."""
    music_dir = tmp_path / "safe_music"
    music_dir.mkdir(parents=True, exist_ok=True)
    symlink = music_dir / "evil_symlink"
    try:
        symlink.symlink_to("/etc", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/api/fs/browse?path={symlink}")
        assert res.status_code == 403


@pytest.mark.asyncio
async def test_artwork_endpoint_traversal_blocked(tmp_path: Path):
    """The /api/artwork/{filename} endpoint must block path traversal attacks."""
    art_dir = settings.data_dir / "artwork"
    art_dir.mkdir(parents=True, exist_ok=True)
    legit_img = art_dir / "cover123.jpg"
    legit_img.write_bytes(b"dummy image data")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Legitimate file
        res_ok = await ac.get("/api/artwork/cover123.jpg")
        assert res_ok.status_code == 200
        assert res_ok.content == b"dummy image data"

        # Traversal to escape artwork directory
        res_traversal1 = await ac.get("/api/artwork/..%2F..%2Fauth%2Fapi_key.txt")
        assert res_traversal1.status_code == 404

        res_traversal2 = await ac.get("/api/artwork/../../etc/passwd")
        assert res_traversal2.status_code == 404


def test_validate_fs_path_unit_tests(tmp_path: Path):
    """Thorough unit tests for validate_fs_path edge cases."""
    safe_root = tmp_path / "safe_root"
    safe_root.mkdir()
    allowed = [safe_root]

    # Valid child
    child = safe_root / "album" / "track.mp3"
    res = validate_fs_path(child, allowed_roots=allowed, allow_create_in_parent=True)
    assert res.is_relative_to(safe_root.resolve())

    # Traversal escape
    with pytest.raises(ValueError, match="outside approved"):
        validate_fs_path(safe_root / ".." / "outside", allowed_roots=allowed)

    # Empty path
    with pytest.raises(ValueError, match="Empty filesystem path"):
        validate_fs_path("", allowed_roots=allowed)

    # Null bytes
    with pytest.raises(ValueError, match="Null bytes"):
        validate_fs_path(f"{safe_root}/test\0.mp3", allowed_roots=allowed)

    # Nonexistent path when must_exist=True
    with pytest.raises(ValueError, match="does not exist"):
        validate_fs_path(safe_root / "nonexistent.mp3", allowed_roots=allowed, must_exist=True)


# ============================================================================
# 6. DESTINATION_DIR VALIDATION
# ============================================================================

@pytest.mark.asyncio
async def test_destination_dir_validation_rejects_unapproved_paths():
    """All endpoints accepting user-controlled destination_dir must reject unapproved roots."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Download track endpoint
        res1 = await ac.post(
            "/api/ytm/playlists/download-track",
            json={"video_id": "vid1", "title": "Song", "destination_dir": "/etc/target"}
        )
        assert res1.status_code == 400
        assert "destination_dir" in res1.json()["detail"].lower()

        # 2. Sync missing endpoint
        res2 = await ac.post("/api/ytm/playlists/PL1/sync-missing?destination_dir=/var/target")
        assert res2.status_code == 400
        assert "destination_dir" in res2.json()["detail"].lower()

        # 3. Resolve needs-help endpoint
        res3 = await ac.post(
            "/api/needs-help/vid1/resolve",
            json={"title": "Song", "destination_dir": "/root/target"}
        )
        assert res3.status_code == 400
        assert "destination_dir" in res3.json()["detail"].lower()


# ============================================================================
# 7. EXTERNAL URL & SSRF PROTECTIONS
# ============================================================================

def test_validate_youtube_url_allowed_hosts():
    """Legitimate YouTube and YouTube Music URLs must pass validation."""
    valid_urls = [
        "https://www.youtube.com/playlist?list=PL12345",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
    ]
    for u in valid_urls:
        validate_youtube_url(u)


def test_validate_youtube_url_domain_and_scheme_restrictions():
    """Non-YouTube domains and invalid schemes must be rejected."""
    invalid_cases = [
        ("https://evil.com/video", "not an authorized YouTube domain"),
        ("https://vimeo.com/12345", "not an authorized YouTube domain"),
        ("https://youtube.com.attacker.com/watch", "not an authorized YouTube domain"),
        ("file:///etc/passwd", "Prohibited URL scheme"),
        ("ftp://youtube.com/file", "Prohibited URL scheme"),
        ("gopher://youtube.com/", "Prohibited URL scheme"),
        ("https://user:pass@youtube.com/watch", "embedded user credentials"),
        ("https://youtube.com:8443/watch", "Custom network ports"),
        ("", "non-empty string"),
    ]
    for url, err_msg in invalid_cases:
        with pytest.raises(ValueError, match=err_msg):
            validate_youtube_url(url)


def test_ssrf_ip_filtering_comprehensive():
    """
    DNS resolution leading to loopback, private, link-local, multicast,
    or reserved addresses must be blocked across both IPv4 and IPv6.
    """
    test_ips = [
        ("127.0.0.1", "loopback"),
        ("127.0.1.1", "loopback"),
        ("::1", "loopback"),
        ("10.0.0.1", "private"),
        ("172.16.0.1", "private"),
        ("172.31.255.255", "private"),
        ("192.168.1.1", "private"),
        ("169.254.169.254", "link-local"),  # AWS/Cloud metadata
        ("fe80::1", "link-local"),
        ("224.0.0.1", "multicast"),
        ("ff02::1", "multicast"),
        ("240.0.0.1", "reserved"),
        ("0.0.0.0", "unspecified"),
        ("::", "unspecified"),
    ]

    for ip_addr, ip_type in test_ips:
        mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip_addr, 0))]
        with patch("socket.getaddrinfo", return_value=mock_addrinfo):
            with pytest.raises(ValueError, match=f"SSRF protection.*{ip_type}"):
                validate_youtube_url("https://www.youtube.com/watch?v=123")


# ============================================================================
# 8. COVER ART SSRF & LOCAL FILE LEAK PREVENTION
# ============================================================================

def test_fetch_cover_image_ssrf_blocked():
    """fetch_cover_image_bytes must reject internal network URLs and metadata services."""
    mock_addrinfo = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]
    with patch("socket.getaddrinfo", return_value=mock_addrinfo):
        result = fetch_cover_image_bytes(cover_url="http://internal-service.local/image.jpg")
        assert result is None, "Cover image fetch must reject SSRF target"


def test_fetch_cover_image_path_traversal_blocked():
    """fetch_cover_image_bytes must reject local paths outside approved roots."""
    # Attempt to read /etc/passwd or files outside allowed roots
    result = fetch_cover_image_bytes(cover_url="/etc/passwd")
    assert result is None, "Cover image fetch must reject files outside approved roots"
