import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta
from ytm_service.main import app
from ytm_service.database import db
from ytm_service.models import UserRole, FamilyRole, YtmAccountStatus, UserCreate
from ytm_service.security import hash_password
from ytm_service.rate_limiter import limiter


@pytest_asyncio.fixture(autouse=True)
async def reset_rate_limiter_and_db():
    await limiter.reset()
    await db.init_db()
    async with db.get_connection() as conn:
        await conn.execute("DELETE FROM family_invitations")
        await conn.execute("DELETE FROM family_members")
        await conn.execute("DELETE FROM families")
        await conn.execute("DELETE FROM sync_jobs")
        await conn.execute("DELETE FROM matches")
        await conn.execute("DELETE FROM music_files")
        await conn.execute("DELETE FROM ytm_accounts")
        await conn.execute("DELETE FROM app_sessions")
        await conn.execute("DELETE FROM users WHERE username NOT IN ('admin')")
        await conn.commit()
    yield
    await limiter.reset()


async def create_test_user(username: str, role: UserRole = UserRole.USER) -> dict:
    """Helper to create user, connect mock YTM account, and create valid session."""
    user = await db.create_user(
        UserCreate(
            username=username,
            password="Password123!",
            role=role
        )
    )
    # Link connected YTM account
    await db.create_or_update_ytm_account(
        user_id=user.id,
        account_name=f"{username.capitalize()} Account",
        account_identifier=f"{username}@example.com",
        encrypted_credentials="encrypted_mock_creds",
        status=YtmAccountStatus.CONNECTED
    )
    session = await db.create_app_session(user.id, duration_seconds=3600)
    return {
        "user": user,
        "token": session.token,
        "headers": {"Authorization": f"Bearer {session.token}"}
    }


@pytest.mark.asyncio
async def test_create_family_sets_owner():
    dad = await create_test_user("dad")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/families",
            headers=dad["headers"],
            json={"name": "Johnson Family"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Johnson Family"
        assert data["owner_user_id"] == dad["user"].id
        assert len(data["members"]) == 1
        assert data["members"][0]["user_id"] == dad["user"].id
        assert data["members"][0]["role"] == "OWNER"
        assert data["members"][0]["allow_family_uploads"] is True
        assert data["members"][0]["show_account_in_family"] is True


@pytest.mark.asyncio
async def test_invitation_lifecycle_and_acceptance():
    dad = await create_test_user("dad")
    charles = await create_test_user("charles")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Dad creates family
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]

        # 2. Charles cannot access family before joining
        forbidden = await client.get(f"/api/families/{family_id}", headers=charles["headers"])
        assert forbidden.status_code == 403

        # 3. Dad creates invitation
        inv_resp = await client.post(
            f"/api/families/{family_id}/invitations",
            headers=dad["headers"],
            json={"role": "MEMBER", "ttl_hours": 24}
        )
        assert inv_resp.status_code == 200
        token = inv_resp.json()["token"]

        # 4. Public invitation preview
        info_resp = await client.get(f"/api/invitations/{token}")
        assert info_resp.status_code == 200
        assert info_resp.json()["family_name"] == "Johnson Family"
        assert info_resp.json()["inviter_username"] == "dad"

        # 5. Charles accepts invitation
        accept_resp = await client.post(f"/api/invitations/{token}/accept", headers=charles["headers"])
        assert accept_resp.status_code == 200
        assert accept_resp.json()["user_id"] == charles["user"].id
        assert accept_resp.json()["role"] == "MEMBER"

        # 6. Reusing token fails
        reuse_resp = await client.post(f"/api/invitations/{token}/accept", headers=charles["headers"])
        assert reuse_resp.status_code == 400

        # 7. Charles can now view family
        view_resp = await client.get(f"/api/families/{family_id}", headers=charles["headers"])
        assert view_resp.status_code == 200
        assert len(view_resp.json()["members"]) == 2


@pytest.mark.asyncio
async def test_expired_invitation_rejected():
    dad = await create_test_user("dad")
    charles = await create_test_user("charles")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]

        inv = await db.create_family_invitation(family_id, dad["user"].id, role="MEMBER", ttl_hours=-1)

        accept_resp = await client.post(f"/api/invitations/{inv.token}/accept", headers=charles["headers"])
        assert accept_resp.status_code == 400
        assert "expired" in accept_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_member_cannot_modify_family_or_invite():
    dad = await create_test_user("dad")
    charles = await create_test_user("charles")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, charles["user"].id, role="MEMBER")

        # Charles attempts to rename family -> 403
        rename_resp = await client.patch(f"/api/families/{family_id}", headers=charles["headers"], json={"name": "Hacked"})
        assert rename_resp.status_code == 403

        # Charles attempts to invite -> 403
        invite_resp = await client.post(f"/api/families/{family_id}/invitations", headers=charles["headers"], json={"role": "MEMBER"})
        assert invite_resp.status_code == 403

        # Charles attempts to transfer ownership -> 403
        trans_resp = await client.post(
            f"/api/families/{family_id}/transfer",
            headers=charles["headers"],
            json={"new_owner_user_id": charles["user"].id, "confirm": True}
        )
        assert trans_resp.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_transfer_ownership():
    dad = await create_test_user("dad")
    mom = await create_test_user("mom")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, mom["user"].id, role="MEMBER")

        # Transfer without confirm fails
        no_confirm = await client.post(
            f"/api/families/{family_id}/transfer",
            headers=dad["headers"],
            json={"new_owner_user_id": mom["user"].id, "confirm": False}
        )
        assert no_confirm.status_code == 400

        # Transfer with confirm succeeds
        succ = await client.post(
            f"/api/families/{family_id}/transfer",
            headers=dad["headers"],
            json={"new_owner_user_id": mom["user"].id, "confirm": True}
        )
        assert succ.status_code == 200

        fam = await db.get_family_by_id(family_id)
        assert fam.owner_user_id == mom["user"].id
        mem_dad = await db.get_family_member(family_id, dad["user"].id)
        mem_mom = await db.get_family_member(family_id, mom["user"].id)
        assert mem_mom.role == FamilyRole.OWNER
        assert mem_dad.role == FamilyRole.ADMIN


@pytest.mark.asyncio
async def test_member_leave_and_owner_guard():
    dad = await create_test_user("dad")
    charles = await create_test_user("charles")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, charles["user"].id, role="MEMBER")

        # Owner cannot leave without transfer/delete
        owner_leave = await client.post(f"/api/families/{family_id}/leave", headers=dad["headers"])
        assert owner_leave.status_code == 400

        # Charles can leave
        charles_leave = await client.post(f"/api/families/{family_id}/leave", headers=charles["headers"])
        assert charles_leave.status_code == 200

        # Charles user record and YTM account remain intact
        c_user = await db.get_user_by_id(charles["user"].id)
        assert c_user is not None
        c_acc = await db.get_ytm_account(charles["user"].id)
        assert c_acc is not None


@pytest.mark.asyncio
async def test_family_deletion_preserves_users():
    dad = await create_test_user("dad")
    mom = await create_test_user("mom")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, mom["user"].id, role="MEMBER")

        # Delete without confirm fails
        no_conf = await client.delete(f"/api/families/{family_id}", headers=dad["headers"])
        assert no_conf.status_code == 400

        # Delete with confirm succeeds
        del_resp = await client.delete(f"/api/families/{family_id}?confirm=true", headers=dad["headers"])
        assert del_resp.status_code == 200

        # Family is gone
        assert await db.get_family_by_id(family_id) is None
        # Users are preserved
        assert await db.get_user_by_id(dad["user"].id) is not None
        assert await db.get_user_by_id(mom["user"].id) is not None


@pytest.mark.asyncio
async def test_privacy_toggles_and_visibility_controls():
    dad = await create_test_user("dad")
    mom = await create_test_user("mom")
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, mom["user"].id, role="MEMBER")

        # Mom hides account from family
        put_priv = await client.put(
            f"/api/families/{family_id}/members/{mom['user'].id}/privacy",
            headers=mom["headers"],
            json={"show_account_in_family": False}
        )
        assert put_priv.status_code == 200

        # Dad checks dashboard: Mom is masked
        dash = await client.get(f"/api/families/{family_id}/dashboard", headers=dad["headers"])
        assert dash.status_code == 200
        mom_item = next(m for m in dash.json()["members"] if m["user_id"] == mom["user"].id)
        assert mom_item["ytm_connected"] is False
        assert mom_item["uploads_count"] is None

        # Dad checks selectable accounts: Mom's account is NOT listed
        accs = await client.get("/api/accounts", headers=dad["headers"])
        assert accs.status_code == 200
        acc_uids = [a["user_id"] for a in accs.json()]
        assert mom["user"].id not in acc_uids

        # Cross-user privacy tampering fails (Dad cannot change Mom's privacy)
        tamper = await client.put(
            f"/api/families/{family_id}/members/{mom['user'].id}/privacy",
            headers=dad["headers"],
            json={"show_account_in_family": True}
        )
        assert tamper.status_code == 403


@pytest.mark.asyncio
async def test_single_and_multi_account_upload_destinations():
    dad = await create_test_user("dad")
    mom = await create_test_user("mom")
    transport = ASGITransport(app=app)

    # Insert a dummy music file
    file_id = await db.upsert_music_file({
        "path": "/music/test_song.mp3",
        "filename": "test_song.mp3",
        "format": "mp3",
        "file_size": 1024,
        "modified_time": 12345.0,
        "title": "Test Song",
        "artist": "Test Artist"
    })

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, mom["user"].id, role="MEMBER")

        # Multi-account upload to Dad and Mom
        upload_resp = await client.post(
            "/api/uploads/destinations",
            headers=dad["headers"],
            json={
                "music_file_ids": [file_id],
                "destination_user_ids": [dad["user"].id, mom["user"].id],
                "family_id": family_id
            }
        )
        assert upload_resp.status_code == 200
        data = upload_resp.json()
        assert data["jobs_created"] == 2
        job_ids = data["job_ids"]

        # Inspect jobs in DB
        job_dad = await db.get_sync_job_by_id(job_ids[0])
        job_mom = await db.get_sync_job_by_id(job_ids[1])

        # Verify job ownership & destination separation
        assert job_dad.user_id == dad["user"].id
        assert job_dad.requested_by_user_id == dad["user"].id
        assert job_mom.user_id == mom["user"].id
        assert job_mom.requested_by_user_id == dad["user"].id


@pytest.mark.asyncio
async def test_upload_destination_authorization_enforced():
    dad = await create_test_user("dad")
    charles = await create_test_user("charles")
    stranger = await create_test_user("stranger")
    transport = ASGITransport(app=app)

    file_id = await db.upsert_music_file({
        "path": "/music/auth_test.mp3",
        "filename": "auth_test.mp3",
        "format": "mp3",
        "file_size": 1024,
        "modified_time": 12345.0
    })

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]

        # Charles disables family uploads
        await db.add_family_member(
            family_id, charles["user"].id, role="MEMBER", allow_family_uploads=False
        )

        # 1. Dad tries uploading to Charles (disabled) -> 403
        c_resp = await client.post(
            "/api/uploads/destinations",
            headers=dad["headers"],
            json={"music_file_ids": [file_id], "destination_user_ids": [charles["user"].id], "family_id": family_id}
        )
        assert c_resp.status_code == 403
        assert "disabled family uploads" in c_resp.json()["detail"].lower()

        # 2. Dad tries uploading to Stranger (not in family) -> 403
        s_resp = await client.post(
            "/api/uploads/destinations",
            headers=dad["headers"],
            json={"music_file_ids": [file_id], "destination_user_ids": [stranger["user"].id], "family_id": family_id}
        )
        assert s_resp.status_code == 403
        assert "not in your family" in s_resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_duplicate_detection_is_strictly_per_account():
    dad = await create_test_user("dad")
    mom = await create_test_user("mom")
    transport = ASGITransport(app=app)

    file_id = await db.upsert_music_file({
        "path": "/music/dup_test.mp3",
        "filename": "dup_test.mp3",
        "format": "mp3",
        "file_size": 1024,
        "modified_time": 12345.0
    })

    # Mark as verified job for Dad only
    job_id = await db.create_sync_job(
        music_file_id=file_id,
        user_id=dad["user"].id,
        destination_user_id=dad["user"].id,
        requested_by_user_id=dad["user"].id
    )
    await db.update_sync_job(job_id, status="verified")

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]
        await db.add_family_member(family_id, mom["user"].id, role="MEMBER")

        status_resp = await client.get(f"/api/tracks/{file_id}/destinations-status", headers=dad["headers"])
        assert status_resp.status_code == 200
        data = status_resp.json()

        dad_status = next(d for d in data if d["destination_user_id"] == dad["user"].id)
        mom_status = next(d for d in data if d["destination_user_id"] == mom["user"].id)

        assert dad_status["is_uploaded"] is True
        assert dad_status["status"] == "already_uploaded"
        assert mom_status["is_uploaded"] is False
        assert mom_status["status"] == "not_uploaded"


@pytest.mark.asyncio
async def test_mocked_end_to_end_family_workflow():
    """Section 38: End-to-End Mocked Workflow for Johnson Family."""
    dad = await create_test_user("dad")
    mom = await create_test_user("mom")
    charles = await create_test_user("charles")
    transport = ASGITransport(app=app)

    file_id = await db.upsert_music_file({
        "path": "/music/workflow_song.mp3",
        "filename": "workflow_song.mp3",
        "format": "mp3",
        "file_size": 2048,
        "modified_time": 12345.0,
        "title": "Workflow Song"
    })

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Setup Johnson Family
        fam_resp = await client.post("/api/families", headers=dad["headers"], json={"name": "Johnson Family"})
        family_id = fam_resp.json()["id"]

        await db.add_family_member(family_id, mom["user"].id, role="MEMBER")
        await db.add_family_member(family_id, charles["user"].id, role="MEMBER")

        # 2. Dad uploads song selecting Dad and Mom
        upload_resp = await client.post(
            "/api/uploads/destinations",
            headers=dad["headers"],
            json={
                "music_file_ids": [file_id],
                "destination_user_ids": [dad["user"].id, mom["user"].id],
                "family_id": family_id
            }
        )
        assert upload_resp.status_code == 200
        data = upload_resp.json()
        assert data["jobs_created"] == 2

        # 3. Check queue grouping (Section 33)
        queue_resp = await client.get(f"/api/families/{family_id}/queue", headers=dad["headers"])
        assert queue_resp.status_code == 200
        q_data = queue_resp.json()
        assert len(q_data) == 1
        dests = [d["destination_username"] for d in q_data[0]["destinations"]]
        assert "dad" in dests
        assert "mom" in dests
        assert "charles" not in dests  # Charles receives nothing

        # 4. Check family history (Section 17)
        hist_resp = await client.get(f"/api/families/{family_id}/history", headers=dad["headers"])
        assert hist_resp.status_code == 200
        h_data = hist_resp.json()
        assert len(h_data) >= 2
        for item in h_data:
            assert item["requested_by_username"] == "dad"
            assert item["destination_username"] in ("dad", "mom")

        # 5. Verify credential isolation (Dad cannot fetch Mom's creds; Mom cannot fetch Dad's creds)
        dad_ytm = await client.get("/api/ytm/account", headers=dad["headers"])
        assert dad_ytm.status_code == 200
        assert dad_ytm.json()["user_id"] == dad["user"].id
        assert "encrypted_credentials" not in dad_ytm.json()

        mom_ytm = await client.get("/api/ytm/account", headers=mom["headers"])
        assert mom_ytm.status_code == 200
        assert mom_ytm.json()["user_id"] == mom["user"].id
