import pytest
import tempfile
import stat
import os
from pathlib import Path
from httpx import AsyncClient, ASGITransport

from ytm_service.main import app
from ytm_service.database import db
from ytm_service.config import settings
from ytm_service.models import UserRole, UserCreate, UserUpdate, UserSettingsUpdate
from ytm_service.security import (
    hash_password,
    verify_password,
    validate_user_id,
    get_auth_encryption_key,
    encrypt_auth_data,
    decrypt_auth_data,
    get_user_dir,
    get_user_subpath,
    validate_user_fs_access,
)


@pytest.fixture(autouse=True)
async def init_test_database(tmp_path: Path):
    """Initialize clean database for user tests."""
    db_file = tmp_path / "test_users.db"
    settings.db_path = db_file
    settings.data_dir = tmp_path
    settings.users_dir = tmp_path / "users"
    settings.users_dir.mkdir(parents=True, exist_ok=True)
    await db.init_db()


# ----------------------------------------------------------------------------
# 1. Password Hashing & Verification (Phases D & E)
# ----------------------------------------------------------------------------
def test_password_hashing_and_verification():
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)
    assert hashed != password
    assert hashed.startswith("scrypt$")

    # Correct password succeeds
    assert verify_password(password, hashed) is True

    # Incorrect password fails
    assert verify_password("WrongPassword123!", hashed) is False

    # Empty or tampered hashes fail gracefully
    assert verify_password(password, "") is False
    assert verify_password(password, "invalid_hash_format") is False

    # Salt uniqueness: hashing same password twice produces distinct hashes
    hashed2 = hash_password(password)
    assert hashed != hashed2
    assert verify_password(password, hashed2) is True


# ----------------------------------------------------------------------------
# 2. User ID Validation & Path Traversal (Phase N)
# ----------------------------------------------------------------------------
def test_user_id_validation():
    # Valid user IDs
    assert validate_user_id("user_123") == "user_123"
    assert validate_user_id("admin") == "admin"
    assert validate_user_id("alice-bob") == "alice-bob"

    # Malformed or path traversal attempts rejected
    for invalid in ["../admin", "/etc/passwd", "user/12", "user\\12", "user\0name", "..", ".", " "]:
        with pytest.raises(ValueError):
            validate_user_id(invalid)


# ----------------------------------------------------------------------------
# 3. Encryption at Rest (Phase J)
# ----------------------------------------------------------------------------
def test_auth_encryption_at_rest(tmp_path: Path):
    settings.auth_dir = tmp_path / "auth"
    settings.auth_dir.mkdir(parents=True, exist_ok=True)

    raw_data = '{"Cookie": "HSID=xyz; SSID=abc", "User-Agent": "Mozilla/5.0"}'
    encrypted = encrypt_auth_data(raw_data)
    assert encrypted != raw_data

    decrypted = decrypt_auth_data(encrypted)
    assert decrypted == raw_data

    # Tampered ciphertext fails decryption
    tampered = encrypted[:-4] + "XXXX"
    with pytest.raises(Exception):
        decrypt_auth_data(tampered)


# ----------------------------------------------------------------------------
# 4. Filesystem Isolation (Phase N)
# ----------------------------------------------------------------------------
def test_user_filesystem_isolation(tmp_path: Path):
    user_id = "user_test_fs"
    user_dir = get_user_dir(user_id)
    assert user_dir.exists()
    # Directory permissions should be 0700
    file_stat = os.stat(user_dir)
    assert file_stat.st_mode & 0o777 == stat.S_IRWXU

    # Subpath isolation
    auth_dir = get_user_subpath(user_id, "auth")
    assert auth_dir.exists()
    assert str(auth_dir).startswith(str(user_dir))

    # Path escape attempts blocked
    with pytest.raises(ValueError):
        validate_user_fs_access(user_id, tmp_path.parent / "escape.txt")


# ----------------------------------------------------------------------------
# 5. User CRUD & Database Operations (Phase D)
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_user_database_crud():
    user = await db.create_user(
        UserCreate(username="alice", password="PasswordAlice123", role=UserRole.USER)
    )
    assert user.id is not None
    assert user.username == "alice"
    assert user.role == UserRole.USER
    assert user.is_active is True

    # Duplicate username raises error
    with pytest.raises(Exception):
        await db.create_user(
            UserCreate(username="alice", password="AnotherPassword123")
        )

    # Fetch by ID and username
    by_id = await db.get_user_by_id(user.id)
    assert by_id is not None
    assert by_id.username == "alice"

    by_uname = await db.get_user_by_username("alice")
    assert by_uname is not None
    assert by_uname.id == user.id

    # Update user (e.g. role and active status)
    updated = await db.update_user(user.id, UserUpdate(role=UserRole.ADMIN, is_active=False))
    assert updated is not None
    assert updated.role == UserRole.ADMIN
    assert updated.is_active is False

    # Delete user
    deleted = await db.delete_user(user.id)
    assert deleted is True
    assert await db.get_user_by_id(user.id) is None


# ----------------------------------------------------------------------------
# 6. Session Lifecycle & Token Authentication (Phases E & F)
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_app_session_lifecycle():
    user = await db.create_user(
        UserCreate(username="bob", password="PasswordBob123", role=UserRole.USER)
    )
    session = await db.create_app_session(user.id)
    assert session.token is not None
    assert session.user_id == user.id

    # Validate session token retrieves user
    found_user = await db.get_user_by_session_token(session.token)
    assert found_user is not None
    assert found_user.id == user.id

    # Revoke session
    await db.revoke_app_session(session.token)
    assert await db.get_user_by_session_token(session.token) is None

    # New session for deactivated user should not return user
    session2 = await db.create_app_session(user.id)
    await db.update_user(user.id, UserUpdate(is_active=False))
    assert await db.get_user_by_session_token(session2.token) is None


# ----------------------------------------------------------------------------
# 7. Login, Logout, /api/auth/me Endpoints (Phases E & G)
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_login_logout_and_profile():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create user via DB
        user = await db.create_user(
            UserCreate(username="charlie", password="CharliePassword123!", role=UserRole.USER)
        )

        # 1. Failed login (bad password)
        res_bad = await ac.post(
            "/api/auth/login",
            json={"username": "charlie", "password": "WrongPassword"},
            headers={"X-No-Auth": "1"}
        )
        assert res_bad.status_code == 401

        # 2. Successful login
        res_login = await ac.post(
            "/api/auth/login",
            json={"username": "charlie", "password": "CharliePassword123!"},
            headers={"X-No-Auth": "1"}
        )
        assert res_login.status_code == 200
        data = res_login.json()
        assert "token" in data
        assert data["user"]["username"] == "charlie"
        token = data["token"]

        # 3. GET /api/auth/me with session token
        res_me = await ac.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res_me.status_code == 200
        assert res_me.json()["username"] == "charlie"

        # 4. Logout
        res_logout = await ac.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res_logout.status_code == 200

        # 5. Access /api/auth/me after logout should fail (401)
        res_me_after = await ac.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert res_me_after.status_code == 401


# ----------------------------------------------------------------------------
# 8. Admin User Management Role Gate (Phase G)
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_admin_role_gate():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Create regular user and admin user
        regular_user = await db.create_user(
            UserCreate(username="regular_dave", password="DavePassword123!", role=UserRole.USER)
        )
        admin_user = await db.create_user(
            UserCreate(username="admin_dana", password="DanaPassword123!", role=UserRole.ADMIN)
        )

        reg_sess = await db.create_app_session(regular_user.id)
        admin_sess = await db.create_app_session(admin_user.id)

        # Regular user accessing /api/admin/users receives 403 Forbidden
        res_reg = await ac.get(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {reg_sess.token}"}
        )
        assert res_reg.status_code == 403

        # Admin user accessing /api/admin/users succeeds (200)
        res_admin = await ac.get(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_sess.token}"}
        )
        assert res_admin.status_code == 200
        users_list = res_admin.json()
        assert any(u["username"] == "regular_dave" for u in users_list)
        assert any(u["username"] == "admin_dana" for u in users_list)

        # Admin creates new user
        res_create = await ac.post(
            "/api/admin/users",
            json={"username": "new_eve", "password": "EvePassword123!", "role": "USER"},
            headers={"Authorization": f"Bearer {admin_sess.token}"}
        )
        assert res_create.status_code == 200
        assert res_create.json()["username"] == "new_eve"

        # Admin cannot delete own account
        res_del_self = await ac.delete(
            f"/api/admin/users/{admin_user.id}",
            headers={"Authorization": f"Bearer {admin_sess.token}"}
        )
        assert res_del_self.status_code == 400


# ----------------------------------------------------------------------------
# 9. Multi-User Replicated Playlist Isolation (Phases L & M)
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multi_user_replicated_playlist_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_a = await db.create_user(UserCreate(username="user_a", password="PasswordA123!", role=UserRole.USER))
        user_b = await db.create_user(UserCreate(username="user_b", password="PasswordB123!", role=UserRole.USER))

        sess_a = await db.create_app_session(user_a.id)
        sess_b = await db.create_app_session(user_b.id)

        # User A creates a replicated playlist
        res_create = await ac.post(
            "/api/replicated-playlists",
            json={
                "source_playlist_id": "PL_SOURCE_USER_A",
                "source_playlist_name": "User A Playlist",
                "destination_playlist_name": "User A Locker Replica",
                "enabled": True,
                "sync_interval_seconds": 300
            },
            headers={"Authorization": f"Bearer {sess_a.token}"}
        )
        assert res_create.status_code == 200
        rep_id_a = res_create.json()["id"]

        # User B listing replicated playlists sees 0 items
        res_list_b = await ac.get(
            "/api/replicated-playlists",
            headers={"Authorization": f"Bearer {sess_b.token}"}
        )
        assert res_list_b.status_code == 200
        assert len(res_list_b.json()) == 0

        # User B attempts to access User A's playlist by ID -> returns 404
        res_get_b = await ac.get(
            f"/api/replicated-playlists/{rep_id_a}",
            headers={"Authorization": f"Bearer {sess_b.token}"}
        )
        assert res_get_b.status_code == 404

        # User B attempts to update User A's playlist -> returns 404
        res_put_b = await ac.put(
            f"/api/replicated-playlists/{rep_id_a}",
            json={"enabled": False},
            headers={"Authorization": f"Bearer {sess_b.token}"}
        )
        assert res_put_b.status_code == 404

        # User B attempts to delete User A's playlist -> returns 404
        res_del_b = await ac.delete(
            f"/api/replicated-playlists/{rep_id_a}",
            headers={"Authorization": f"Bearer {sess_b.token}"}
        )
        assert res_del_b.status_code == 404


# ----------------------------------------------------------------------------
# 10. Multi-User Settings Isolation (Phase Q)
# ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_multi_user_settings_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_x = await db.create_user(UserCreate(username="user_x", password="PasswordX123!", role=UserRole.USER))
        user_y = await db.create_user(UserCreate(username="user_y", password="PasswordY123!", role=UserRole.USER))

        sess_x = await db.create_app_session(user_x.id)
        sess_y = await db.create_app_session(user_y.id)

        # User X modifies their settings
        res_put_x = await ac.put(
            "/api/settings",
            json={
                "auto_upload": True,
                "scan_interval_minutes": 15,
                "music_folders": ["/music/user_x"]
            },
            headers={"Authorization": f"Bearer {sess_x.token}"}
        )
        assert res_put_x.status_code == 200
        settings_x = res_put_x.json()
        assert settings_x["auto_upload"] is True
        assert settings_x["music_folders"] == ["/music/user_x"]

        # User Y reads their settings -> should remain defaults, unpolluted by User X
        res_get_y = await ac.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {sess_y.token}"}
        )
        assert res_get_y.status_code == 200
        settings_y = res_get_y.json()
        assert settings_y["auto_upload"] is False
        assert settings_y["music_folders"] == []
