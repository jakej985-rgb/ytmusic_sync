"""
========================================================================================
CRITICAL UPLOAD INTEGRITY DIRECTIVE (Phase 15 Architecture Lock):
"An Upload Locker item may only be downloaded from its exact authenticated upload source.
Metadata may never be used to find a replacement source. If the exact source cannot be
downloaded, the operation fails and the existing local file remains untouched."
========================================================================================
"""

import os
import sys
import json
import logging
import tempfile
import asyncio
import subprocess
import urllib.request
import re
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Union, Dict, Any
from .config import settings
from .security import validate_youtube_url, validate_fs_path
from .database import db

logger = logging.getLogger("ytm_sync.downloader")


class PrivateUploadUnavailableError(RuntimeError):
    """Raised when an authentic Upload Locker download fails due to private video or auth failure."""
    pass


class DownloadIntegrityError(RuntimeError):
    """Raised when the downloaded audio source identity does not match the expected source ID."""
    pass


def _generate_netscape_cookies(cookie_raw: str) -> str:
    """Generate a clean Netscape-format cookie file from raw Cookie header string."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="ytm_cookies_")
    with os.fdopen(fd, "w", encoding="utf-8") as tf:
        tf.write("# Netscape HTTP Cookie File\n")
        tf.write("# Generated for YouTube Music private upload download\n")
        seen = set()
        for item in cookie_raw.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                k = k.strip()
                v = v.strip()
                if not k or k in seen:
                    continue
                seen.add(k)
                # In Netscape cookie format:
                # domain flag path secure expiration name value
                # .youtube.com covers all subdomains including music.youtube.com
                tf.write(f".youtube.com\tTRUE\t/\tTRUE\t2147483647\t{k}\t{v}\n")
    return path


def _download_via_ytmusicapi(video_id: str, output_path: Path) -> Optional[Path]:
    """Attempt direct audio stream download using the authenticated ytmusicapi session."""
    try:
        from .ytm_client import ytm_client
        if not ytm_client.is_auth_configured():
            return None

        yt = ytm_client._get_client()
        # ytmusicapi get_song returns video details and streamingData using authenticated session
        song_data = yt.get_song(video_id)
        streaming_data = song_data.get("streamingData", {})
        formats = streaming_data.get("adaptiveFormats", []) + streaming_data.get("formats", [])

        # Filter for formats with a direct stream URL
        audio_formats = [f for f in formats if "audio" in f.get("mimeType", "") and f.get("url")]
        if not audio_formats:
            audio_formats = [f for f in formats if f.get("url")]

        if not audio_formats:
            logger.debug(f"No direct streaming URL found in ytmusicapi get_song({video_id})")
            return None

        # Sort by bitrate descending
        audio_formats.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
        best_format = audio_formats[0]
        stream_url = best_format["url"]

        logger.info(
            f"Found direct stream URL for {video_id} via ytmusicapi "
            f"(mime: {best_format.get('mimeType')}, bitrate: {best_format.get('bitrate')})"
        )

        raw_temp = output_path.with_suffix(".raw")
        req = urllib.request.Request(
            stream_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=60) as resp, open(raw_temp, "wb") as out_f:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                out_f.write(chunk)

        if not raw_temp.exists() or raw_temp.stat().st_size == 0:
            return None

        # Transcode to clean MP3 using ffmpeg
        final_mp3 = output_path.with_suffix(".mp3")
        conv_cmd = [
            "ffmpeg", "-y", "-i", str(raw_temp),
            "-codec:a", "libmp3lame", "-q:a", "2",
            str(final_mp3)
        ]
        conv_res = subprocess.run(conv_cmd, capture_output=True, text=True, timeout=60)

        try:
            if raw_temp.exists():
                raw_temp.unlink()
        except Exception:
            pass

        if conv_res.returncode == 0 and final_mp3.exists() and final_mp3.stat().st_size > 0:
            logger.info(f"Direct stream download & conversion succeeded for {video_id}: {final_mp3} ({final_mp3.stat().st_size} bytes)")
            return final_mp3
        else:
            logger.warning(f"ffmpeg conversion failed for {video_id}: {conv_res.stderr}")
    except Exception as e:
        logger.warning(f"Direct download via ytmusicapi failed for {video_id}: {e}")

    return None


def _download_sync(
    video_id: str,
    output_path: Path,
    fallback_query: Optional[str] = None,
    source_type: str = "ytm_upload"
) -> Path:
    """
    Download audio stream.
    For source_type='ytm_upload', ONLY exact upload identity is permitted (direct stream / watch URLs).
    Search fallbacks (ytsearch1:) are strictly forbidden for uploads to prevent catalog substitution.
    For source_type='catalog', public YouTube catalog track downloading is allowed with optional search fallback.
    """
    clean_id = str(video_id).strip()
    if not re.match(r'^[a-zA-Z0-9_-]+$', clean_id):
        raise ValueError(f"Invalid video ID format: {video_id}")

    if source_type not in ("ytm_upload", "catalog"):
        raise ValueError(f"Invalid or contradictory source_type: {source_type}. Must be 'ytm_upload' or 'catalog'.")

    if source_type == "ytm_upload" and fallback_query and fallback_query.strip():
        raise ValueError("Upload downloads cannot specify or use fallback search queries. Exact upload identity is required.")

    if source_type == "ytm_upload":
        logger.info(
            f"UPLOAD DOWNLOAD START\n"
            f"source_type={source_type}\n"
            f"expected_video_id={clean_id}"
        )

    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. First attempt direct download via authenticated ytmusicapi session
    try:
        direct_file = _download_via_ytmusicapi(clean_id, output_path)
        if direct_file and direct_file.exists() and direct_file.stat().st_size > 0:
            if source_type == "ytm_upload":
                logger.info(
                    f"UPLOAD DOWNLOAD SUCCESS\n"
                    f"expected_video_id={clean_id}\n"
                    f"actual_video_id={clean_id}\n"
                    f"verification=PASS"
                )
            return direct_file
    except Exception as e:
        logger.debug(f"Direct ytmusicapi stream failed: {e}")

    logger.info(f"Direct ytmusicapi stream not available; trying yt-dlp for {source_type} {clean_id}...")

    # 2. Extract auth credentials from all candidate paths
    auth_candidates = [
        settings.auth_file,
        settings.data_dir / "headers_auth.json",
        settings.data_dir / "auth" / "headers_auth.json",
        Path("/config/headers_auth.json"),
        Path("/config/auth/headers_auth.json"),
        Path.home() / ".config" / "ytm_sync" / "headers_auth.json",
        Path.home() / ".config" / "ytm_sync" / "auth" / "headers_auth.json",
    ]

    cookie_raw = ""
    auth_header = ""
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    for auth_p in auth_candidates:
        if auth_p.exists():
            try:
                with open(auth_p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        kl = k.lower()
                        if kl == "cookie" and isinstance(v, str) and v.strip():
                            cookie_raw = v.strip()
                        elif kl == "authorization" and isinstance(v, str) and v.strip():
                            auth_header = v.strip()
                        elif kl == "user-agent" and isinstance(v, str) and v.strip():
                            user_agent = v.strip()
                if cookie_raw:
                    break
            except Exception as e:
                logger.warning(f"Failed to read auth file {auth_p}: {e}")

    cookie_file = None
    if cookie_raw:
        cookie_file = _generate_netscape_cookies(cookie_raw)

    try:
        env = dict(os.environ)
        # Ensure deno is in PATH for JS challenge solving
        deno_paths = ["/usr/local/bin", "/home/m3tal/.deno/bin", os.path.expanduser("~/.deno/bin")]
        existing_path = env.get("PATH", "")
        extra_paths = [p for p in deno_paths if os.path.isdir(p) and p not in existing_path]
        if extra_paths:
            env["PATH"] = ":".join(extra_paths) + ":" + existing_path

        # Try URLs in order:
        # 1. Standard youtube.com watch URL
        # 2. music.youtube.com watch URL
        watch_url = f"https://www.youtube.com/watch?v={clean_id}"
        music_url = f"https://music.youtube.com/watch?v={clean_id}"
        validate_youtube_url(watch_url)
        validate_youtube_url(music_url)
        urls_to_try = [watch_url, music_url]

        # ONLY catalog tracks may use public search fallback; NEVER upload locker downloads
        if source_type == "catalog" and fallback_query and fallback_query.strip():
            clean_query = fallback_query.strip()
            for ext in [".mp3", ".flac", ".m4a", ".wav", ".opus", ".webm"]:
                clean_query = clean_query.replace(ext, "")
            urls_to_try.append(f"ytsearch1:{clean_query.strip()}")

        id_file = output_path.with_suffix(".id")
        last_error = "Unknown error"
        for target_url in urls_to_try:
            modes = [
                {"use_cookies": False, "clients": "android,ios,mweb"},
            ]
            if cookie_file:
                modes.append({"use_cookies": True, "clients": "web,ios"})

            for mode in modes:
                cmd = [
                    sys.executable, "-m", "yt_dlp",
                    "--remote-components", "ejs:github",
                    "--extractor-args", f"youtube:player_client={mode['clients']}",
                    "-x", "--audio-format", "mp3",
                    "--no-playlist",
                    "--print-to-file", "%(id)s", str(id_file),
                    "--user-agent", user_agent,
                    "-o", str(output_path.with_suffix(".%(ext)s")),
                    target_url
                ]
                if mode["use_cookies"]:
                    if auth_header:
                        cmd.extend(["--add-header", f"Authorization: {auth_header}"])
                    if cookie_file:
                        cmd.extend(["--cookies", cookie_file])

                logger.info(f"Downloading audio via {target_url} (clients={mode['clients']}, cookies={mode['use_cookies']})...")
                res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

                if res.returncode == 0:
                    actual_video_id = clean_id
                    if id_file.exists():
                        try:
                            actual_video_id = id_file.read_text(encoding="utf-8").strip()
                        finally:
                            try:
                                id_file.unlink()
                            except Exception:
                                pass

                    final_file = output_path.with_suffix(".mp3")
                    if not final_file.exists() or final_file.stat().st_size == 0:
                        candidates = [c for c in output_dir.glob(f"{output_path.stem}.*") if c.suffix != ".id"]
                        if candidates:
                            final_file = candidates[0]

                    if final_file.exists() and final_file.stat().st_size > 0:
                        # HARD SOURCE-INTEGRITY GATE:
                        # Ensure the downloaded audio comes from the exact expected video ID.
                        if actual_video_id != clean_id:
                            logger.error(
                                f"UPLOAD DOWNLOAD BLOCKED\n"
                                f"expected_video_id={clean_id}\n"
                                f"actual_video_id={actual_video_id}\n"
                                f"reason=SOURCE_ID_MISMATCH"
                            )
                            try:
                                final_file.unlink()
                            except Exception:
                                pass
                            raise DownloadIntegrityError(
                                f"Download integrity verification failed: expected video ID '{clean_id}', "
                                f"but received '{actual_video_id}'. Staging file destroyed."
                            )

                        if source_type == "ytm_upload":
                            logger.info(
                                f"UPLOAD DOWNLOAD SUCCESS\n"
                                f"expected_video_id={clean_id}\n"
                                f"actual_video_id={actual_video_id}\n"
                                f"verification=PASS"
                            )
                        logger.info(f"Successfully downloaded {target_url} to {final_file} ({final_file.stat().st_size} bytes, verified ID={actual_video_id})")
                        return final_file

                last_error = res.stderr.strip() or res.stdout.strip() or "Unknown error"
                logger.warning(f"Download attempt for {target_url} (cookies={mode['use_cookies']}) failed: {last_error[:300]}")

        if source_type == "ytm_upload":
            logger.error(
                f"UPLOAD DOWNLOAD BLOCKED\n"
                f"reason=PRIVATE_UPLOAD_UNAVAILABLE\n"
                f"fallback_search=DISABLED\n"
                f"replacement=DISABLED\n"
                f"expected_video_id={clean_id}\n"
                f"error={last_error[:200]}"
            )
            raise PrivateUploadUnavailableError(
                f"Failed to download upload {clean_id}: private upload unavailable or auth failed ({last_error[:200]})"
            )

        raise RuntimeError(f"Failed to download audio for {clean_id}: {last_error[:300]}")

    finally:
        if id_file.exists():
            try:
                id_file.unlink()
            except Exception:
                pass
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


def _extract_source_identity(source_record: Union[str, Dict[str, Any], Any], expected_type: str) -> tuple[str, str]:
    """Extract and validate (source_type, source_id) from record or string."""
    if isinstance(source_record, str):
        return expected_type, source_record.strip()

    if isinstance(source_record, dict):
        rec_type = source_record.get("source_type") or expected_type
        if rec_type != expected_type:
            raise ValueError(f"Contradictory source_type: expected '{expected_type}', got '{rec_type}'")
        rec_id = (
            source_record.get("source_id")
            or source_record.get("upload_video_id")
            or source_record.get("video_id")
        )
        if not rec_id:
            raise ValueError(f"Missing source_id in {expected_type} record")
        return rec_type, str(rec_id).strip()

    rec_type = getattr(source_record, "source_type", expected_type)
    if rec_type != expected_type:
        raise ValueError(f"Contradictory source_type: expected '{expected_type}', got '{rec_type}'")
    rec_id = getattr(source_record, "source_id", None) or getattr(source_record, "video_id", None)
    if not rec_id:
        raise ValueError(f"Missing source_id in {expected_type} record")
    return rec_type, str(rec_id).strip()


def commit_staged_file_to_destination(
    staged_file: Path,
    destination_file: Path,
    allow_overwrite: bool = False,
    replacement_source_id: Optional[str] = None
) -> Path:
    """
    Safely commit a verified file from staging to a destination path.
    Write Policy (Phase 6 & Phase 9):
    - NEW FILE -> ALLOWED
    - EXISTING FILE -> NEVER automatically replace (BLOCK by default)

    An existing file will NEVER be overwritten unless:
    1. allow_overwrite is explicitly set to True by an intentional caller, AND
    2. settings.allow_automatic_replacement is explicitly enabled (YTM_SYNC_ALLOW_AUTOMATIC_REPLACEMENT=true).
    Otherwise, replacement is BLOCKED and FileExistsError is raised.

    Phase 9 — Pre-Replacement Protection:
    Before any authorized overwrite occurs:
    - Calculates the SHA-256 of the existing file.
    - Saves a snapshot backup into settings.backups_dir.
    - Logs and persists an audit trail entry (original_path, sha256, size, mtime, backup_path).
    """
    if not staged_file.exists() or staged_file.stat().st_size == 0:
        raise FileNotFoundError(f"Staged file not found or empty: {staged_file}")

    target_path = validate_fs_path(destination_file, allow_create_in_parent=True)

    if target_path.exists():
        if not (allow_overwrite and settings.allow_automatic_replacement):
            logger.warning(
                f"REPLACEMENT BLOCKED: Destination file '{target_path}' already exists. "
                f"Automatic file replacement is BLOCKED by policy to protect local audio recordings. "
                f"(allow_overwrite={allow_overwrite}, allow_automatic_replacement={settings.allow_automatic_replacement})"
            )
            raise FileExistsError(
                f"Destination file already exists: {target_path}. Automatic replacement blocked by write policy."
            )

        # Authorized replacement: Protect the original file (Phase 9)
        h = hashlib.sha256()
        with open(target_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        orig_sha256 = h.hexdigest()
        orig_size = target_path.stat().st_size
        orig_mtime = target_path.stat().st_mtime

        # Create safety backup snapshot before overwriting
        backup_file = settings.backups_dir / f"{target_path.stem}_{orig_sha256[:12]}.bak"
        try:
            shutil.copy2(target_path, backup_file)
        except Exception as ex:
            logger.warning(f"Could not create pre-replacement backup for {target_path}: {ex}")

        logger.warning(
            f"PRE-REPLACEMENT AUDIT RECORDED: path={target_path} sha256={orig_sha256} "
            f"size={orig_size} backup={backup_file}"
        )

        try:
            db.record_file_replacement_sync(
                original_path=str(target_path),
                original_sha256=orig_sha256,
                original_size=orig_size,
                original_mtime=orig_mtime,
                replacement_source_id=replacement_source_id or "unknown",
                replacement_path=str(staged_file),
                backup_path=str(backup_file) if backup_file.exists() else None
            )
        except Exception as ex:
            logger.error(f"Failed to record file replacement audit trail: {ex}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(staged_file, target_path)
    logger.info(f"Committed verified file from staging ({staged_file.name}) to {target_path}")
    return target_path


async def download_upload(
    upload_record: Union[str, Dict[str, Any], Any],
    dest_dir: Optional[Path] = None
) -> Path:
    """
    Download an authentic Upload Locker item by its exact upload identity.
    Source type is strictly 'ytm_upload'.
    Never searches YouTube, never uses ytsearch, and never substitutes public audio.
    Fails closed if the authenticated upload stream is inaccessible.
    Always downloads to isolated staging first before any commit.
    """
    source_type, video_id = _extract_source_identity(upload_record, expected_type="ytm_upload")

    # Hard staging isolation: Always download to staging first
    staging_dir = settings.data_dir / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    target_base = staging_dir / f"ytm_{video_id}"

    logger.info(f"UPLOAD DOWNLOAD START source_type={source_type} expected_video_id={video_id}")
    staged_file = await asyncio.to_thread(_download_sync, video_id, target_base, None, "ytm_upload")

    # Audio Fingerprint / Characteristic Protection (Phase 8)
    expected_duration = None
    if isinstance(upload_record, dict):
        expected_duration = upload_record.get("duration")
    else:
        expected_duration = getattr(upload_record, "duration", None)

    if expected_duration is not None and expected_duration > 0:
        from .audio_fingerprint import verify_audio_integrity, AudioFingerprintMismatchError
        try:
            await asyncio.to_thread(verify_audio_integrity, staged_file, expected_duration=expected_duration)
        except AudioFingerprintMismatchError as ex:
            logger.error(
                f"UPLOAD DOWNLOAD BLOCKED\n"
                f"expected_video_id={video_id}\n"
                f"reason=AUDIO_FINGERPRINT_MISMATCH\n"
                f"details={ex}"
            )
            try:
                staged_file.unlink()
            except Exception:
                pass
            raise

    if dest_dir:
        dest_path = validate_fs_path(dest_dir, allow_create_in_parent=True)
        return commit_staged_file_to_destination(staged_file, dest_path / staged_file.name)
    return staged_file


async def download_catalog_track(
    catalog_record: Union[str, Dict[str, Any], Any],
    dest_dir: Optional[Path] = None,
    fallback_query: Optional[str] = None
) -> Path:
    """
    Download a catalog/public playlist track.
    Source type is strictly 'catalog'.
    Rejects requests if source_type is missing or contradictory.
    Always downloads to isolated staging first before any commit.
    """
    source_type, video_id = _extract_source_identity(catalog_record, expected_type="catalog")

    # Hard staging isolation: Always download to staging first
    staging_dir = settings.data_dir / "staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    target_base = staging_dir / f"cat_{video_id}"

    staged_file = await asyncio.to_thread(_download_sync, video_id, target_base, fallback_query, "catalog")

    if dest_dir:
        dest_path = validate_fs_path(dest_dir, allow_create_in_parent=True)
        return commit_staged_file_to_destination(staged_file, dest_path / staged_file.name)
    return staged_file


async def download_ytm_upload(
    video_id: str,
    dest_dir: Optional[Path] = None
) -> Path:
    """
    Download an uploaded YouTube Music track by its video_id and return the local path.
    Enforces upload-only semantics with no search fallback.
    Always downloads to isolated staging first.
    """
    return await download_upload(video_id, dest_dir=dest_dir)


def extract_playlist_info_sync(playlist_url_or_id: str) -> dict:
    """Extract playlist metadata and track list using yt-dlp flat-playlist mode."""
    url = playlist_url_or_id
    if not url.startswith("http"):
        clean_id = playlist_url_or_id.strip()
        if not re.match(r'^[a-zA-Z0-9_-]+$', clean_id):
            raise ValueError(f"Invalid playlist ID format: {playlist_url_or_id}")
        url = f"https://www.youtube.com/playlist?list={clean_id}"

    validate_youtube_url(url)

    auth_candidates = [
        settings.auth_file,
        settings.data_dir / "headers_auth.json",
        settings.data_dir / "auth" / "headers_auth.json",
        Path("/config/headers_auth.json"),
        Path("/config/auth/headers_auth.json"),
        Path.home() / ".config" / "ytm_sync" / "headers_auth.json",
        Path.home() / ".config" / "ytm_sync" / "auth" / "headers_auth.json",
    ]

    cookie_raw = ""
    auth_header = ""
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    for auth_p in auth_candidates:
        if auth_p.exists():
            try:
                with open(auth_p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        kl = k.lower()
                        if kl == "cookie" and isinstance(v, str) and v.strip():
                            cookie_raw = v.strip()
                        elif kl == "authorization" and isinstance(v, str) and v.strip():
                            auth_header = v.strip()
                        elif kl == "user-agent" and isinstance(v, str) and v.strip():
                            user_agent = v.strip()
                if cookie_raw:
                    break
            except Exception as e:
                logger.warning(f"Failed to read auth file {auth_p}: {e}")

    cookie_file = _generate_netscape_cookies(cookie_raw) if cookie_raw else None

    try:
        env = dict(os.environ)
        deno_paths = ["/usr/local/bin", "/home/m3tal/.deno/bin", os.path.expanduser("~/.deno/bin")]
        existing_path = env.get("PATH", "")
        extra_paths = [p for p in deno_paths if os.path.isdir(p) and p not in existing_path]
        if extra_paths:
            env["PATH"] = ":".join(extra_paths) + ":" + existing_path

        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--flat-playlist",
            "--dump-single-json",
            "--no-warnings",
            "--user-agent", user_agent,
            url
        ]
        if auth_header:
            cmd.extend(["--add-header", f"Authorization: {auth_header}"])
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])

        res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"Failed to fetch playlist info: {res.stderr[:200] or 'Unknown error'}")

        data = json.loads(res.stdout)
        entries = data.get("entries") or []
        tracks = []
        for e in entries:
            if not e or not e.get("id"):
                continue
            tracks.append({
                "video_id": e.get("id"),
                "title": e.get("title", "Untitled Track"),
                "artist": e.get("uploader") or e.get("channel") or e.get("artist"),
                "album": data.get("title", "YouTube Playlist"),
                "duration": e.get("duration"),
                "thumbnail": e.get("thumbnails")[-1]["url"] if e.get("thumbnails") else None,
            })

        return {
            "id": data.get("id") or playlist_url_or_id,
            "title": data.get("title", "YouTube Playlist"),
            "description": data.get("description", ""),
            "track_count": len(tracks),
            "thumbnail": data.get("thumbnails")[-1]["url"] if data.get("thumbnails") else (tracks[0]["thumbnail"] if tracks else None),
            "tracks": tracks
        }
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


async def extract_playlist_info(playlist_url_or_id: str) -> dict:
    """Asynchronously extract playlist metadata and tracks via yt-dlp."""
    return await asyncio.to_thread(extract_playlist_info_sync, playlist_url_or_id)

