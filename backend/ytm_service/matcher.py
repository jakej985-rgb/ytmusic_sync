"""
========================================================================================
CRITICAL UPLOAD INTEGRITY DIRECTIVE (Phase 15 Architecture Lock):
"An Upload Locker item may only be downloaded from its exact authenticated upload source.
Metadata may never be used to find a replacement source. If the exact source cannot be
downloaded, the operation fails and the existing local file remains untouched."
========================================================================================
"""

import difflib
import logging
from typing import Optional
from .models import MatchType, MusicFile, SyncDecision
from .normalizer import normalize_text
from .database import db

logger = logging.getLogger("ytm_sync.matcher")

def string_similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def evaluate_match(
    local_artist: Optional[str],
    local_album: Optional[str],
    local_title: Optional[str],
    local_duration: Optional[float],
    ytm_artist: Optional[str],
    ytm_album: Optional[str],
    ytm_title: Optional[str],
    ytm_duration: Optional[float]
) -> tuple[MatchType, float]:
    """
    Evaluates confidence score and MatchType between local track and YTM upload.
    Returns (MatchType, score: 0.0 - 1.0).
    """
    n_loc_art = normalize_text(local_artist)
    n_ytm_art = normalize_text(ytm_artist)
    n_loc_title = normalize_text(local_title)
    n_ytm_title = normalize_text(ytm_title)
    n_loc_alb = normalize_text(local_album)
    n_ytm_alb = normalize_text(ytm_album)

    # 1. Exact match test
    art_exact = (n_loc_art == n_ytm_art) or (not n_loc_art and not n_ytm_art)
    title_exact = (n_loc_title == n_ytm_title) and (len(n_loc_title) > 0)
    alb_exact = (n_loc_alb == n_ytm_alb) or (not n_loc_alb and not n_ytm_alb)
    
    # Duration tolerance test (+- 3.5 seconds)
    dur_exact = False
    if local_duration is not None and ytm_duration is not None:
        dur_exact = abs(local_duration - ytm_duration) <= 3.5
    elif local_duration is None or ytm_duration is None:
        # If one has no duration tag, neutral
        dur_exact = True

    if art_exact and title_exact and alb_exact and dur_exact:
        return MatchType.EXACT, 1.0

    if art_exact and title_exact and dur_exact:
        return MatchType.STRONG, 0.85

    # 2. Fuzzy match test
    art_sim = string_similarity(n_loc_art, n_ytm_art)
    title_sim = string_similarity(n_loc_title, n_ytm_title)
    
    # Check containment (e.g. "Song (Live)" vs "Song")
    if (n_loc_title in n_ytm_title or n_ytm_title in n_loc_title) and (art_sim > 0.8 or art_exact):
        title_sim = max(title_sim, 0.85)

    if art_sim >= 0.8 and title_sim >= 0.8:
        # Check duration if both exist
        if local_duration is not None and ytm_duration is not None:
            if abs(local_duration - ytm_duration) <= 8.0:
                score = round((art_sim * 0.4 + title_sim * 0.4 + 0.2), 2)
                return MatchType.STRONG if score >= 0.85 else MatchType.WEAK, score
            else:
                # Duration mismatch strongly indicates different live/radio/extended version
                return MatchType.WEAK, 0.45
        return MatchType.WEAK, round((art_sim + title_sim) / 2.0, 2)

    if art_sim >= 0.6 and title_sim >= 0.7:
        return MatchType.WEAK, round((art_sim + title_sim) / 2.0, 2)

    return MatchType.NONE, 0.0


def evaluate_sync_decision(
    *,
    expected_id: Optional[str] = None,
    actual_id: Optional[str] = None,
    source_type: Optional[str] = None,
    is_private_inaccessible: bool = False,
    destination_exists: bool = False,
    allow_overwrite: bool = False,
    audio_valid: Optional[bool] = None,
    metadata_match: Optional[str] = None,
    has_search_fallback: bool = False,
) -> tuple[SyncDecision, str]:
    """
    Three-way sync decision engine (Phase 7):
    - SAFE: Exact upload identity verified (expected_id == actual_id).
    - REVIEW: Metadata compatible, but identity cannot be authoritatively established.
              Never automatically downloads or replaces.
    - BLOCKED: Security or integrity failure (private inaccessible, wrong video ID,
               ytsearch fallback, unknown source type, audio validation failed, destination exists).
    """
    # 1. BLOCKED conditions
    if is_private_inaccessible:
        return SyncDecision.BLOCKED, "Private upload stream inaccessible or authentication failure"

    if has_search_fallback:
        return SyncDecision.BLOCKED, "ytsearch fallback prohibited for uploads"

    if source_type and source_type not in ("ytm_upload", "catalog"):
        return SyncDecision.BLOCKED, f"Source type '{source_type}' unknown or invalid"

    if destination_exists and not allow_overwrite:
        return SyncDecision.BLOCKED, "Destination file already exists; automatic replacement is blocked"

    if audio_valid is False:
        return SyncDecision.BLOCKED, "Audio integrity validation failed"

    if expected_id and actual_id and expected_id != actual_id:
        return SyncDecision.BLOCKED, f"Wrong video ID: expected '{expected_id}', got '{actual_id}'"

    if metadata_match == "none":
        return SyncDecision.BLOCKED, "Download metadata mismatch"

    # 2. SAFE condition
    if expected_id and actual_id and expected_id == actual_id:
        return SyncDecision.SAFE, "Exact upload identity verified"

    # 3. REVIEW conditions
    if metadata_match in ("exact", "strong"):
        return SyncDecision.REVIEW, "Metadata compatible but source identity not authoritatively established"

    if metadata_match == "weak":
        return SyncDecision.REVIEW, "Weak metadata match requires manual review"

    return SyncDecision.BLOCKED, "Default fail-closed"


class MatchingEngine:
    async def match_all(self) -> dict:
        """Run comparison between all local music files and YTM uploads."""
        local_files = await db.get_music_files(limit=100000)
        ytm_uploads = await db.get_all_ytm_uploads()

        matched_count = 0
        exact_count = 0
        strong_count = 0
        weak_count = 0

        # Build lookup tables to speed up matching
        # 1. exact normalized (artist, title, album)
        # 2. exact normalized (artist, title)
        title_map: dict[str, list] = {}
        for y in ytm_uploads:
            n_t = normalize_text(y.title)
            if n_t not in title_map:
                title_map[n_t] = []
            title_map[n_t].append(y)

        for loc in local_files:
            best_match: Optional[tuple] = None
            best_score = 0.0
            best_type = MatchType.NONE

            n_loc_title = normalize_text(loc.title)
            candidates = title_map.get(n_loc_title, [])
            
            # If no direct title match, consider all YTM uploads for fuzzy search
            search_pool = candidates if candidates else ytm_uploads

            for y in search_pool:
                m_type, score = evaluate_match(
                    loc.artist, loc.album, loc.title, loc.duration,
                    y.artist, y.album, y.title, y.duration
                )
                if score > best_score:
                    best_score = score
                    best_type = m_type
                    best_match = y
                if best_type == MatchType.EXACT:
                    break

            if best_match and best_type in (MatchType.EXACT, MatchType.STRONG):
                decision, reason = evaluate_sync_decision(
                    expected_id=getattr(loc, "matched_upload_id", None) or getattr(best_match, "upload_video_id", None),
                    actual_id=getattr(best_match, "upload_video_id", None) or getattr(best_match, "video_id", None),
                    source_type=getattr(best_match, "source_type", "ytm_upload"),
                    metadata_match=best_type.value
                )
                await db.save_match(
                    file_id=loc.id,
                    ytm_upload_id=best_match.entity_id,
                    match_type=best_type,
                    score=best_score,
                    sync_decision=decision.value,
                    decision_reason=reason
                )
                matched_count += 1
                if best_type == MatchType.EXACT:
                    exact_count += 1
                else:
                    strong_count += 1
            elif best_match and best_type == MatchType.WEAK:
                weak_count += 1

        return {
            "total_local": len(local_files),
            "total_ytm": len(ytm_uploads),
            "matched": matched_count,
            "exact": exact_count,
            "strong": strong_count,
            "weak_potential": weak_count,
            "missing": len(local_files) - matched_count
        }

    async def match_single_file(self, loc: MusicFile) -> Optional[dict]:
        """Evaluate matching for a single music file against known YTM uploads."""
        if not loc.id:
            return None
        ytm_uploads = await db.get_all_ytm_uploads()
        if not ytm_uploads:
            return None

        best_match = None
        best_score = 0.0
        best_type = MatchType.NONE

        for y in ytm_uploads:
            m_type, score = evaluate_match(
                loc.artist, loc.album, loc.title, loc.duration,
                y.artist, y.album, y.title, y.duration
            )
            if score > best_score:
                best_score = score
                best_type = m_type
                best_match = y
            if best_type == MatchType.EXACT:
                break

        if best_match and best_type in (MatchType.EXACT, MatchType.STRONG):
            decision, reason = evaluate_sync_decision(
                expected_id=getattr(loc, "matched_upload_id", None) or getattr(best_match, "upload_video_id", None),
                actual_id=getattr(best_match, "upload_video_id", None) or getattr(best_match, "video_id", None),
                source_type=getattr(best_match, "source_type", "ytm_upload"),
                metadata_match=best_type.value
            )
            await db.save_match(
                file_id=loc.id,
                ytm_upload_id=best_match.entity_id,
                match_type=best_type,
                score=best_score,
                sync_decision=decision.value,
                decision_reason=reason
            )
            return {
                "matched": True,
                "upload_id": best_match.entity_id,
                "type": best_type.value,
                "score": best_score,
                "sync_decision": decision.value,
                "decision_reason": reason
            }

        return {"matched": False}

matcher = MatchingEngine()
