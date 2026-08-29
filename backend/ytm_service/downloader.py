import os
import sys
import json
import logging
import tempfile
import asyncio
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional
from .config import settings

logger = logging.getLogger("ytm_sync.downloader")


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


def _download_sync(video_id: str, output_path: Path) -> Path:
    """Download audio stream. Tries ytmusicapi direct stream, standard youtube.com URL, and music.youtube.com URL."""
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. First attempt direct download via authenticated ytmusicapi session
    try:
        direct_file = _download_via_ytmusicapi(video_id, output_path)
        if direct_file and direct_file.exists() and direct_file.stat().st_size > 0:
            return direct_file
    except Exception as e:
        logger.debug(f"Direct ytmusicapi stream failed: {e}")

    logger.info(f"Direct ytmusicapi stream not available; falling back to yt-dlp for upload {video_id}...")

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

        # Try standard youtube.com first (doesn't trigger web_music GVS PO token requirement)
        # and fallback to music.youtube.com
        urls_to_try = [
            f"https://www.youtube.com/watch?v={video_id}",
            f"https://music.youtube.com/watch?v={video_id}"
        ]

        last_error = "Unknown error"
        for target_url in urls_to_try:
            cmd = [
                sys.executable, "-m", "yt_dlp",
                "--remote-components", "ejs:github",
                "--extractor-args", "youtube:formats=missing_pot",
                "-x", "--audio-format", "mp3",
                "--no-playlist",
                "--user-agent", user_agent,
                "-o", str(output_path.with_suffix(".%(ext)s")),
                target_url
            ]
            if auth_header:
                cmd.extend(["--add-header", f"Authorization: {auth_header}"])
            if cookie_file:
                cmd.extend(["--cookies", cookie_file])

            logger.info(f"Downloading YTM upload {video_id} via {target_url}...")
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

            if res.returncode == 0:
                final_file = output_path.with_suffix(".mp3")
                if not final_file.exists() or final_file.stat().st_size == 0:
                    candidates = list(output_dir.glob(f"{output_path.stem}.*"))
                    if candidates:
                        final_file = candidates[0]
                if final_file.exists() and final_file.stat().st_size > 0:
                    logger.info(f"Successfully downloaded {video_id} to {final_file} ({final_file.stat().st_size} bytes)")
                    return final_file

            last_error = res.stderr.strip() or res.stdout.strip() or "Unknown error"
            logger.warning(f"Download attempt for {target_url} failed: {last_error[:300]}")

        raise RuntimeError(f"Failed to download audio: {last_error[:300]}")

    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except Exception:
                pass


async def download_ytm_upload(video_id: str, dest_dir: Optional[Path] = None) -> Path:
    """Download an uploaded YouTube Music track by its video_id and return the local path."""
    target_dir = dest_dir or (settings.data_dir / "staging")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_base = target_dir / f"ytm_{video_id}"

    return await asyncio.to_thread(_download_sync, video_id, target_base)
