import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport

from ytm_service.database import Database
from ytm_service.main import app

@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db_file = tmp_path / "test_uploads.db"
    db_instance = Database(db_file)
    await db_instance.init_db()
    with patch("ytm_service.main.db", db_instance):
        yield db_instance

@pytest.mark.asyncio
async def test_ytm_uploads_filtering_and_summary(temp_db):
    # 1. Untagged upload
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_untagged_1",
        "video_id": "vid_1",
        "title": "A Bar Song.mp3",
        "artist": None,
        "album": None,
        "duration": 180.0
    })

    # 2. Another untagged upload (Unknown Artist)
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_untagged_2",
        "video_id": "vid_2",
        "title": "Track 02",
        "artist": "Unknown Artist",
        "album": None,
        "duration": 200.0
    })

    # 3. Properly tagged upload
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_proper_1",
        "video_id": "vid_3",
        "title": "Clean Song",
        "artist": "Good Artist",
        "album": "Great Album",
        "thumbnail": "https://example.com/cover.jpg",
        "duration": 210.0
    })

    summary = await temp_db.get_ytm_uploads_summary()
    assert summary["total"] == 3
    assert summary["missing_metadata"] == 2
    assert summary["proper"] == 1

    missing_res = await temp_db.get_ytm_uploads(filter_type="missing_metadata")
    assert missing_res["total"] == 2
    assert {x.entity_id for x in missing_res["items"]} == {"ent_untagged_1", "ent_untagged_2"}

    proper_res = await temp_db.get_ytm_uploads(filter_type="proper")
    assert proper_res["total"] == 1
    assert proper_res["items"][0].entity_id == "ent_proper_1"

@pytest.mark.asyncio
async def test_replace_ytm_upload_endpoint(temp_db, tmp_path):
    # Insert test upload to replace
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_to_replace",
        "video_id": "vid_rep_1",
        "title": "Untagged_Song.mp3",
        "artist": None,
        "album": None,
        "duration": 190.0
    })

    # Create dummy file to simulate download
    dummy_file = tmp_path / "dummy_audio.mp3"
    dummy_file.write_bytes(b"dummy audio content")

    with patch("ytm_service.main.download_ytm_upload", AsyncMock(return_value=dummy_file)), \
         patch("ytm_service.main.write_metadata_tags") as mock_tag, \
         patch("ytm_service.main.ytm_client.upload_file", AsyncMock(return_value={"success": True, "response": "STATUS_SUCCEEDED"})), \
         patch("ytm_service.main.ytm_client.delete_upload", AsyncMock(return_value={"success": True})), \
         patch("ytm_service.main.ytm_client.fetch_and_cache_uploads", AsyncMock()):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ytm/uploads/ent_to_replace/replace",
                json={
                    "title": "Clean Song Title",
                    "artist": "Real Artist",
                    "album": "Real Album",
                    "track_number": 3,
                    "cover_url": "https://example.com/art.jpg"
                }
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["still_missing"] is False
        mock_tag.assert_called_once()

        # Fully tagged record should be deleted from untagged list in DB
        remaining = await temp_db.get_ytm_upload_by_entity_id("ent_to_replace")
        assert remaining is None

@pytest.mark.asyncio
async def test_replace_ytm_upload_still_missing_artwork(temp_db, tmp_path):
    # Insert test upload
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_missing_art",
        "video_id": "vid_art_1",
        "title": "Song_No_Art.mp3",
        "artist": None,
        "album": None,
        "thumbnail": None
    })

    dummy_file = tmp_path / "dummy_audio2.mp3"
    dummy_file.write_bytes(b"dummy audio content")

    with patch("ytm_service.main.download_ytm_upload", AsyncMock(return_value=dummy_file)), \
         patch("ytm_service.main.write_metadata_tags"), \
         patch("ytm_service.main.ytm_client.upload_file", AsyncMock(return_value={"success": True, "response": "STATUS_SUCCEEDED"})), \
         patch("ytm_service.main.ytm_client.delete_upload", AsyncMock(return_value={"success": True})), \
         patch("ytm_service.main.ytm_client.fetch_and_cache_uploads", AsyncMock()):

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/ytm/uploads/ent_missing_art/replace",
                json={
                    "title": "Updated Title",
                    "artist": "Updated Artist",
                    "album": "Updated Album",
                    "cover_url": None  # No artwork
                }
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["still_missing"] is True

        # Should keep record in DB with refreshed data!
        updated = await temp_db.get_ytm_upload_by_entity_id("ent_missing_art")
        assert updated is not None
        assert updated.title == "Updated Title"
        assert updated.artist == "Updated Artist"
        assert updated.thumbnail is None

@pytest.mark.asyncio
async def test_delete_ytm_upload_endpoint(temp_db):
    await temp_db.upsert_ytm_upload({
        "entity_id": "ent_delete_me",
        "video_id": "vid_del",
        "title": "Bad Upload",
        "artist": None,
        "album": None
    })

    with patch("ytm_service.main.ytm_client.delete_upload", AsyncMock(return_value={"success": True})):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete("/api/ytm/uploads/ent_delete_me")

        assert resp.status_code == 200
        assert (await temp_db.get_ytm_upload_by_entity_id("ent_delete_me")) is None
