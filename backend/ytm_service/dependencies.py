from fastapi import Depends, HTTPException, Request
from typing import Optional

from .database import db
from .models import User, UserRole
from .security import verify_api_key_header


async def require_authenticated_user(request: Request) -> User:
    """
    Reusable dependency to resolve the authenticated application user.
    Strictly derives identity from server-validated session token or master API key.
    Client-supplied user_id in payloads is NEVER trusted for identity or ownership.
    """
    # 1. Check if middleware already attached the authenticated user
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User account is inactive")
        return user

    # 2. Inspect Authorization header
    auth_hdr = request.headers.get("Authorization")
    if not auth_hdr:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # 3. Check Session Token
    if auth_hdr.strip().lower().startswith("bearer "):
        token = auth_hdr.strip().split(" ", 1)[1].strip()
        user = await db.get_user_by_session_token(token)
        if user:
            if not user.is_active:
                raise HTTPException(status_code=403, detail="User account is inactive")
            request.state.user = user
            return user

    # 4. Check Master API Key (Resolves to default admin)
    if verify_api_key_header(auth_hdr):
        users = await db.list_users()
        admin_user = next((u for u in users if u.role == UserRole.ADMIN), None)
        if admin_user:
            request.state.user = admin_user
            return admin_user

    raise HTTPException(
        status_code=401,
        detail="Invalid or expired session token",
        headers={"WWW-Authenticate": "Bearer"}
    )


async def require_admin(current_user: User = Depends(require_authenticated_user)) -> User:
    """
    Reusable dependency ensuring the caller has administrator privileges.
    Raises 403 Forbidden for standard USER accounts.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Administrator privileges required")
    return current_user


async def get_optional_authenticated_user(request: Request) -> Optional[User]:
    """
    Returns the authenticated user if present via Session Token or API Key,
    otherwise returns None without raising 401.
    """
    if hasattr(request.state, "user") and request.state.user:
        return request.state.user

    auth_hdr = request.headers.get("Authorization")
    if not auth_hdr:
        return None

    if auth_hdr.strip().lower().startswith("bearer "):
        token = auth_hdr.strip().split(" ", 1)[1].strip()
        user = await db.get_user_by_session_token(token)
        if user and user.is_active:
            request.state.user = user
            return user

    if verify_api_key_header(auth_hdr):
        users = await db.list_users()
        admin_user = next((u for u in users if u.role == UserRole.ADMIN), None)
        if admin_user:
            request.state.user = admin_user
            return admin_user

    return None

