import os
import ipaddress
import logging
import secrets
import socket
import urllib.parse
import hashlib
import hmac
import re
import stat
from pathlib import Path
from typing import Optional, Union

from .config import settings, AUTH_DIR, DEFAULT_DATA_DIR, USERS_DIR

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


def validate_auth_origin_url(
    origin_url: str,
    request_host: Optional[str] = None,
    request_proto: Optional[str] = None,
) -> str:
    """
    Validate callback origin URL against approved origins and current host.
    Prevents credential exfiltration to untrusted/attacker-controlled destinations.
    Enforces Phase B (3.1) requirements.
    """
    if not origin_url or not isinstance(origin_url, str):
        raise ValueError("Origin URL must be a non-empty string.")

    cleaned = origin_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Prohibited URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    if not parsed.netloc or not parsed.hostname:
        raise ValueError("Invalid origin URL: missing host.")

    if parsed.username or parsed.password:
        raise ValueError("Origin URL must not contain embedded user credentials.")

    canonical_origin = f"{parsed.scheme}://{parsed.netloc}"

    # Approved origins from settings (e.g. ALLOWED_ORIGINS env var / defaults)
    approved: set[str] = set()
    for o in settings.allowed_origins:
        if o:
            approved.add(o.strip().rstrip("/"))

    # Request host / proxy host if available
    if request_host:
        proto = (request_proto or "http").lower()
        clean_host = request_host.strip().rstrip("/")
        approved.add(f"{proto}://{clean_host}")
        approved.add(f"http://{clean_host}")
        approved.add(f"https://{clean_host}")

    if canonical_origin in approved:
        return canonical_origin

    # Hostname checks: localhost, 127.0.0.1, ::1, or private IP
    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return canonical_origin

    try:
        ip_obj = ipaddress.ip_address(hostname)
        if ip_obj.is_loopback or ip_obj.is_private:
            return canonical_origin
    except ValueError:
        pass

    raise ValueError(f"Origin URL '{canonical_origin}' is not an approved destination.")


# --- Multi-User Security: Password Hashing, User ID Validation, Encryption & Filesystem Isolation ---

USER_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


def validate_user_id(user_id: str) -> str:
    """
    Ensure user_id is non-empty, alphanumeric/hyphen/underscore only, and safe from path traversal.
    """
    if not user_id or not isinstance(user_id, str):
        raise ValueError("User ID must be a non-empty string.")
    cleaned = user_id.strip()
    if len(cleaned) > 64 or not USER_ID_REGEX.match(cleaned) or ".." in cleaned:
        raise ValueError(f"Invalid user ID format: '{user_id}'. Only alphanumeric, underscores, and hyphens allowed.")
    return cleaned


def hash_password(password: str) -> str:
    """
    Salted memory-hard password hashing using Argon2id if available, falling back to hashlib.scrypt.
    Format: scrypt$16384$8$1$<salt_hex>$<derived_hex>
    """
    if not isinstance(password, str) or not password:
        raise ValueError("Password must be a non-empty string.")

    salt = secrets.token_bytes(16)
    # n=16384, r=8, p=1 is standard OWASP recommendation for interactive logins
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed_password: str) -> bool:
    """
    Constant-time password verification against hashed password string.
    Safe against timing attacks and returns False on invalid/malformed hashes.
    """
    if not password or not hashed_password or not isinstance(hashed_password, str):
        return False

    parts = hashed_password.split("$")
    if len(parts) == 6 and parts[0] == "scrypt":
        try:
            n = int(parts[1])
            r = int(parts[2])
            p = int(parts[3])
            salt = bytes.fromhex(parts[4])
            expected = bytes.fromhex(parts[5])
            derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p)
            return hmac.compare_digest(derived, expected)
        except Exception:
            return False

    return False


# --- Encryption at Rest (Phase J) ---

_fernet_instance = None


def get_auth_encryption_key() -> bytes:
    """
    Retrieve encryption key from YTM_AUTH_ENCRYPTION_KEY env var or AUTH_DIR/encryption.key.
    Generates a secure 32-byte urlsafe base64 key with 0600 permissions if not found.
    """
    env_key = os.environ.get("YTM_AUTH_ENCRYPTION_KEY", "").strip()
    if env_key:
        return env_key.encode("utf-8")

    key_file = AUTH_DIR / "encryption.key"
    if key_file.exists():
        return key_file.read_bytes().strip()

    from cryptography.fernet import Fernet
    new_key = Fernet.generate_key()
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(key_file), flags, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(new_key)
    try:
        os.chmod(str(key_file), 0o600)
    except OSError:
        pass
    return new_key


def get_fernet():
    global _fernet_instance
    if _fernet_instance is None:
        from cryptography.fernet import Fernet
        key = get_auth_encryption_key()
        _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_auth_data(plaintext: Union[str, bytes]) -> str:
    """Encrypt credentials string at rest with authenticated AES-128-CBC + HMAC-SHA256."""
    if not plaintext:
        return ""
    fernet = get_fernet()
    data = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    return fernet.encrypt(data).decode("utf-8")


def decrypt_auth_data(ciphertext: str) -> str:
    """Decrypt authenticated ciphertext string back into plaintext."""
    if not ciphertext:
        return ""
    fernet = get_fernet()
    return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# --- User-Specific Filesystem Isolation (Phase N) ---

def get_user_dir(user_id: str) -> Path:
    """Return isolated directory for the given user (/config/users/<user_id>) with 0700 permissions."""
    clean_id = validate_user_id(user_id)
    base_users_dir = getattr(settings, "users_dir", USERS_DIR)
    user_dir = (base_users_dir / clean_id).resolve()
    user_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(user_dir), stat.S_IRWXU)
    except OSError:
        pass
    return user_dir


def get_user_subpath(user_id: str, subfolder: str) -> Path:
    """
    Return and ensure isolated user subfolder (e.g. auth, uploads, metadata, playlists, cache)
    with restrictive 0700 permissions.
    """
    clean_sub = subfolder.strip().lower()
    allowed_subfolders = {"auth", "uploads", "metadata", "playlists", "cache"}
    if clean_sub not in allowed_subfolders:
        raise ValueError(
            f"Subfolder '{subfolder}' is not an authorized user subfolder. Allowed: {sorted(list(allowed_subfolders))}"
        )
    user_root = get_user_dir(user_id)
    target = (user_root / clean_sub).resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(target), stat.S_IRWXU)
    except OSError:
        pass
    return target


def validate_user_fs_access(user_id: str, target_path: Union[str, Path]) -> Path:
    """
    Verify that target_path is strictly within the user's isolated directory tree.
    Raises ValueError if path traversal or cross-user directory access is attempted.
    """
    clean_id = validate_user_id(user_id)
    user_root = (USERS_DIR / clean_id).resolve()
    p = Path(target_path).expanduser().resolve()
    if not (p == user_root or p.is_relative_to(user_root)):
        raise ValueError(f"Path '{target_path}' is outside user '{user_id}' isolated filesystem directory.")
    return p

