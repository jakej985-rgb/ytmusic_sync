import os
import stat
import json
import logging
import asyncio
import time
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

class YTMClientManager:
    """
    User-aware YouTube Music client manager (Phase K).
    Maintains isolated YTMusic instances per authenticated user_id to prevent
    cross-account client reuse or credential contamination.
    """
    def __init__(self):
        self._clients: dict[str, YTMusic] = {}
        self._recently_deleted: dict[str, dict[str, float]] = {}

    def get_user_auth_file(self, user_id: str) -> Path:
        from .security import get_user_subpath, validate_user_id
        clean_id = validate_user_id(user_id)
        return get_user_subpath(clean_id, "auth") / "headers_auth.json"

    def is_user_auth_configured(self, user_id: str) -> bool:
        try:
            auth_file = self.get_user_auth_file(user_id)
            if auth_file.exists() and auth_file.stat().st_size > 10:
                return True
        except Exception:
            pass
        # Fallback to global config if available
        return settings.auth_file.exists() and settings.auth_file.stat().st_size > 10

    def get_client_for_user(self, user_id: str) -> YTMusic:
        from .security import validate_user_id
        clean_id = validate_user_id(user_id)
        if clean_id in self._clients:
            return self._clients[clean_id]

        user_auth = self.get_user_auth_file(clean_id)
        if user_auth.exists() and user_auth.stat().st_size > 10:
            client = YTMusic(str(user_auth))
            self._clients[clean_id] = client
            return client

        if settings.auth_file.exists() and settings.auth_file.stat().st_size > 10:
            client = YTMusic(str(settings.auth_file))
            self._clients[clean_id] = client
            return client

        raise YTMusicUserError(f"YouTube Music authentication has not been configured for user '{user_id}'.")

    def reset_client_for_user(self, user_id: str):
        from .security import validate_user_id
        try:
            clean_id = validate_user_id(user_id)
            self._clients.pop(clean_id, None)
            self._recently_deleted.pop(clean_id, None)
        except Exception:
            pass

    def disconnect_user(self, user_id: str) -> dict:
        self.reset_client_for_user(user_id)
        try:
            auth_file = self.get_user_auth_file(user_id)
            if auth_file.exists():
                auth_file.unlink()
        except Exception as e:
            logger.warning(f"Error removing user auth file: {e}")
        return {
            "connected": False,
            "message": "Disconnected from YouTube Music successfully.",
            "user_name": None
        }

    async def setup_user_auth(self, user_id: str, raw_headers: str) -> dict:
        auth_file = self.get_user_auth_file(user_id)
        def _setup_sync():
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(auth_file.parent, stat.S_IRWXU)
            except OSError:
                pass
            cleaned_headers = preprocess_headers(raw_headers)
            res = setup(filepath=str(auth_file), headers_raw=cleaned_headers)
            try:
                os.chmod(auth_file, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            return res

        await asyncio.to_thread(_setup_sync)
        self.reset_client_for_user(user_id)
        client = self.get_client_for_user(user_id)
        try:
            acc_info = await asyncio.to_thread(client.get_account_info)
            user_name = acc_info.get("accountName") or acc_info.get("channelHandle")
        except Exception:
            user_name = "Connected Account"
        return {
            "connected": True,
            "message": "Connected to YouTube Music successfully.",
            "user_name": user_name
        }

    def mark_deleted(self, user_id: str, entity_id: str):
        if user_id not in self._recently_deleted:
            self._recently_deleted[user_id] = {}
        self._recently_deleted[user_id][entity_id] = time.time()

    def is_recently_deleted(self, user_id: str, entity_id: str) -> bool:
        if user_id not in self._recently_deleted:
            return False
        user_map = self._recently_deleted[user_id]
        if entity_id not in user_map:
            return False
        if time.time() - user_map[entity_id] > 1800:
            del user_map[entity_id]
            return False
        return True


ytm_client_manager = YTMClientManager()


class YTMClient:
    def __init__(self):
        self._ytm: Optional[YTMusic] = None
        self._recently_deleted: dict[str, float] = {}
        self.manager = ytm_client_manager

    def mark_deleted(self, entity_id: str, user_id: Optional[str] = None):
        """Mark an entity_id as deleted so stale YTM continuation caches cannot re-insert it."""
        self._recently_deleted[entity_id] = time.time()
        if user_id:
            self.manager.mark_deleted(user_id, entity_id)

    def is_recently_deleted(self, entity_id: str, user_id: Optional[str] = None) -> bool:
        """Check if an entity was deleted within the last 30 minutes."""
        if user_id and self.manager.is_recently_deleted(user_id, entity_id):
            return True
        if entity_id not in self._recently_deleted:
            return False
        if time.time() - self._recently_deleted[entity_id] > 1800:
            del self._recently_deleted[entity_id]
            return False
        return True

    def is_auth_configured(self, user_id: Optional[str] = None) -> bool:
        if user_id:
            return self.manager.is_user_auth_configured(user_id)
        return settings.auth_file.exists() and settings.auth_file.stat().st_size > 10

    def _get_client(self, user_id: Optional[str] = None) -> YTMusic:
        if user_id:
            return self.manager.get_client_for_user(user_id)
        if not self.is_auth_configured():
            raise YTMusicUserError("YouTube Music authentication has not been configured yet.")
        if self._ytm is None:
            self._ytm = YTMusic(str(settings.auth_file))
        return self._ytm

    def reset_client(self, user_id: Optional[str] = None):
        if user_id:
            self.manager.reset_client_for_user(user_id)
        self._ytm = None

    def disconnect_auth(self, user_id: Optional[str] = None) -> dict:
        """Safely disconnect YouTube Music account, removing stored headers and resetting the client."""
        res = {
            "connected": False,
            "message": "Disconnected from YouTube Music successfully.",
            "user_name": None
        }
        if user_id:
            res = self.manager.disconnect_user(user_id)
        self.reset_client()
        if settings.auth_file.exists():
            try:
                settings.auth_file.unlink()
            except OSError as e:
                logger.warning(f"Error removing auth file: {e}")
        return res

    def disconnect_user(self, user_id: str) -> dict:
        """Disconnect and evict cached YouTube Music client for a specific user."""
        return self.manager.disconnect_user(user_id)

    async def setup_auth(self, raw_headers: str, user_id: Optional[str] = None) -> dict:
        """Parse raw browser headers and write securely to auth_file with 0600 permissions."""
        if user_id:
            return await self.manager.setup_user_auth(user_id, raw_headers)
        def _setup_sync():
            settings.auth_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(settings.auth_file.parent, stat.S_IRWXU)
            except OSError:
                pass
            cleaned_headers = preprocess_headers(raw_headers)
            # Use ytmusicapi's setup parser
            res = setup(filepath=str(settings.auth_file), headers_raw=cleaned_headers)
            # Secure file permissions (rw-------)
            os.chmod(settings.auth_file, stat.S_IRUSR | stat.S_IWUSR)
            return res

        await asyncio.to_thread(_setup_sync)
        self.reset_client()
        return await self.test_connection()

    async def test_connection(self, user_id: Optional[str] = None) -> dict:
        """Validate credentials by running a test request."""
        if not self.is_auth_configured(user_id=user_id):
            return {
                "connected": False,
                "message": "Authentication headers file not found. Please connect your account.",
                "user_name": None
            }

        def _test_sync():
            yt = self._get_client(user_id=user_id)
            user_name = None
            try:
                acc_info = yt.get_account_info()
                user_name = acc_info.get("accountName") or acc_info.get("channelHandle")
            except Exception as e:
                logger.debug(f"Could not retrieve user account name: {e}")

            # Fetch 1 upload song to verify upload browse capabilities
            yt.get_library_upload_songs(limit=1)
            return user_name

        try:
            detected_user = await asyncio.to_thread(_test_sync)
            return {
                "connected": True,
                "message": "Connected to YouTube Music successfully.",
                "user_name": detected_user
            }
        except Exception as e:
            logger.error(f"YTM connection test failed: {e}", exc_info=True)
            self.reset_client(user_id=user_id)
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str:
                msg = "Connection timed out while contacting YouTube Music."
            elif any(x in err_str for x in ["connection refused", "unreachable", "name or service not known", "nodename nor servname"]):
                msg = "YouTube Music servers are unreachable. Please check your network connection."
            elif any(x in err_str for x in ["401", "403", "unauthorized", "forbidden"]):
                msg = "Credentials were rejected by YouTube Music. Please relink your account."
            else:
                msg = "Account verification failed. Please check your credentials."
            return {
                "connected": False,
                "message": msg,
                "user_name": None
            }

    async def fetch_and_cache_uploads(self, user_id: Optional[str] = None) -> list[dict]:
        """Fetch all user uploads from YouTube Music and cache them in the SQLite DB."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _fetch_sync():
            yt = self._get_client(user_id=user_id)
            # limit=None fetches all uploads via continuations
            return yt.get_library_upload_songs(limit=None)

        raw_uploads = await asyncio.to_thread(_fetch_sync)
        cached = []
        active_entity_ids: set[str] = set()
        for item in raw_uploads:
            # Parse item structure
            entity_id = item.get("entityId")
            if not entity_id or self.is_recently_deleted(entity_id, user_id=user_id):
                continue

            active_entity_ids.add(entity_id)

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

            # Parse duration from string e.g. "3:45"
            duration_str = item.get("duration")
            duration_sec = None
            if duration_str and isinstance(duration_str, str):
                parts = duration_str.split(":")
                try:
                    if len(parts) == 2:
                        duration_sec = int(parts[0]) * 60 + int(parts[1])
                    elif len(parts) == 3:
                        duration_sec = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                except ValueError:
                    pass

            thumbs = item.get("thumbnails")
            thumb = None
            if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
                thumb = thumbs[-1].get("url")

            vid = item.get("videoId")
            upload_record = {
                "entity_id": entity_id,
                "video_id": vid,
                "upload_video_id": vid,
                "upload_url": f"https://www.youtube.com/watch?v={vid}" if vid else None,
                "source_type": "ytm_upload",
                "title": title,
                "artist": artist_name,
                "album": album_name,
                "duration": duration_sec,
                "like_status": item.get("likeStatus"),
                "thumbnail": thumb,
            }
            if user_id:
                upload_record["user_id"] = user_id
            await db.upsert_ytm_upload(upload_record)
            cached.append(upload_record)

        # Prune records from SQLite DB that were deleted on YTM or recently replaced
        await db.prune_deleted_ytm_uploads(active_entity_ids, set(self._recently_deleted.keys()))

        return cached

    async def upload_file(self, filepath: str, user_id: Optional[str] = None) -> dict:
        """Upload single music file to YouTube Music."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        p = Path(filepath)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {filepath}")

        def _upload_sync():
            yt = self._get_client(user_id=user_id)
            res = yt.upload_song(str(p.resolve()))
            return str(res)

        result_str = await asyncio.to_thread(_upload_sync)
        # Check success indicators from ytmusicapi
        # 409 means the file already exists in user's cloud locker
        already_exists = "409" in result_str or "already exists" in result_str.lower()
        is_success = "STATUS_SUCCEEDED" in result_str or "200" in result_str or "SUCCEEDED" in result_str or already_exists
        return {
            "success": is_success,
            "already_exists": already_exists,
            "response": result_str
        }

    async def delete_upload(self, entity_id: str, user_id: Optional[str] = None) -> dict:
        """Delete an uploaded song from YouTube Music using its entity_id."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        self.mark_deleted(entity_id, user_id=user_id)

        def _delete_sync():
            yt = self._get_client(user_id=user_id)
            return yt.delete_upload_entity(entity_id)

        res = await asyncio.to_thread(_delete_sync)
        logger.info(f"Deleted upload entity {entity_id}: {res}")
        return {"success": True, "response": str(res)}

    async def get_playlists(self, user_id: Optional[str] = None) -> list[dict]:
        """Fetch user's YouTube Music library playlists."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _fetch_playlists_sync():
            yt = self._get_client(user_id=user_id)
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

    async def get_playlist_details(self, playlist_id: str, user_id: Optional[str] = None) -> dict:
        """Fetch playlist tracks and match against local library and uploads."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _fetch_details_sync():
            yt = self._get_client(user_id=user_id)
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
        from .playlist_downloader import clean_youtube_title

        local_map = {}
        for f in local_files:
            key = f"{normalize_text(f.get('artist'))}|{normalize_text(f.get('title'))}"
            local_map[key] = f.get("path")
            title_key = normalize_text(f.get("title"))
            if title_key and title_key not in local_map:
                local_map[title_key] = f.get("path")

        uploads_set = set()
        uploads_video_ids = set()
        uploads_by_title: dict[str, list[str]] = {}
        for u in uploads:
            # Require both artist and title to match, with collaborator tolerance
            u_art = normalize_text(u.artist)
            u_tit = normalize_text(u.title)
            if u_tit:
                uploads_set.add(f"{u_art}|{u_tit}")
                uploads_by_title.setdefault(u_tit, []).append(u_art)
            if u.video_id and not (u.entity_id and u.entity_id.startswith("up_")):
                uploads_video_ids.add(u.video_id)

        def is_music_track(t: dict, raw_title: str) -> bool:
            """Determine if track is an official audio release vs a video version."""
            vtype = t.get("videoType") or ""
            if vtype == "MUSIC_VIDEO_TYPE_ATV":
                return True
            if vtype in ("MUSIC_VIDEO_TYPE_OMV", "MUSIC_VIDEO_TYPE_UGC"):
                return False

            album = t.get("album")
            has_album = bool(album and (album.get("name") if isinstance(album, dict) else album))
            low_t = raw_title.lower()
            has_video_noise = bool(re.search(r"\b(?:official\s+(?:music\s+)?video|music\s+video|lyric\s+video|visualizer|video)\b", low_t))
            return has_album and not has_video_noise

        parsed_candidates = []
        for t in tracks_raw:
            title = t.get("title", "")
            artists = t.get("artists", [])
            artist_name = None
            if artists and isinstance(artists, list) and len(artists) > 0:
                artist_name = artists[0].get("name") if isinstance(artists[0], dict) else str(artists[0])

            clean_t, detected_a = clean_youtube_title(title, artist_name)
            effective_artist = artist_name or detected_a or "Unknown Artist"
            effective_title = clean_t or title

            album = t.get("album")
            album_name = album.get("name") if isinstance(album, dict) else (album if isinstance(album, str) else None)

            thumb = None
            thumbs = t.get("thumbnails")
            if thumbs and isinstance(thumbs, list) and len(thumbs) > 0:
                thumb = thumbs[-1].get("url")

            duration = t.get("duration") or t.get("duration_seconds")
            music_flag = is_music_track(t, title)
            norm_key = f"{normalize_text(effective_artist)}|{normalize_text(effective_title)}"

            parsed_candidates.append({
                "video_id": t.get("videoId"),
                "title": effective_title,
                "raw_title": title,
                "artist": effective_artist,
                "album": album_name,
                "duration": duration,
                "thumbnail": thumb,
                "is_music": music_flag,
                "norm_key": norm_key,
            })

        # Group tracks to prefer official music track over video version
        groups: dict[str, list[dict]] = {}
        for c in parsed_candidates:
            groups.setdefault(c["norm_key"], []).append(c)

        selected_video_ids = set()
        for k, group in groups.items():
            if len(group) == 1:
                selected_video_ids.add(group[0]["video_id"])
            else:
                music_versions = [item for item in group if item["is_music"]]
                if music_versions:
                    # Pick official music version, ignore video versions
                    selected_video_ids.add(music_versions[0]["video_id"])
                else:
                    # No music version available, only then grab video version
                    selected_video_ids.add(group[0]["video_id"])

        matched_tracks = []
        for c in parsed_candidates:
            vid = c["video_id"]
            is_dup = vid not in selected_video_ids

            norm_key = c["norm_key"]
            title_key = normalize_text(c["title"])

            local_path = local_map.get(norm_key) or local_map.get(title_key)
            in_local = local_path is not None

            in_uploads = (norm_key in uploads_set) or (vid in uploads_video_ids)
            if not in_uploads and title_key in uploads_by_title:
                c_art = normalize_text(c["artist"])
                for u_art in uploads_by_title[title_key]:
                    if c_art and u_art and (c_art in u_art or u_art in c_art):
                        in_uploads = True
                        break
                    parts_c = {p.strip() for p in re.split(r"[,/&]|(?:\b(?:feat|ft|featuring|with|x)\b)", c_art) if p.strip()}
                    parts_u = {p.strip() for p in re.split(r"[,/&]|(?:\b(?:feat|ft|featuring|with|x)\b)", u_art) if p.strip()}
                    if parts_c & parts_u:
                        in_uploads = True
                        break

            matched_tracks.append({
                "video_id": vid,
                "title": c["title"],
                "artist": c["artist"],
                "album": c["album"],
                "duration": c["duration"],
                "thumbnail": c["thumbnail"],
                "in_local": in_local,
                "in_uploads": in_uploads or is_dup,
                "is_duplicate": is_dup,
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

    async def get_playlist_raw(self, playlist_id: str, user_id: Optional[str] = None) -> dict:
        """Fetch full raw playlist payload directly from YouTube Music."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _fetch_sync():
            yt = self._get_client(user_id=user_id)
            if playlist_id == "LM":
                return yt.get_liked_songs(limit=None)
            try:
                return yt.get_playlist(playlist_id, limit=None)
            except Exception as e:
                err_msg = str(e).lower()
                # Empty or newly created playlists raise KeyError contents or sectionListRenderer
                if "contents" in err_msg or "sectionlistrenderer" in err_msg:
                    logger.info(f"Playlist {playlist_id} is empty or has no contents renderer yet.")
                    return {"id": playlist_id, "title": "", "tracks": []}
                raise

        return await asyncio.to_thread(_fetch_sync)

    async def create_playlist(
        self,
        title: str,
        description: str = "",
        privacy_status: str = "PRIVATE",
        video_ids: Optional[list[str]] = None,
        user_id: Optional[str] = None
    ) -> str:
        """Create a new playlist in YouTube Music."""
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _create_sync():
            yt = self._get_client(user_id=user_id)
            res = yt.create_playlist(
                title=title,
                description=description,
                privacy_status=privacy_status,
                video_ids=video_ids
            )
            if isinstance(res, str):
                return res
            if isinstance(res, dict):
                return res.get("id") or res.get("playlistId") or ""
            return str(res)

        p_id = await asyncio.to_thread(_create_sync)
        logger.info(f"Created YouTube Music playlist '{title}' with ID: {p_id}")
        return p_id

    async def add_playlist_items(
        self,
        playlist_id: str,
        video_ids: list[str],
        duplicates: bool = True,
        user_id: Optional[str] = None
    ) -> Any:
        """Add tracks to an existing playlist in YouTube Music, batching in safe chunks."""
        if not video_ids:
            return {"status": "ok", "added": 0}
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _add_chunk_sync(chunk):
            yt = self._get_client(user_id=user_id)
            return yt.add_playlist_items(playlist_id, chunk, duplicates=duplicates)

        chunk_size = 50
        last_res = None
        for i in range(0, len(video_ids), chunk_size):
            chunk = video_ids[i:i + chunk_size]
            last_res = await asyncio.to_thread(_add_chunk_sync, chunk)
            if i + chunk_size < len(video_ids):
                await asyncio.sleep(0.5)

        logger.info(f"Added {len(video_ids)} tracks to playlist {playlist_id}")
        return last_res

    async def remove_playlist_items(
        self,
        playlist_id: str,
        video_items: list[dict],
        user_id: Optional[str] = None
    ) -> Any:
        """Remove tracks from a playlist in YouTube Music using videoId & setVideoId, batching in safe chunks."""
        if not video_items:
            return {"status": "ok", "removed": 0}
        if not self.is_auth_configured(user_id=user_id):
            raise YTMusicUserError("Not authenticated.")

        def _remove_chunk_sync(chunk):
            yt = self._get_client(user_id=user_id)
            return yt.remove_playlist_items(playlist_id, chunk)

        chunk_size = 50
        last_res = None
        for i in range(0, len(video_items), chunk_size):
            chunk = video_items[i:i + chunk_size]
            last_res = await asyncio.to_thread(_remove_chunk_sync, chunk)
            if i + chunk_size < len(video_items):
                await asyncio.sleep(0.5)

        logger.info(f"Removed {len(video_items)} tracks from playlist {playlist_id}")
        return last_res

ytm_client = YTMClient()

