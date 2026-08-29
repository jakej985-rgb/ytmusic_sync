import os
import sys
import json
import logging
import tempfile
import asyncio
import subprocess
from pathlib import Path
from typing import Optional
from .config import settings

logger = logging.getLogger("ytm_sync.downloader")

def _generate_netscape_cookies(cookie_raw: str) -> str:
    """Generate a Netscape-format cookie file from raw Cookie header string."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="ytm_cookies_")
    with os.fdopen(fd, "w", encoding="utf-8") as tf:
        tf.write("# Netscape HTTP Cookie File\n")
        tf.write("# Generated for YouTube Music private upload download\n")
        for item in cookie_raw.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                for domain in (".youtube.com", ".music.youtube.com", ".googlevideo.com"):
                    tf.write(f"{domain}\tTRUE\t/\tTRUE\t2147483647\t{k.strip()}\t{v.strip()}\n")
    return path


def _download_sync(video_id: str, output_path: Path) -> Path:
    """Synchronously execute yt-dlp to download and convert the audio stream to MP3."""
    cookie_raw = ""
    if settings.auth_file.exists():
        try:
            with open(settings.auth_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                cookie_raw = data.get("cookie", "")
        except Exception as e:
            logger.warning(f"Failed to read cookie from auth file: {e}")

    cookie_file = None
    if cookie_raw:
        cookie_file = _generate_netscape_cookies(cookie_raw)

    try:
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        env = dict(os.environ)
        # Ensure deno is in PATH for JS challenge solving
        deno_paths = ["/usr/local/bin", "/home/m3tal/.deno/bin", os.path.expanduser("~/.deno/bin")]
        existing_path = env.get("PATH", "")
        extra_paths = [p for p in deno_paths if os.path.isdir(p) and p not in existing_path]
        if extra_paths:
            env["PATH"] = ":".join(extra_paths) + ":" + existing_path

        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--remote-components", "ejs:github",
            "-x", "--audio-format", "mp3",
            "--no-playlist",
            "-o", str(output_path.with_suffix(".%(ext)s")),
            f"https://music.youtube.com/watch?v={video_id}"
        ]
        if cookie_file:
            cmd.extend(["--cookies", cookie_file])

        logger.info(f"Downloading YTM upload {video_id} using yt-dlp...")
        res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

        if res.returncode != 0:
            err_msg = res.stderr.strip() or res.stdout.strip() or "Unknown error"
            logger.error(f"yt-dlp failed for video {video_id}: {err_msg[:400]}")
            raise RuntimeError(f"Failed to download audio: {err_msg[:300]}")

        # The extracted file will have .mp3 extension
        final_file = output_path.with_suffix(".mp3")
        if not final_file.exists() or final_file.stat().st_size == 0:
            # Check if downloaded under another extension
            candidates = list(output_dir.glob(f"{output_path.stem}.*"))
            if candidates:
                final_file = candidates[0]
            else:
                raise FileNotFoundError(f"Downloaded audio file not found at {final_file}")

        logger.info(f"Successfully downloaded {video_id} to {final_file} ({final_file.stat().st_size} bytes)")
        return final_file

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
