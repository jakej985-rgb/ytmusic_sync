import time
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class AuthSessionStatus(str, Enum):
    PENDING = "pending"
    AUTHENTICATING = "authenticating"
    PROCESSING = "processing"
    CONNECTED = "connected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AuthSession(BaseModel):
    session_id: str
    user_id: Optional[str] = "default"
    extension_token: Optional[str] = None
    status: AuthSessionStatus = AuthSessionStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    expires_at: float
    origin_url: Optional[str] = None
    callback_origin: Optional[str] = None
    client_ip: Optional[str] = None
    auth_url: str
    connected: bool = False
    user_name: Optional[str] = None
    error_message: Optional[str] = None
    consumed_at: Optional[float] = None


class AuthStartRequest(BaseModel):
    origin_url: Optional[str] = None
    user_id: Optional[str] = None


class AuthStartResponse(BaseModel):
    session_id: str
    auth_url: str
    status: AuthSessionStatus
    expires_at: float
    user_id: Optional[str] = None
    extension_token: Optional[str] = None


class AuthSessionResponse(BaseModel):
    session_id: str
    user_id: Optional[str] = None
    status: AuthSessionStatus
    connected: bool
    user_name: Optional[str] = None
    error_message: Optional[str] = None
    expires_at: float
    callback_origin: Optional[str] = None
    consumed_at: Optional[float] = None


class AuthCompleteRequest(BaseModel):
    raw_headers: str


class AuthCallbackRequest(BaseModel):
    session_id: str
    raw_headers: str
    token: Optional[str] = None


class AuthCancelRequest(BaseModel):
    session_id: str
