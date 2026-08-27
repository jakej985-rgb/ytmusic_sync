import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from pathlib import Path
from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings

@pytest_asyncio.fixture(autouse=True)
async def setup_test_environment(tmp_path: Path):
    settings.db_path = tmp_path / "test_api.db"
    settings.auth_file = tmp_path / "auth.json"
    db.db_path = settings.db_path
    await db.init_db()

@pytest.mark.asyncio
async def test_get_dashboard_status():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/status")
        assert response.status_code == 200
        data = response.json()
        assert "local_songs_count" in data
        assert "ytm_uploads_count" in data
        assert "missing_count" in data
        assert data["ytm_connected"] is False

@pytest.mark.asyncio
async def test_settings_and_folders():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Update folders
        post_resp = await ac.post("/api/folders", json={"folders": ["/home/user/Music"]})
        assert post_resp.status_code == 200
        assert post_resp.json()["folders"] == ["/home/user/Music"]

        # Get folders
        get_resp = await ac.get("/api/folders")
        assert get_resp.status_code == 200
        assert get_resp.json() == ["/home/user/Music"]

@pytest.mark.asyncio
async def test_get_uploads_and_jobs():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/uploads")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

        # Test 404 for non-existent job
        job_resp = await ac.get("/api/jobs/99999")
        assert job_resp.status_code == 404

@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}
