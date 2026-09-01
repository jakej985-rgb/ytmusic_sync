import os
import ipaddress
import logging
import secrets
import socket
import urllib.parse
from pathlib import Path
from typing import Optional, Union

from .config import settings

logger = logging.getLogger("ytm_sync.security")

ALLOWED_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "music.youtube.com",
    "m.youtube.com",
    "youtu.be"
}


def verify_api_key_header(auth_header: Optional[str]) -> bool:
    """
    Validate incoming Bearer token against settings.api_key using constant-time comparison.
    Canonical format: 'Authorization: Bearer <API_KEY>'.
    """
    if not auth_header or not settings.api_key:
        return False

    parts = auth_header.strip().split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False

    token = parts[1].strip()
    if not token:
        return False

    return secrets.compare_digest(token, settings.api_key)


def get_allowed_roots(custom_roots: Optional[list[Path]] = None) -> list[Path]:
    """Get the list of canonically resolved allowed filesystem roots."""
    raw_roots = custom_roots if custom_roots is not None else settings.allowed_fs_roots
    resolved_roots: list[Path] = []
    for r in raw_roots:
        try:
            resolved_roots.append(r.resolve())
        except Exception:
            resolved_roots.append(r)
    return resolved_roots


def validate_fs_path(
    path: Union[str, Path],
    allowed_roots: Optional[list[Path]] = None,
    must_exist: bool = False,
    allow_create_in_parent: bool = False
) -> Path:
    """
    Validate that a given filesystem path is safe and strictly contained within an approved root.
    Accounts for '..', absolute paths, symlinks, nonexistent paths where appropriate, and null bytes.
    Raises ValueError if path is invalid or attempts traversal outside approved roots.
    """
    raw_str = str(path).strip()
    if "\0" in raw_str:
        raise ValueError("Null bytes in filesystem path are prohibited")
    if not raw_str:
        raise ValueError("Empty filesystem path provided")

    roots = get_allowed_roots(allowed_roots)
    if not roots:
        raise ValueError("No approved filesystem roots configured")

    p = Path(raw_str).expanduser()

    if must_exist and not p.exists():
        raise ValueError(f"Path does not exist: {path}")

    resolved = p.resolve()

    # Verify that the canonical path is within at least one approved root
    is_contained = False
    for root in roots:
        try:
            root_res = root.resolve()
            if resolved == root_res or resolved.is_relative_to(root_res):
                is_contained = True
                break
        except Exception:
            continue

    if not is_contained:
        raise ValueError(
            f"Path '{path}' resolves to '{resolved}' which is outside approved directories: {[str(r) for r in roots]}"
        )

    # For non-existent paths, ensure existing parent/ancestor doesn't escape via symlink
    if not p.exists():
        curr = p
        while not curr.exists() and curr != curr.parent:
            curr = curr.parent
        if curr.exists():
            resolved_ancestor = curr.resolve()
            for root in roots:
                try:
                    root_res = root.resolve()
                    if curr == root_res or curr.is_relative_to(root_res):
                        if not (resolved_ancestor == root_res or resolved_ancestor.is_relative_to(root_res)):
                            raise ValueError(f"Path '{path}' symlink ancestor escapes approved root")
                except Exception:
                    continue

    return resolved


def validate_network_url(url: str, allowed_hosts: Optional[set[str]] = None) -> None:
    """
    Validate that a URL is well-formed, uses http/https, contains no userinfo/bad ports,
    matches allowed_hosts if specified, and resolves only to public non-internal IP addresses.
    Raises ValueError on any violation.
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string")

    raw_url = url.strip()
    parsed = urllib.parse.urlparse(raw_url)

    # 1. Scheme enforcement
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"Prohibited URL scheme '{scheme}'. Only http and https are allowed.")

    # 2. No embedded userinfo
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded user credentials are prohibited.")

    # 3. Port check
    if parsed.port and parsed.port not in (80, 443):
        raise ValueError("Custom network ports in URLs are prohibited.")

    # 4. Hostname validation
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("URL must contain a valid hostname.")

    if allowed_hosts is not None and hostname not in allowed_hosts:
        auth_type = "YouTube " if allowed_hosts == ALLOWED_YOUTUBE_HOSTS else ""
        raise ValueError(
            f"Domain '{hostname}' is not an authorized {auth_type}domain. "
            f"Allowed domains: {sorted(list(allowed_hosts))}"
        )

    # 5. DNS resolution and SSRF check
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"Failed to resolve domain '{hostname}': {e}")

    for entry in addr_info:
        ip_str = entry[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError(f"Invalid IP address returned for host: {ip_str}")

        if ip.is_loopback:
            raise ValueError(f"SSRF protection: '{hostname}' resolved to loopback IP {ip_str}")
        if ip.is_link_local:
            raise ValueError(f"SSRF protection: '{hostname}' resolved to link-local IP {ip_str}")
        if ip.is_unspecified:
            raise ValueError(f"SSRF protection: '{hostname}' resolved to unspecified IP {ip_str}")
        if ip.is_reserved:
            raise ValueError(f"SSRF protection: '{hostname}' resolved to reserved IP {ip_str}")
        if ip.is_multicast:
            raise ValueError(f"SSRF protection: '{hostname}' resolved to multicast IP {ip_str}")
        if ip.is_private:
            raise ValueError(f"SSRF protection: '{hostname}' resolved to private IP {ip_str}")


def validate_youtube_url(url: str) -> None:
    """Validate that a URL is a legitimate YouTube or YouTube Music URL."""
    validate_network_url(url, allowed_hosts=ALLOWED_YOUTUBE_HOSTS)
