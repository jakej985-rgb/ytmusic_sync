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
    status: AuthSessionStatus = AuthSessionStatus.PENDING
    created_at: float = Field(default_factory=time.time)
    expires_at: float
    origin_url: Optional[str] = None
    client_ip: Optional[str] = None
    auth_url: str
    connected: bool = False
    user_name: Optional[str] = None
    error_message: Optional[str] = None


class AuthStartRequest(BaseModel):
    origin_url: Optional[str] = None


class AuthStartResponse(BaseModel):
    session_id: str
    auth_url: str
    status: AuthSessionStatus
    expires_at: float


class AuthSessionResponse(BaseModel):
    session_id: str
    status: AuthSessionStatus
    connected: bool
    user_name: Optional[str] = None
    error_message: Optional[str] = None
    expires_at: float


class AuthCompleteRequest(BaseModel):
    raw_headers: str


class AuthCallbackRequest(BaseModel):
    session_id: str
    raw_headers: str


class AuthCancelRequest(BaseModel):
    session_id: str

