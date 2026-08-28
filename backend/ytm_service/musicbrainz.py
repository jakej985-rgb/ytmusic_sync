import asyncio
import logging
import re
import time
from typing import List, Optional
import httpx

from .models import MusicBrainzMatch

logger = logging.getLogger("ytm_sync")

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "YTMusicSync/0.0.1 ( https://github.com/jakej985-rgb/ytmusic_sync )"

class MusicBrainzClient:
    def __init__(self):
        self._last_request_time: float = 0.0
        self._rate_limit_seconds: float = 1.1
        self._cache: dict[str, List[MusicBrainzMatch]] = {}

    async def _throttle(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit_seconds:
            await asyncio.sleep(self._rate_limit_seconds - elapsed)
        self._last_request_time = time.time()

    def _sanitize_term(self, text: str) -> str:
        """Removes characters that can break Lucene search queries."""
        cleaned = re.sub(r'[\/\\:;*?+^=!~(){}\[\]]', ' ', text)
        return re.sub(r'\s+', ' ', cleaned).strip()

    def _parse_artist_credits(self, credits_list: list) -> tuple[str, Optional[str]]:
        """
        Parses MusicBrainz artist-credit array into (primary_artist, featured_artists).
        Example:
          [{"name": "C-Mob", "joinphrase": " feat. "}, {"name": "Brotha Lynch Hung", "joinphrase": ""}]
          -> ("C-Mob", "Brotha Lynch Hung")
        """
        if not credits_list:
            return ("Unknown Artist", None)

        primary_artist = credits_list[0].get("name", "").strip()
        
        if len(credits_list) == 1:
            feat_match = re.search(r'^(.*?)\s+(?:feat\.|ft\.|featuring)\s+(.+)$', primary_artist, re.IGNORECASE)
            if feat_match:
                return (feat_match.group(1).strip(), feat_match.group(2).strip())
            return (primary_artist, None)

        featured_parts = []
        for item in credits_list[1:]:
            name = item.get("name", "").strip()
            if name:
                featured_parts.append(name)

        featured_str = ", ".join(featured_parts) if featured_parts else None
        return (primary_artist, featured_str)

    async def search(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """
        Search MusicBrainz for recording matches.
        """
        cache_key = f"{query or ''}|{artist or ''}|{title or ''}|{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        lucene_query = ""
        clean_artist = self._sanitize_term(artist) if artist else ""
        clean_title = self._sanitize_term(title) if title else ""

        if clean_title and clean_artist:
            lucene_query = f'recording:"{clean_title}" AND artist:"{clean_artist}"'
        elif clean_title:
            lucene_query = f'recording:"{clean_title}"'
        elif query:
            clean_q = self._sanitize_term(query)
            by_match = re.search(r'^(.*?)\s+(?:by|-)\s+(.+)$', clean_q, re.IGNORECASE)
            if by_match:
                lucene_query = f'recording:"{by_match.group(1).strip()}" AND artist:"{by_match.group(2).strip()}"'
            else:
                lucene_query = clean_q
        else:
            return []

        results = await self._execute_query(lucene_query, limit)

        if not results and (clean_artist or clean_title):
            fallback_q = f"{clean_title} {clean_artist}".strip()
            if fallback_q and fallback_q != lucene_query:
                results = await self._execute_query(fallback_q, limit)

        self._cache[cache_key] = results
        return results

    async def _execute_query(self, query_str: str, limit: int) -> List[MusicBrainzMatch]:
        await self._throttle()

        params = {
            "query": query_str,
            "fmt": "json",
            "limit": limit
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(MUSICBRAINZ_API_URL, params=params, headers=headers)
                if response.status_code != 200:
                    logger.warning(f"MusicBrainz search returned status {response.status_code}: {response.text[:100]}")
                    return []

                data = response.json()
                recordings = data.get("recordings", [])
                matches: List[MusicBrainzMatch] = []

                for rec in recordings:
                    mbid = rec.get("id", "")
                    primary_title = rec.get("title", "").strip()
                    score = int(rec.get("score", 100))

                    credits_list = rec.get("artist-credit", [])
                    primary_artist, featured_artists = self._parse_artist_credits(credits_list)

                    if featured_artists and not re.search(r'\b(?:feat\.|ft\.|featuring)\b', primary_title, re.IGNORECASE):
                        display_title = f"{primary_title} ft. {featured_artists}"
                    else:
                        display_title = primary_title

                    releases = rec.get("releases", [])
                    album: Optional[str] = None
                    release_date: Optional[str] = None
                    track_number: Optional[int] = None

                    if releases:
                        first_rel = releases[0]
                        album = first_rel.get("title")
                        release_date = first_rel.get("date")
                        media = first_rel.get("media", [])
                        if media and "track-offset" in media[0]:
                            track_number = int(media[0]["track-offset"]) + 1

                    matches.append(
                        MusicBrainzMatch(
                            mbid=mbid,
                            title=display_title,
                            primary_title=primary_title,
                            artist=primary_artist,
                            featured_artists=featured_artists,
                            album=album,
                            track_number=track_number,
                            release_date=release_date,
                            score=score
                        )
                    )

                return matches

        except Exception as e:
            logger.error(f"Error querying MusicBrainz API: {e}", exc_info=True)
            return []

musicbrainz_client = MusicBrainzClient()
