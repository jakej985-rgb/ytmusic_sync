import pytest
from pathlib import Path
from httpx import AsyncClient
from ytm_service.config import settings

_orig_request = AsyncClient.request


async def _test_request(self, method, url, *args, **kwargs):
    headers = dict(kwargs.get("headers") or {})

    if "X-No-Auth" in headers:
        del headers["X-No-Auth"]
        saved_auth = self.headers.pop("Authorization", None)
        saved_x = self.headers.pop("X-API-Key", None)
        headers.pop("Authorization", None)
        headers.pop("X-API-Key", None)
        kwargs["headers"] = headers
        try:
            return await _orig_request(self, method, url, *args, **kwargs)
        finally:
            if saved_auth:
                self.headers["Authorization"] = saved_auth
            if saved_x:
                self.headers["X-API-Key"] = saved_x

    if "X-API-Key" in headers:
        saved_auth = self.headers.pop("Authorization", None)
        kwargs["headers"] = headers
        try:
            return await _orig_request(self, method, url, *args, **kwargs)
        finally:
            if saved_auth:
                self.headers["Authorization"] = saved_auth

    if "Authorization" not in headers and "Authorization" not in self.headers:
        headers["Authorization"] = f"Bearer {settings.api_key}"
        kwargs["headers"] = headers

    return await _orig_request(self, method, url, *args, **kwargs)


AsyncClient.request = _test_request


import tempfile

@pytest.fixture(autouse=True)
def configure_test_security(tmp_path: Path):
    """Ensure test runs with a known test API key and allowed directories."""
    settings.api_key = "test-secret-token-32bytes-hex123456"
    settings.auth_disabled = False
    settings.allowed_fs_roots = [
        tmp_path,
        tmp_path.parent,
        Path(tempfile.gettempdir()),
        Path("/music"),
        Path("/downloads"),
        Path("/home/user/Music"),
    ]
