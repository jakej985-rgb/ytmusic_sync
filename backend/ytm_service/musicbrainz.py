import asyncio
import logging
import re
import time
from typing import List, Optional
import httpx
from ytmusicapi import YTMusic

from .models import MusicBrainzMatch

logger = logging.getLogger("ytm_sync")

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording"
ITUNES_API_URL = "https://itunes.apple.com/search"
DEEZER_API_URL = "https://api.deezer.com/search"
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

    def _clean_ripper_junk(self, name: str) -> str:
        """Strips ripper prefixes, YouTube video junk, and normalizes underscores."""
        s = re.sub(r'^(?:y2mate(?:\.com|\.is)?|snapsave(?:\.app|\.io)?|tuberipper(?:\.com)?|youtube)\s*[-_–]\s*', '', name, flags=re.IGNORECASE)
        s = re.sub(r'\b(?:official\s+music\s+video|official\s+video|official\s+audio|lyrics\s+video|music\s+video|video\s+clip|official)\b', '', s, flags=re.IGNORECASE)
        s = re.sub(r'\s*[\(\[](?:official.*?|lyrics.*?|hd|hq|1080p|720p|audio|video)[\)\]]', '', s, flags=re.IGNORECASE)
        if '_' in s and ' - ' not in s:
            s = s.replace('_', ' ')
        return re.sub(r'\s+', ' ', s).strip()

    def _parse_artist_credits(self, credits_list: list) -> tuple[str, Optional[str]]:
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
        if not releases:
            return None, None, None, None

        best_rel = None
        for rel in releases:
            status = rel.get("status", "")
            rg = rel.get("release-group", {})
            ptype = rg.get("primary-type", "")
            stypes = rg.get("secondary-types", [])
            if status == "Official" and ptype == "Album" and not stypes:
                best_rel = rel
                break

        if not best_rel:
            for rel in releases:
                status = rel.get("status", "")
                rg = rel.get("release-group", {})
                ptype = rg.get("primary-type", "")
                if status == "Official" and ptype == "Album":
                    best_rel = rel
                    break

        if not best_rel:
            for rel in releases:
                if rel.get("status") == "Official":
                    best_rel = rel
                    break

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

    async def _search_ytmusic(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """Search YouTube Music's own official catalog (both songs and videos) for exact metadata."""
        search_terms = []
        if artist and title:
            search_terms.append(f"{artist} - {title}")
        elif title:
            search_terms.append(title)
        elif artist:
            search_terms.append(artist)
        elif query:
            search_terms.append(query)

        term = self._clean_ripper_junk(" ".join(search_terms).strip())
        if not term:
            return []

        def _sync_ytm():
            results = []
            try:
                yt = YTMusic()
                # 1. Official songs catalog
                try:
                    s_res = yt.search(term, filter="songs", limit=limit)
                    if s_res:
                        results.extend(s_res)
                except Exception as e:
                    logger.debug(f"YTM songs search error: {e}")

                # 2. Videos catalog (crucial for YouTube music videos, featured collabs, and singles)
                try:
                    v_res = yt.search(term, filter="videos", limit=limit)
                    if v_res:
                        results.extend(v_res)
                except Exception as e:
                    logger.debug(f"YTM videos search error: {e}")

                return results
            except Exception as e:
                logger.warning(f"YouTube Music catalog search error: {e}")
                return []

        items = await asyncio.to_thread(_sync_ytm)
        matches: List[MusicBrainzMatch] = []
        for s in items:
            raw_title = s.get("title", "").strip()
            artists = [a.get("name") for a in s.get("artists", []) if isinstance(a, dict)]
            artist_name = ", ".join(artists) if artists else "Unknown Artist"
            album_name = s.get("album", {}).get("name") if isinstance(s.get("album"), dict) else (s.get("album") or None)

            track_name = raw_title
            # If title is in 'Artist - Title' format (common in YouTube music videos)
            if " - " in raw_title:
                p1, p2 = raw_title.split(" - ", 1)
                if artist_name == "Unknown Artist" or artist_name.lower() in p1.lower() or p1.lower() in artist_name.lower():
                    artist_name = p1.strip()
                    track_name = p2.strip()

            thumb = None
            if s.get("thumbnails"):
                thumb = s.get("thumbnails")[-1].get("url")
                if thumb and "=w120-h120" in thumb:
                    thumb = re.sub(r'=w\d+-h\d+', '=w600-h600', thumb)
            elif s.get("videoId"):
                thumb = f"https://i.ytimg.com/vi/{s.get('videoId')}/hqdefault.jpg"

            feat_match = re.search(r'^(.*?)\s+(?:\(|\[)?(?:feat\.|ft\.|featuring)\s+(.+?)(?:\)|\])?$', track_name, re.IGNORECASE)
            primary_title = track_name
            featured_artists = None
            if feat_match:
                primary_title = feat_match.group(1).strip()
                featured_artists = feat_match.group(2).strip()

            matches.append(
                MusicBrainzMatch(
                    mbid=f"ytm:{s.get('videoId') or track_name}",
                    title=track_name,
                    primary_title=primary_title,
                    artist=artist_name,
                    featured_artists=featured_artists,
                    album=album_name,
                    cover_url=thumb,
                    source="YouTube Music",
                    score=98
                )
            )
        return matches

    async def _search_deezer(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """Search Deezer API (90M+ tracks, instant response, lossless album covers)."""
        search_terms = []
        if artist and title:
            search_terms.append(f"{artist} {title}")
        elif title:
            search_terms.append(title)
        elif artist:
            search_terms.append(artist)
        elif query:
            search_terms.append(query)

        term = self._clean_ripper_junk(" ".join(search_terms).strip())
        if not term:
            return []

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                r = await client.get(DEEZER_API_URL, params={"q": term, "limit": limit})
                if r.status_code != 200:
                    return []
                items = r.json().get("data", [])
                matches: List[MusicBrainzMatch] = []
                for d in items:
                    track_name = d.get("title", "").strip()
                    artist_name = d.get("artist", {}).get("name", "").strip()
                    album_name = d.get("album", {}).get("title")
                    cover = d.get("album", {}).get("cover_big") or d.get("album", {}).get("cover_medium")
                    track_num = d.get("track_position")

                    feat_match = re.search(r'^(.*?)\s+(?:\(|\[)?(?:feat\.|ft\.|featuring)\s+(.+?)(?:\)|\])?$', track_name, re.IGNORECASE)
                    primary_title = track_name
                    featured_artists = None
                    if feat_match:
                        primary_title = feat_match.group(1).strip()
                        featured_artists = feat_match.group(2).strip()

                    matches.append(
                        MusicBrainzMatch(
                            mbid=f"deezer:{d.get('id')}",
                            title=track_name,
                            primary_title=primary_title,
                            artist=artist_name,
                            featured_artists=featured_artists,
                            album=album_name,
                            track_number=track_num,
                            cover_url=cover,
                            source="Deezer",
                            score=95
                        )
                    )
                return matches
        except Exception as e:
            logger.warning(f"Deezer search error: {e}")
            return []

    async def _search_itunes(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """Search iTunes / Apple Music catalog for official tracks."""
        search_terms = []
        if title:
            search_terms.append(title)
        if artist:
            search_terms.append(artist)
        if not search_terms and query:
            search_terms.append(query)

        term = self._clean_ripper_junk(" ".join(search_terms).strip())
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
                            source="Apple Music",
                            score=92
                        )
                    )
                return matches
        except Exception as e:
            logger.warning(f"iTunes query failed: {e}")
            return []

    async def _search_musicbrainz(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 5
    ) -> List[MusicBrainzMatch]:
        """Search MusicBrainz with retry on 503."""
        clean_artist = self._sanitize_term(artist) if artist else ""
        clean_title = self._sanitize_term(title) if title else ""
        lucene_query = ""

        if clean_title and clean_artist:
            lucene_query = f'recording:"{clean_title}" AND (artist:"{clean_artist}" OR artistname:"{clean_artist}")'
        elif clean_title:
            lucene_query = f'recording:"{clean_title}"'
        elif clean_artist:
            lucene_query = f'artist:"{clean_artist}"'
        elif query:
            clean_q = self._clean_ripper_junk(self._sanitize_term(query))
            if " - " in clean_q:
                p_a, p_b = clean_q.split(" - ", 1)
                # Try artist - title
                lucene_query = f'(recording:"{p_b.strip()}" AND artist:"{p_a.strip()}") OR (recording:"{p_a.strip()}" AND artist:"{p_b.strip()}")'
            else:
                lucene_query = clean_q
        else:
            return []

        params = {"query": lucene_query, "fmt": "json", "limit": limit}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

        for attempt in range(2):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=12.0) as client:
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
                                    source="MusicBrainz",
                                    score=min(90, score)
                                )
                            )
                        return matches
                    elif response.status_code == 503:
                        await asyncio.sleep(1.0)
                        continue
                    else:
                        break
            except Exception as e:
                logger.debug(f"MusicBrainz query error: {e}")
                await asyncio.sleep(0.8)

        return []

    async def search(
        self,
        query: Optional[str] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        limit: int = 6
    ) -> List[MusicBrainzMatch]:
        """
        Unified multi-source metadata search across YouTube Music, Deezer, Apple Music, and MusicBrainz.
        """
        cache_key = f"{query or ''}|{artist or ''}|{title or ''}|{limit}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Target terms for scoring
        target_a = (artist or "").strip().lower()
        target_t = (title or "").strip().lower()
        target_feat = ""
        if not target_a and not target_t and query:
            q_clean = self._clean_ripper_junk(query.strip())
            if " - " in q_clean:
                p1, p2 = q_clean.split(" - ", 1)
                target_a, target_t = p1.strip().lower(), p2.strip().lower()
            elif " by " in q_clean.lower():
                p1, p2 = re.split(r'\s+by\s+', q_clean, flags=re.IGNORECASE, maxsplit=1)
                target_t, target_a = p1.strip().lower(), p2.strip().lower()
            else:
                target_t = q_clean.lower()

        # Check for feat/ft in query
        full_q = (query or f"{artist} {title}").lower()
        feat_match = re.search(r'\b(?:feat\.|ft\.|featuring)\s+(.+?)(?:\)|\]|$)', full_q)
        if feat_match:
            target_feat = feat_match.group(1).strip()
            # Strip ft. ... from target_t to isolate primary title
            target_t = re.sub(r'\s*(?:\(|\[)?(?:feat\.|ft\.|featuring).*$', '', target_t).strip()

        # Run multi-source searches in parallel
        tasks = [
            self._search_ytmusic(query=query, artist=artist, title=title, limit=limit),
            self._search_deezer(query=query, artist=artist, title=title, limit=limit),
            self._search_itunes(query=query, artist=artist, title=title, limit=limit),
            self._search_musicbrainz(query=query, artist=artist, title=title, limit=limit),
        ]
        results_nested = await asyncio.gather(*tasks, return_exceptions=True)

        all_candidates: List[MusicBrainzMatch] = []
        for r in results_nested:
            if isinstance(r, list):
                all_candidates.extend(r)

        # Score candidates
        for m in all_candidates:
            m_a = m.artist.lower()
            m_t = m.title.lower()
            m_pt = m.primary_title.lower()
            m_feat = (m.featured_artists or "").lower()

            artist_match = bool(target_a and (target_a in m_a or m_a in target_a or target_a in m_feat))
            title_match = bool(target_t and (target_t in m_t or m_t in target_t or target_t in m_pt or m_pt in target_t))

            # Featured artist match bonus
            feat_match_score = False
            if target_feat and m_feat:
                for f_part in re.split(r'[,&]\s*', target_feat):
                    f_clean = f_part.strip()
                    if f_clean and (f_clean in m_feat or m_feat in f_clean or f_clean in m_t):
                        feat_match_score = True
                        break

            if artist_match and title_match:
                m.score = 100
            elif title_match and feat_match_score:
                m.score = 100
            elif artist_match and feat_match_score:
                m.score = 95
            elif artist_match:
                m.score = 85
            elif title_match:
                m.score = 80
            elif feat_match_score:
                m.score = 75
            else:
                m.score = max(35, m.score - 20)

        # Deduplicate candidates across providers by (normalized primary title, normalized artist)
        unique_matches: List[MusicBrainzMatch] = []
        seen = set()
        for m in sorted(all_candidates, key=lambda x: x.score, reverse=True):
            norm_key = (
                re.sub(r'[\W_]+', '', m.primary_title.lower()),
                re.sub(r'[\W_]+', '', m.artist.lower())
            )
            if norm_key not in seen:
                seen.add(norm_key)
                unique_matches.append(m)

        final_results = unique_matches[:limit]
        self._cache[cache_key] = final_results
        return final_results

    async def fetch_cover_art_url(self, artist: str, title: Optional[str] = None, album: Optional[str] = None) -> Optional[str]:
        """Fetch high resolution album artwork URL across YTM, Deezer, and iTunes."""
        # 1. Try Deezer
        deezer_matches = await self._search_deezer(artist=artist, title=title or album, limit=1)
        if deezer_matches and deezer_matches[0].cover_url:
            return deezer_matches[0].cover_url

        # 2. Try YouTube Music
        ytm_matches = await self._search_ytmusic(artist=artist, title=title or album, limit=1)
        if ytm_matches and ytm_matches[0].cover_url:
            return ytm_matches[0].cover_url

        # 3. Try iTunes
        itunes_matches = await self._search_itunes(artist=artist, title=title or album, limit=1)
        if itunes_matches and itunes_matches[0].cover_url:
            return itunes_matches[0].cover_url

        return None

musicbrainz_client = MusicBrainzClient()
