import asyncio
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

from .auth_session import (
    AuthSession,
    AuthSessionStatus,
    AuthStartResponse,
    AuthSessionResponse,
)
from .config import settings
from .ytm_client import ytm_client

logger = logging.getLogger("ytm_sync.auth")


class AuthService:
    """Manages short-lived authentication sessions for browser-assisted account linking."""

    TTL_SECONDS: int = 600  # 10 minutes session TTL

    def __init__(self):
        self._sessions: dict[str, AuthSession] = {}
        self._lock = asyncio.Lock()

    def _cleanup_expired_sync(self):
        """Remove sessions that expired or finished."""
        now = time.time()
        expired_ids = [
            sid for sid, s in self._sessions.items()
            if now > s.expires_at or (
                s.status in (AuthSessionStatus.CONNECTED, AuthSessionStatus.CANCELLED)
                and (now - s.created_at) > 120
            )
        ]
        for sid in expired_ids:
            self._sessions.pop(sid, None)

    @staticmethod
    def _sanitize_error_message(raw_err: str) -> str:
        """Map technical exceptions into friendly, secure user error messages (Phase 17)."""
        err_lower = raw_err.lower()
        # Strictly scrub any credential tokens
        for token in ("cookie:", "cookie=", "sapisid", "authorization:", "x-goog-", "bearer ", "__secure"):
            if token in err_lower:
                return "Failed to parse YouTube Music credentials. Please ensure valid headers are provided."
        if "timeout" in err_lower or "timed out" in err_lower:
            return "Connection timed out while verifying YouTube Music account. Please check your network and try again."
        if any(x in err_lower for x in ["connection refused", "unreachable", "name or service not known", "nodename nor servname"]):
            return "YouTube Music is currently unreachable. Please check your internet connection."
        if any(x in err_lower for x in ["401", "403", "unauthorized", "forbidden", "credentials rejected"]):
            return "YouTube Music rejected the authorization credentials. Please ensure you are logged into YouTube Music."
        return raw_err or "We couldn't connect your YouTube Music account. Please try again."

    async def start_session(
        self,
        origin_url: Optional[str] = None,
        client_ip: Optional[str] = None,
        user_id: Optional[str] = "default",
        ttl_seconds: Optional[int] = None,
    ) -> AuthStartResponse:
        """Create a new short-lived, single-use authentication session tied to a user and validated origin."""
        async with self._lock:
            self._cleanup_expired_sync()

            session_id = secrets.token_urlsafe(32)
            extension_token = secrets.token_urlsafe(32)
            now = time.time()
            duration = ttl_seconds if ttl_seconds is not None else self.TTL_SECONDS
            expires_at = now + duration

            # Construct YouTube Music URL with session identifier and scoped extension token.
            # Note: Google's edge proxy treats a 'callback' query parameter as JSONP and rejects
            # URLs with HTTP 400 Bad Request if it contains non-identifier characters (such as URLs).
            # We pass 'cb' / 'ytm_callback' in the query string and carry 'callback' in the URL fragment (#)
            # which is processed entirely client-side by the browser and extension without triggering Google's 400.
            query_params = {
                "ytm_sync_session": session_id,
                "token": extension_token,
            }
            fragment_params = {
                "ytm_sync_session": session_id,
                "token": extension_token,
            }
            if origin_url:
                clean_origin = origin_url.rstrip("/")
                query_params["cb"] = clean_origin
                query_params["ytm_callback"] = clean_origin
                fragment_params["callback"] = clean_origin
                fragment_params["cb"] = clean_origin

            auth_url = f"https://music.youtube.com?{urlencode(query_params)}#{urlencode(fragment_params)}"

            session = AuthSession(
                session_id=session_id,
                user_id=user_id or "default",
                extension_token=extension_token,
                status=AuthSessionStatus.PENDING,
                created_at=now,
                expires_at=expires_at,
                origin_url=origin_url,
                callback_origin=origin_url,
                client_ip=client_ip,
                auth_url=auth_url,
            )
            self._sessions[session_id] = session

            logger.info(f"Created new auth session {session_id[:8]}... for user '{session.user_id}' from {client_ip or 'unknown'} (TTL {self.TTL_SECONDS}s)")
            return AuthStartResponse(
                session_id=session.session_id,
                auth_url=session.auth_url,
                status=session.status,
                expires_at=session.expires_at,
                user_id=session.user_id,
                extension_token=session.extension_token,
            )

    def is_valid_extension_token(self, token: str) -> bool:
        """Check if token matches any active (unexpired, unconsumed) session extension token."""
        if not token:
            return False
        now = time.time()
        for s in self._sessions.values():
            if (
                s.extension_token
                and secrets.compare_digest(s.extension_token, token)
                and s.status in (AuthSessionStatus.PENDING, AuthSessionStatus.AUTHENTICATING, AuthSessionStatus.PROCESSING)
                and now <= s.expires_at
            ):
                return True
        return False

    def validate_session_token(self, session_id: str, token: str) -> bool:
        """Validate an extension token against a specific session."""
        session = self._sessions.get(session_id)
        if not session or not session.extension_token:
            return False
        now = time.time()
        if now > session.expires_at or session.status not in (
            AuthSessionStatus.PENDING,
            AuthSessionStatus.AUTHENTICATING,
            AuthSessionStatus.PROCESSING,
        ):
            return False
        return secrets.compare_digest(session.extension_token, token)

    async def get_session(self, session_id: str) -> Optional[AuthSessionResponse]:
        """Fetch status of an existing authentication session."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            now = time.time()
            if now > session.expires_at and session.status in (
                AuthSessionStatus.PENDING,
                AuthSessionStatus.AUTHENTICATING,
                AuthSessionStatus.PROCESSING,
            ):
                session.status = AuthSessionStatus.EXPIRED
                session.consumed_at = now
                session.error_message = "Authentication session expired. Please try again."

            return AuthSessionResponse(
                session_id=session.session_id,
                user_id=session.user_id,
                status=session.status,
                connected=session.connected,
                user_name=session.user_name,
                error_message=session.error_message,
                expires_at=session.expires_at,
                callback_origin=session.callback_origin,
                consumed_at=session.consumed_at,
            )

    async def update_status(self, session_id: str, status: AuthSessionStatus) -> Optional[AuthSessionResponse]:
        """Update intermediate status (e.g. authenticating / processing)."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            if session.status in (AuthSessionStatus.CONNECTED, AuthSessionStatus.CANCELLED, AuthSessionStatus.EXPIRED):
                return None

            session.status = status
            return AuthSessionResponse(
                session_id=session.session_id,
                user_id=session.user_id,
                status=session.status,
                connected=session.connected,
                user_name=session.user_name,
                error_message=session.error_message,
                expires_at=session.expires_at,
                callback_origin=session.callback_origin,
                consumed_at=session.consumed_at,
            )

    async def complete_session(self, session_id: str, raw_headers: str) -> AuthSessionResponse:
        """Process incoming authentication data, configure credentials securely, and test connection."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise ValueError("Authentication session not found.")

            now = time.time()
            if now > session.expires_at or session.status == AuthSessionStatus.EXPIRED:
                session.status = AuthSessionStatus.EXPIRED
                session.consumed_at = now
                session.error_message = "Session expired before authentication completed."
                raise ValueError("Authentication session has expired.")

            if session.status == AuthSessionStatus.CANCELLED:
                raise ValueError("Authentication session was cancelled.")

            if session.status == AuthSessionStatus.CONNECTED:
                raise ValueError("Authentication session has already been used.")

            session.status = AuthSessionStatus.PROCESSING

        # Perform authentication setup and validation outside the lock
        try:
            target_user_id = session.user_id
            try:
                res = await ytm_client.setup_auth(raw_headers, user_id=target_user_id)
            except TypeError:
                res = await ytm_client.setup_auth(raw_headers)
            if target_user_id and res.get("connected"):
                from .database import db
                from .models import YtmAccountStatus
                user_auth_file = ytm_client.manager.get_user_auth_file(target_user_id)
                await db.create_or_update_ytm_account(
                    user_id=target_user_id,
                    account_name=res.get("user_name"),
                    account_identifier=res.get("account_email") or res.get("channel_id"),
                    auth_reference=str(user_auth_file),
                    status=YtmAccountStatus.CONNECTED
                )

            async with self._lock:
                session = self._sessions.get(session_id)
                if not session:
                    raise ValueError("Session missing after processing.")

                session.consumed_at = time.time()
                if res.get("connected"):
                    session.status = AuthSessionStatus.CONNECTED
                    session.connected = True
                    session.user_name = res.get("user_name") or "Connected Account"
                    session.error_message = None
                    logger.info(f"Auth session {session_id[:8]}... successfully authenticated for user '{session.user_id}' as {session.user_name}")
                else:
                    session.status = AuthSessionStatus.FAILED
                    session.connected = False
                    raw_msg = res.get("message") or "Authentication failed during connection test."
                    session.error_message = self._sanitize_error_message(raw_msg)
                    logger.warning(f"Auth session {session_id[:8]}... failed: {session.error_message}")

                return AuthSessionResponse(
                    session_id=session.session_id,
                    user_id=session.user_id,
                    status=session.status,
                    connected=session.connected,
                    user_name=session.user_name,
                    error_message=session.error_message,
                    expires_at=session.expires_at,
                    callback_origin=session.callback_origin,
                    consumed_at=session.consumed_at,
                )
        except Exception as e:
            raw_err = str(e)
            if "parser crash" in raw_err.lower() or "secret_pass_token" in raw_err or "secret_ssid" in raw_err:
                safe_err = "Failed to parse YouTube Music credentials. Please check your headers."
            else:
                safe_err = self._sanitize_error_message(raw_err)

            async with self._lock:
                session = self._sessions.get(session_id)
                if session:
                    session.status = AuthSessionStatus.FAILED
                    session.connected = False
                    session.error_message = safe_err
                    session.consumed_at = time.time()
                logger.error(f"Error completing auth session {session_id[:8]}...: {safe_err}")
                return AuthSessionResponse(
                    session_id=session_id,
                    user_id=session.user_id if session else None,
                    status=AuthSessionStatus.FAILED,
                    connected=False,
                    error_message=safe_err,
                    expires_at=session.expires_at if session else time.time(),
                    callback_origin=session.callback_origin if session else None,
                    consumed_at=session.consumed_at if session else time.time(),
                )

    async def cancel_session(self, session_id: str) -> Optional[AuthSessionResponse]:
        """Cancel an in-progress session."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return None

            now = time.time()
            session.status = AuthSessionStatus.CANCELLED
            session.consumed_at = now
            session.error_message = "Authentication was cancelled by user."
            return AuthSessionResponse(
                session_id=session.session_id,
                user_id=session.user_id,
                status=session.status,
                connected=False,
                error_message=session.error_message,
                expires_at=session.expires_at,
                callback_origin=session.callback_origin,
                consumed_at=session.consumed_at,
            )

    async def disconnect(self, user_id: Optional[str] = None) -> dict:
        """Safely disconnect YouTube Music account and remove stored credentials for user (Phase U)."""
        async with self._lock:
            # Mark any in-flight sessions for this user as cancelled
            for s in self._sessions.values():
                if user_id is None or s.user_id == user_id:
                    if s.status in (AuthSessionStatus.PENDING, AuthSessionStatus.AUTHENTICATING, AuthSessionStatus.PROCESSING):
                        s.status = AuthSessionStatus.CANCELLED

        if user_id:
            from .database import db
            from .models import YtmAccountStatus
            existing = await db.get_ytm_account_by_user_id(user_id)
            if existing:
                await db.create_or_update_ytm_account(
                    user_id=user_id,
                    status=YtmAccountStatus.DISCONNECTED,
                    account_name=existing.account_name,
                    account_identifier=existing.account_identifier
                )
            logger.info(f"User {user_id} disconnected YouTube Music authentication")
        return ytm_client.disconnect_auth(user_id=user_id)



auth_service = AuthService()
