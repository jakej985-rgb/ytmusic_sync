import asyncio
import logging
import re
import time
from typing import List, Optional
import httpx

from .models import MusicBrainzMatch

logger = logging.getLogger("ytm_sync")

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording"
ITUNES_API_URL = "https://itunes.apple.com/search"
USER_AGENT = "YTMusicSync/0.0.1 ( mailto:jakej985@gmail.com; https://github.com/jakej985-rgb/ytmusic_sync )"

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
        cleaned = re.sub(r'[\/\\:;*?+^=!~(){}\[\]"]', ' ', text)
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

    def _select_best_release(self, releases: list) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str]]:
        """
        Selects the best studio album from a list of release dictionaries.
        Prioritizes:
          1. Official Studio Albums (status=Official, type=Album, no live/compilation)
          2. Any Official Album
          3. Any Official release
          4. First available release
        """
        if not releases:
            return None, None, None, None

        best_rel = None

        # Pass 1: Studio Album
        for rel in releases:
            status = rel.get("status", "")
            rg = rel.get("release-group", {})
            ptype = rg.get("primary-type", "")
            stypes = rg.get("secondary-types", [])
            if status == "Official" and ptype == "Album" and not stypes:
                best_rel = rel
                break

        # Pass 2: Any official album
        if not best_rel:
            for rel in releases:
                status = rel.get("status", "")
                rg = rel.get("release-group", {})
                ptype = rg.get("primary-type", "")
                if status == "Official" and ptype == "Album":
                    best_rel = rel
                    break

        # Pass 3: Any official release
        if not best_rel:
            for rel in releases:
                if rel.get("status") == "Official":
                    best_rel = rel
                    break

        # Fallback to first release
        if not best_rel:
            best_rel = releases[0]

        album = best_rel.get("title")
        release_date = best_rel.get("date")
        track_number = None

        media = best_rel.get("media", [])
        if media and "track-offset" in media[0]:
            track_number = int(media[0]["track-offset"]) + 1
        elif media and "tracks" in media[0] and media[0]["tracks"]:
            track_number = int(media[0]["tracks"][0].get("number", 1))

        release_id = best_rel.get("id")
        cover_url = f"https://coverartarchive.org/release/{release_id}/front-500" if release_id else None

        return album, release_date, track_number, cover_url

    async def search(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """
        Search for recording matches using MusicBrainz with automatic retry, studio album
        prioritization, and iTunes fast fallback.
        """
        cache_key = f"{query or ''}|{artist or ''}|{title or ''}|{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        clean_artist = self._sanitize_term(artist) if artist else ""
        clean_title = self._sanitize_term(title) if title else ""
        lucene_query = ""

        if clean_title and clean_artist:
            # Query title with artist/artistname
            lucene_query = f'recording:"{clean_title}" AND (artist:"{clean_artist}" OR artistname:"{clean_artist}")'
        elif clean_title:
            lucene_query = f'recording:"{clean_title}"'
        elif clean_artist:
            lucene_query = f'artist:"{clean_artist}"'
        elif query:
            clean_q = self._sanitize_term(query)
            by_match = re.search(r'^(.*?)\s+(?:by|-)\s+(.+)$', clean_q, re.IGNORECASE)
            if by_match:
                part_a = by_match.group(1).strip()
                part_b = by_match.group(2).strip()
                lucene_query = f'recording:"{part_a}" AND (artist:"{part_b}" OR artistname:"{part_b}")'
            else:
                lucene_query = clean_q
        else:
            return []

        results = await self._execute_query(lucene_query, limit)

        # If strict lucene returned nothing and we have artist + title, try looser search
        if not results and clean_title and clean_artist:
            looser_query = f'recording:{clean_title} AND artist:{clean_artist}'
            results = await self._execute_query(looser_query, limit)

        # If an artist was explicitly specified, re-rank to ensure matches containing the requested artist are on top
        if clean_artist and results:
            target_artist_lower = clean_artist.lower()
            matching_artist_results = []
            other_results = []
            for m in results:
                if target_artist_lower in m.artist.lower() or (m.featured_artists and target_artist_lower in m.featured_artists.lower()):
                    matching_artist_results.append(m)
                else:
                    m.score = max(30, m.score - 40)
                    other_results.append(m)
            results = matching_artist_results + other_results

        # Fallback to iTunes if MusicBrainz returned 0 matches or only non-matching covers
        if len(results) < 2:
            itunes_results = await self._search_itunes(query=query, artist=clean_artist or artist, title=clean_title or title, limit=limit)
            if itunes_results:
                # Merge deduplicated by title and artist
                existing_keys = {(r.title.lower(), r.artist.lower()) for r in results}
                for itm in itunes_results:
                    key = (itm.title.lower(), itm.artist.lower())
                    if key not in existing_keys:
                        results.append(itm)
                        existing_keys.add(key)
                results = results[:limit]

        self._cache[cache_key] = results
        return results

    async def _execute_query(self, query_str: str, limit: int) -> List[MusicBrainzMatch]:
        """Executes a search against MusicBrainz with retry on 503 Service Unavailable."""
        params = {
            "query": query_str,
            "fmt": "json",
            "limit": limit
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }

        for attempt in range(3):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(MUSICBRAINZ_API_URL, params=params, headers=headers)
                    if response.status_code == 200:
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
                            album, release_date, track_number, cover_url = self._select_best_release(releases)

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
                                    cover_url=cover_url,
                                    score=score
                                )
                            )

                        return matches

                    elif response.status_code == 503:
                        logger.warning(f"MusicBrainz server busy (503), retrying attempt {attempt + 1}/3...")
                        await asyncio.sleep(1.5 + attempt * 0.5)
                        continue
                    else:
                        logger.warning(f"MusicBrainz search returned status {response.status_code}: {response.text[:100]}")
                        break

            except Exception as e:
                logger.warning(f"MusicBrainz query exception on attempt {attempt + 1}: {e}")
                await asyncio.sleep(1.0)

        return []

    async def _search_itunes(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """
        Fast, reliable metadata fallback using the iTunes Search API.
        Never throttled and provides studio albums and clean track numbers.
        """
        search_terms = []
        if title:
            search_terms.append(title)
        if artist:
            search_terms.append(artist)
        if not search_terms and query:
            search_terms.append(query)

        term = " ".join(search_terms).strip()
        if not term:
            return []

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    ITUNES_API_URL,
                    params={"term": term, "entity": "song", "limit": limit}
                )
                if response.status_code != 200:
                    return []

                items = response.json().get("results", [])
                matches: List[MusicBrainzMatch] = []

                for item in items:
                    track_name = item.get("trackName", "").strip()
                    artist_name = item.get("artistName", "").strip()
                    album_name = item.get("collectionName")
                    track_num = item.get("trackNumber")
                    rel_date = item.get("releaseDate")
                    if rel_date and len(rel_date) >= 10:
                        rel_date = rel_date[:10]

                    # Parse possible featured artists in track name
                    feat_match = re.search(r'^(.*?)\s+(?:\(|\[)?(?:feat\.|ft\.|featuring)\s+(.+?)(?:\)|\])?$', track_name, re.IGNORECASE)
                    primary_title = track_name
                    featured_artists = None
                    if feat_match:
                        primary_title = feat_match.group(1).strip()
                        featured_artists = feat_match.group(2).strip()

                    cover_url = item.get("artworkUrl100", "").replace("100x100bb", "600x600bb") or None

                    matches.append(
                        MusicBrainzMatch(
                            mbid=f"itunes:{item.get('trackId')}",
                            title=track_name,
                            primary_title=primary_title,
                            artist=artist_name,
                            featured_artists=featured_artists,
                            album=album_name,
                            track_number=track_num,
                            release_date=rel_date,
                            cover_url=cover_url,
                            score=98
                        )
                    )

                return matches

        except Exception as e:
            logger.warning(f"iTunes fallback query failed: {e}")
            return []

    async def fetch_cover_art_url(self, artist: str, title: Optional[str] = None, album: Optional[str] = None) -> Optional[str]:
        """Query iTunes for high resolution 600x600 album artwork URL."""
        terms = []
        if title:
            terms.append(title)
        if artist:
            terms.append(artist)
        if album and not terms:
            terms.append(album)
        term = " ".join(terms).strip()
        if not term:
            return None
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(ITUNES_API_URL, params={"term": term, "entity": "song", "limit": 1})
                if r.status_code == 200:
                    items = r.json().get("results", [])
                    if items:
                        art = items[0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
                        if art:
                            return art
        except Exception as e:
            logger.warning(f"Error fetching cover art URL: {e}")
        return None

musicbrainz_client = MusicBrainzClient()
