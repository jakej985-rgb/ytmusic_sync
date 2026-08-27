import os
import stat
import json
import logging
import asyncio
from pathlib import Path
from typing import Optional, Any
from ytmusicapi import YTMusic, setup
from ytmusicapi.exceptions import YTMusicUserError, YTMusicError

from .config import settings
from .database import db
from .normalizer import parse_duration

logger = logging.getLogger("ytm_sync.ytm_client")

import re

def preprocess_headers(raw: str) -> str:
    raw = raw.strip()
    # 1. Handle Copy as cURL (bash / POSIX / Windows)
    if raw.startswith("curl "):
        header_lines = []
        matches = re.findall(r'(?:-H|--header)\s+[\'"]([^\'"]+)[\'"]', raw, re.IGNORECASE)
        for m in matches:
            header_lines.append(m)
        if header_lines:
            return "\n".join(header_lines)

    # 2. Handle JSON array of cookies/headers
    if raw.startswith("[") or raw.startswith("{"):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                cookie_pairs = []
                for item in parsed:
                    if isinstance(item, dict) and "name" in item and "value" in item:
                        cookie_pairs.append(f"{item['name']}={item['value']}")
                if cookie_pairs:
                    return f"cookie: {'; '.join(cookie_pairs)}\nx-goog-authuser: 0\nuser-agent: Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0\naccept: */*\n"
        except Exception:
            pass

    return raw

class YTMClient:
    def __init__(self):
        self._ytm: Optional[YTMusic] = None

    def is_auth_configured(self) -> bool:
        return settings.auth_file.exists() and settings.auth_file.stat().st_size > 10

    def _get_client(self) -> YTMusic:
        if not self.is_auth_configured():
            raise YTMusicUserError("YouTube Music authentication has not been configured yet.")
        if self._ytm is None:
            self._ytm = YTMusic(str(settings.auth_file))
        return self._ytm

    def reset_client(self):
        self._ytm = None

    async def setup_auth(self, raw_headers: str) -> dict:
        """Parse raw browser headers and write securely to auth_file with 0600 permissions."""
        def _setup_sync():
            settings.auth_file.parent.mkdir(parents=True, exist_ok=True)
            cleaned_headers = preprocess_headers(raw_headers)
            # Use ytmusicapi's setup parser
            res = setup(filepath=str(settings.auth_file), headers_raw=cleaned_headers)
            # Secure file permissions (rw-------)
            os.chmod(settings.auth_file, stat.S_IRUSR | stat.S_IWUSR)
            return res

        await asyncio.to_thread(_setup_sync)
        self.reset_client()
        return await self.test_connection()

    async def test_connection(self) -> dict:
        """Validate credentials by running a test request."""
        if not self.is_auth_configured():
            return {
                "connected": False,
                "message": "Authentication headers file not found. Please connect your account.",
                "user_name": None
            }

        def _test_sync():
            yt = self._get_client()
            # Fetch 1 upload song to verify upload browse capabilities
            res = yt.get_library_upload_songs(limit=1)
            return res

        try:
            await asyncio.to_thread(_test_sync)
            return {
                "connected": True,
                "message": "Connected to YouTube Music successfully.",
                "user_name": "Connected Account"
            }
        except Exception as e:
            logger.error(f"YTM connection test failed: {e}")
            self.reset_client()
            return {
                "connected": False,
                "message": f"Connection failed: {str(e)}",
                "user_name": None
            }

    async def fetch_and_cache_uploads(self) -> list[dict]:
        """Fetch all user uploads from YouTube Music and cache them in the SQLite DB."""
        if not self.is_auth_configured():
            raise YTMusicUserError("Not authenticated.")

        def _fetch_sync():
            yt = self._get_client()
            # limit=None fetches all uploads via continuations
            return yt.get_library_upload_songs(limit=None)

        raw_uploads = await asyncio.to_thread(_fetch_sync)
        cached = []
        for item in raw_uploads:
            # Parse item structure
            entity_id = item.get("entityId")
            if not entity_id:
                continue

            title = item.get("title", "")
            artists_list = item.get("artists")
            artist_name = None
            if artists_list and isinstance(artists_list, list) and len(artists_list) > 0:
                artist_name = artists_list[0].get("name") if isinstance(artists_list[0], dict) else str(artists_list[0])

            album_dict = item.get("album")
            album_name = None
            if isinstance(album_dict, dict):
                album_name = album_dict.get("name")
            elif isinstance(album_dict, str):
                album_name = album_dict

            duration_raw = item.get("duration") or item.get("duration_seconds")
            duration_sec = parse_duration(duration_raw)

            thumb = None
            thumbs = item.get("thumbnails")
            if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
                thumb = thumbs[-1].get("url")

            upload_record = {
                "entity_id": entity_id,
                "video_id": item.get("videoId"),
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration_sec,
                "like_status": item.get("likeStatus"),
                "thumbnail": thumb,
            }
            await db.upsert_ytm_upload(upload_record)
            cached.append(upload_record)

        return cached

    async def upload_file(self, filepath: str) -> dict:
        """Upload single music file to YouTube Music."""
        if not self.is_auth_configured():
            raise YTMusicUserError("Not authenticated.")

        p = Path(filepath)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {filepath}")

        def _upload_sync():
            yt = self._get_client()
            res = yt.upload_song(str(p.resolve()))
            return str(res)

        result_str = await asyncio.to_thread(_upload_sync)
        # Check success indicators from ytmusicapi
        is_success = "STATUS_SUCCEEDED" in result_str or "200" in result_str or "SUCCEEDED" in result_str
        return {
            "success": is_success,
            "response": result_str
        }

    async def get_playlists(self) -> list[dict]:
        """Fetch user's YouTube Music library playlists."""
        if not self.is_auth_configured():
            raise YTMusicUserError("Not authenticated.")

        def _fetch_playlists_sync():
            yt = self._get_client()
            playlists = yt.get_library_playlists(limit=None)
            result = []
            # Add Liked Music auto-playlist at top
            result.append({
                "id": "LM",
                "title": "Liked Music",
                "description": "Your auto-generated liked songs playlist",
                "track_count": None,
                "thumbnail": None
            })
            for p in playlists:
                p_id = p.get("playlistId")
                if not p_id:
                    continue
                thumb = None
                thumbs = p.get("thumbnails")
                if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
                    thumb = thumbs[-1].get("url")

                count_val = p.get("count")
                track_cnt = None
                if count_val is not None:
                    try:
                        track_cnt = int(re.sub(r"[^\d]", "", str(count_val)))
                    except Exception:
                        track_cnt = None

                result.append({
                    "id": p_id,
                    "title": p.get("title", "Untitled Playlist"),
                    "description": p.get("description", ""),
                    "track_count": track_cnt,
                    "thumbnail": thumb
                })
            return result

        return await asyncio.to_thread(_fetch_playlists_sync)

    async def get_playlist_details(self, playlist_id: str) -> dict:
        """Fetch playlist tracks and match against local library and uploads."""
        if not self.is_auth_configured():
            raise YTMusicUserError("Not authenticated.")

        def _fetch_details_sync():
            yt = self._get_client()
            if playlist_id == "LM":
                return yt.get_liked_songs(limit=None)
            return yt.get_playlist(playlist_id, limit=None)

        raw = await asyncio.to_thread(_fetch_details_sync)
        tracks_raw = raw.get("tracks", [])

        # Load local songs and uploads for matching
        local_files = await db.get_all_local_songs()
        uploads = await db.get_all_ytm_uploads()

        # Build normalized lookups for fast comparison
        from .normalizer import normalize_text

        local_map = {}
        for f in local_files:
            key = f"{normalize_text(f.get('artist'))}|{normalize_text(f.get('title'))}"
            local_map[key] = f.get("path")
            title_key = normalize_text(f.get("title"))
            if title_key and title_key not in local_map:
                local_map[title_key] = f.get("path")

        uploads_set = set()
        for u in uploads:
            u_key = f"{normalize_text(u.artist)}|{normalize_text(u.title)}"
            uploads_set.add(u_key)
            u_title = normalize_text(u.title)
            if u_title:
                uploads_set.add(u_title)

        matched_tracks = []
        for t in tracks_raw:
            title = t.get("title", "")
            artists = t.get("artists", [])
            artist_name = None
            if artists and isinstance(artists, list) and len(artists) > 0:
                artist_name = artists[0].get("name") if isinstance(artists[0], dict) else str(artists[0])

            album = t.get("album")
            album_name = album.get("name") if isinstance(album, dict) else (album if isinstance(album, str) else None)

            thumb = None
            thumbs = t.get("thumbnails")
            if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
                thumb = thumbs[-1].get("url")

            duration = t.get("duration") or t.get("duration_seconds")

            # Check match against local library & uploads
            key = f"{normalize_text(artist_name)}|{normalize_text(title)}"
            title_key = normalize_text(title)

            local_path = local_map.get(key) or local_map.get(title_key)
            in_local = local_path is not None
            in_uploads = (key in uploads_set) or (title_key in uploads_set)

            matched_tracks.append({
                "video_id": t.get("videoId"),
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration,
                "thumbnail": thumb,
                "in_local": in_local,
                "in_uploads": in_uploads,
                "local_path": local_path
            })

        thumb = None
        thumbs = raw.get("thumbnails")
        if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
            thumb = thumbs[-1].get("url")

        return {
            "id": playlist_id,
            "title": raw.get("title", "Liked Music" if playlist_id == "LM" else "Playlist"),
            "description": raw.get("description", ""),
            "track_count": len(matched_tracks),
            "thumbnail": thumb,
            "tracks": matched_tracks
        }

ytm_client = YTMClient()
