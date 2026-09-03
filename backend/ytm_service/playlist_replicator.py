"""
Playlist Replicator Engine:
Maintains an exact 1:1 replica of a YouTube Music source playlist
using ONLY tracks that exist in the user's YouTube Music Upload Locker.

Core Invariants:
1. Destination Tracks ⊆ Locker Uploads
2. Destination Order = Source Order filtered by Locker Uploads
3. Source Playlist is strictly READ-ONLY
4. Preserves duplicate track occurrences and their positions
"""

import logging
from typing import Optional, Any
from datetime import datetime, timezone
from .database import db
from .ytm_client import ytm_client
from .normalizer import normalize_text, parse_duration
from .models import ReplicatedPlaylist

logger = logging.getLogger("ytm_sync.playlist_replicator")


def build_locker_lookup(uploads: list[Any]) -> dict:
    """
    Build indexed lookups of verified upload locker items.
    """
    by_video_id = {}
    by_entity_id = {}
    by_exact_metadata = {}

    for u in uploads:
        u_dict = u.model_dump() if hasattr(u, "model_dump") else (u.dict() if hasattr(u, "dict") else dict(u))
        vid = u_dict.get("video_id") or u_dict.get("upload_video_id")
        eid = u_dict.get("entity_id")

        if vid:
            by_video_id[str(vid).strip()] = u_dict
        if eid:
            by_entity_id[str(eid).strip()] = u_dict

        norm_art = normalize_text(u_dict.get("artist"))
        norm_tit = normalize_text(u_dict.get("title"))
        if norm_art and norm_tit:
            key = (norm_art, norm_tit)
            if key not in by_exact_metadata:
                by_exact_metadata[key] = []
            by_exact_metadata[key].append(u_dict)

    return {
        "by_video_id": by_video_id,
        "by_entity_id": by_entity_id,
        "by_exact_metadata": by_exact_metadata,
    }


def match_source_track_to_locker(source_track: dict, locker_lookup: dict) -> tuple[Optional[dict], Optional[str]]:
    """
    Match a single source playlist track against the locker lookup using tiered safety gates.
    Returns (matched_locker_record, exclusion_reason).
    """
    vid = str(source_track.get("videoId") or source_track.get("id") or "").strip()

    # Tier 1: Exact video ID match in locker uploads
    if vid and vid in locker_lookup["by_video_id"]:
        return locker_lookup["by_video_id"][vid], None

    # Tier 2: Exact upload entity ID match
    if vid and vid in locker_lookup["by_entity_id"]:
        return locker_lookup["by_entity_id"][vid], None

    # Tier 3: Strict exact metadata equality with duration sanity check
    # Used only if unambiguous (exactly 1 locker candidate matches)
    norm_art = normalize_text(source_track.get("artist"))
    norm_tit = normalize_text(source_track.get("title"))

    if not norm_art:
        artists = source_track.get("artists")
        if isinstance(artists, list) and artists:
            first = artists[0]
            norm_art = normalize_text(first.get("name") if isinstance(first, dict) else str(first))

    if norm_art and norm_tit:
        candidates = locker_lookup["by_exact_metadata"].get((norm_art, norm_tit), [])
        if len(candidates) == 1:
            candidate = candidates[0]
            # Verify duration if both available
            src_dur = source_track.get("duration_seconds")
            if src_dur is None and "duration" in source_track:
                src_dur = parse_duration(source_track["duration"])
            cand_dur = candidate.get("duration")

            if src_dur and cand_dur:
                if abs(float(src_dur) - float(cand_dur)) <= 3.0:
                    return candidate, None
                else:
                    return None, "DURATION_MISMATCH"
            return candidate, None
        elif len(candidates) > 1:
            return None, "IDENTITY_AMBIGUOUS"

    return None, "NOT_PRESENT_IN_LOCKER"


def filter_source_tracks_for_replica(source_tracks: list[dict], locker_lookup: dict) -> tuple[list[dict], list[dict]]:
    """
    Filter source tracks preserving exact sequence and duplicate positions.
    Returns (desired_tracks, excluded_tracks).
    """
    desired_tracks = []
    excluded_tracks = []

    for idx, st in enumerate(source_tracks):
        matched, reason = match_source_track_to_locker(st, locker_lookup)
        if matched:
            target_vid = matched.get("video_id") or matched.get("upload_video_id") or st.get("videoId")
            desired_tracks.append({
                "position": len(desired_tracks),
                "video_id": target_vid,
                "title": st.get("title") or matched.get("title"),
                "artist": st.get("artist") or matched.get("artist"),
                "source_video_id": st.get("videoId"),
                "locker_upload_id": matched.get("entity_id") or target_vid
            })
        else:
            reason_str = reason or "NOT_PRESENT_IN_LOCKER"
            human_reason = "Not present in upload locker" if reason_str == "NOT_PRESENT_IN_LOCKER" else reason_str
            t_name = st.get("title") or "Unknown Title"
            a_name = st.get("artist") or "Unknown Artist"
            logger.info(f"Source track excluded: '{a_name} - {t_name}' | Reason: {human_reason}")
            excluded_tracks.append({
                "source_position": idx,
                "video_id": st.get("videoId"),
                "title": t_name,
                "artist": a_name,
                "reason": reason_str,
                "human_reason": human_reason
            })

    return desired_tracks, excluded_tracks


def calculate_reconciliation_diff(current_dest_tracks: list[dict], desired_tracks: list[dict]) -> dict:
    """
    Compute diff between current destination playlist tracks and the desired ordered tracks.
    Returns planned actions: additions, removals, and moves.
    """
    current_vids = [t.get("videoId") for t in current_dest_tracks if t.get("videoId")]
    desired_vids = [t.get("video_id") for t in desired_tracks if t.get("video_id")]

    # If already exact 1:1 match in order and count
    if current_vids == desired_vids:
        return {
            "status": "IN_SYNC",
            "actions": [],
            "additions": [],
            "removals": [],
            "reordered": False
        }

    # Identify items to remove (tracks in destination not in desired list, or excess duplicates)
    removals = []
    current_pool = list(current_dest_tracks)
    desired_pool = list(desired_vids)

    # Walk current to see what matches desired occurrences
    for item in current_pool:
        vid = item.get("videoId")
        if vid in desired_pool:
            desired_pool.remove(vid)
        else:
            removals.append({
                "videoId": vid,
                "setVideoId": item.get("setVideoId")
            })

    # If destination has items out of order or additions needed
    actions = []
    for r in removals:
        actions.append({
            "action": "REMOVE",
            "video_id": r["videoId"],
            "title": r.get("title"),
            "artist": r.get("artist"),
            "setVideoId": r.get("setVideoId"),
            "reason": "Track not present in desired locker-filtered playlist"
        })

    # Compare sequence: if ordering differs from desired, it's cleaner to sync in sequence
    reordered = (current_vids != desired_vids)

    # Detect position changes (MOVE actions)
    if reordered:
        for des_idx, des_track in enumerate(desired_tracks):
            d_vid = des_track.get("video_id")
            prev_positions = [i for i, t in enumerate(current_dest_tracks) if t.get("videoId") == d_vid]
            if prev_positions and prev_positions[0] != des_idx:
                actions.append({
                    "action": "MOVE",
                    "video_id": d_vid,
                    "title": des_track.get("title"),
                    "artist": des_track.get("artist"),
                    "from_position": prev_positions[0] + 1,
                    "to_position": des_idx + 1,
                    "reason": f"Position changed from {prev_positions[0] + 1} to {des_idx + 1}"
                })

    # Additions needed
    additions = []
    current_after_removals = [
        t.get("videoId") for t in current_dest_tracks
        if t.get("videoId") and not any(r["videoId"] == t.get("videoId") and r.get("setVideoId") == t.get("setVideoId") for r in removals)
    ]

    # For any missing occurrences:
    desired_remaining = list(desired_vids)
    for vid in current_after_removals:
        if vid in desired_remaining:
            desired_remaining.remove(vid)

    for vid in desired_remaining:
        additions.append(vid)
        # Find track metadata
        meta = next((d for d in desired_tracks if d.get("video_id") == vid), {})
        actions.append({
            "action": "ADD",
            "video_id": vid,
            "title": meta.get("title"),
            "artist": meta.get("artist"),
            "reason": "New locker track to append/insert"
        })

    return {
        "status": "CHANGES_REQUIRED",
        "actions": actions,
        "additions": additions,
        "removals": removals,
        "reordered": reordered
    }


def is_managed_by_ytmusic_sync(description: Optional[str]) -> bool:
    """Check if destination playlist description has the managed_by marker (Section 18)."""
    if not description:
        return False
    return "managed_by=ytmusic_sync" in description


class PlaylistReplicatorService:
    """Service to orchestrate playlist watching, locker matching, and reconciliation."""

    async def reconcile_playlist(self, replicated_id: int, dry_run: bool = False) -> dict:
        """
        Execute or preview reconciliation for a configured replicated playlist.
        Guarantees source playlist remains read-only.
        """
        config = await db.get_replicated_playlist(replicated_id)
        if not config:
            raise ValueError(f"Replicated playlist configuration {replicated_id} not found")

        logger.info(
            f"RECONCILIATION START: '{config.source_playlist_name}' -> '{config.destination_playlist_name}' "
            f"(dry_run={dry_run})"
        )

        # 1. Fetch source playlist tracks (Strictly READ-ONLY)
        source_raw = await ytm_client.get_playlist_raw(config.source_playlist_id)
        source_tracks = source_raw.get("tracks", [])
        source_title = source_raw.get("title") or config.source_playlist_name

        # Build SourcePlaylistSnapshot (Section 4 of plan)
        import hashlib
        import json
        snapshot_tracks = []
        for idx, t in enumerate(source_tracks):
            art_name = t.get("artist")
            if not art_name and t.get("artists") and isinstance(t.get("artists"), list) and len(t["artists"]) > 0:
                first_art = t["artists"][0]
                art_name = first_art.get("name") if isinstance(first_art, dict) else str(first_art)

            snapshot_tracks.append({
                "position": idx + 1,
                "video_id": str(t.get("videoId") or t.get("id") or ""),
                "title": t.get("title"),
                "artist": art_name,
                "setVideoId": t.get("setVideoId")
            })

        revision_content = json.dumps([t["video_id"] for t in snapshot_tracks])
        revision = hashlib.sha256(revision_content.encode()).hexdigest()[:12]

        if not dry_run:
            await db.save_replicated_playlist_snapshot(replicated_id, revision, snapshot_tracks)
            await db.update_replicated_playlist(replicated_id, last_source_revision=revision)

        # 2. Fetch all verified locker uploads
        uploads = await db.get_all_ytm_uploads()
        locker_lookup = build_locker_lookup(uploads)

        # 3. Filter source tracks: Locker-Only Guarantee & Order Preservation
        desired_tracks, excluded_tracks = filter_source_tracks_for_replica(source_tracks, locker_lookup)

        # 4. Fetch or verify destination playlist
        dest_id = config.destination_playlist_id
        current_dest_tracks = []
        if dest_id:
            try:
                dest_raw = await ytm_client.get_playlist_raw(dest_id)
                current_dest_tracks = dest_raw.get("tracks", [])
                dest_desc = dest_raw.get("description", "")
                if not is_managed_by_ytmusic_sync(dest_desc):
                    logger.warning(
                        f"Destination playlist {dest_id} does not have managed_by=ytmusic_sync marker in description"
                    )
            except Exception as e:
                logger.warning(f"Destination playlist {dest_id} could not be fetched: {e}")
                dest_id = None

        if not dest_id and not dry_run:
            dest_name = config.destination_playlist_name or f"{source_title} - Locker"
            ownership_desc = (
                f"Automated 1:1 Locker-Only Replica of '{source_title}'. "
                f"[managed_by=ytmusic_sync;replica_mode=locker_only;source_playlist_id={config.source_playlist_id}]"
            )
            logger.info(f"Creating destination playlist '{dest_name}' with ownership marker...")
            dest_id = await ytm_client.create_playlist(
                title=dest_name,
                description=ownership_desc
            )
            await db.update_replicated_playlist(replicated_id, destination_playlist_id=dest_id)
            config.destination_playlist_id = dest_id

        # 5. Calculate diff
        diff = calculate_reconciliation_diff(current_dest_tracks, desired_tracks)

        # 6. Apply changes if not dry_run
        if not dry_run and diff["status"] == "CHANGES_REQUIRED" and dest_id:
            # A. Execute Removals
            if diff["removals"]:
                removals_payload = [
                    {"videoId": r["videoId"], "setVideoId": r["setVideoId"]}
                    for r in diff["removals"]
                    if r.get("videoId") and r.get("setVideoId")
                ]
                if removals_payload:
                    await ytm_client.remove_playlist_items(dest_id, removals_payload)
                    for r in diff["removals"]:
                        await db.record_replicated_playlist_event(
                            replicated_playlist_id=replicated_id,
                            action="REMOVE",
                            source_video_id=r["videoId"],
                            reason="Not present in desired locker playlist"
                        )

            # B. If reordering was needed or destination had complete drift:
            # If current remaining does not match desired sequence, sync desired items
            if diff["reordered"]:
                # Re-fetch after removals only if destination originally had items
                if current_dest_tracks:
                    updated_dest = await ytm_client.get_playlist_raw(dest_id)
                    curr_remaining = updated_dest.get("tracks", [])
                else:
                    curr_remaining = []

                curr_remaining_vids = [t.get("videoId") for t in curr_remaining]
                desired_vids = [t["video_id"] for t in desired_tracks]

                if curr_remaining_vids != desired_vids:
                    # Clear and re-populate to ensure 100% exact order and duplicates
                    clear_items = [{"videoId": t["videoId"], "setVideoId": t["setVideoId"]} for t in curr_remaining if t.get("setVideoId")]
                    if clear_items:
                        await ytm_client.remove_playlist_items(dest_id, clear_items)
                    if desired_vids:
                        await ytm_client.add_playlist_items(dest_id, desired_vids, duplicates=True)
                        for t in desired_tracks:
                            await db.record_replicated_playlist_event(
                                replicated_playlist_id=replicated_id,
                                action="ADD",
                                source_video_id=t["video_id"],
                                locker_upload_id=t["locker_upload_id"],
                                reason="Replicated in exact source order"
                            )
            elif diff["additions"]:
                await ytm_client.add_playlist_items(dest_id, diff["additions"], duplicates=True)
                for vid in diff["additions"]:
                    await db.record_replicated_playlist_event(
                        replicated_playlist_id=replicated_id,
                        action="ADD",
                        source_video_id=vid,
                        reason="Added new verified locker track"
                    )

            # Log audit events for excluded tracks
            for ex in excluded_tracks:
                await db.record_replicated_playlist_event(
                    replicated_playlist_id=replicated_id,
                    action="EXCLUDE",
                    source_video_id=ex["video_id"],
                    reason=ex["reason"]
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            await db.update_replicated_playlist(
                replicated_id,
                last_sync_at=now_iso,
                last_sync_status="SUCCESS"
            )

        logger.info(
            f"RECONCILIATION FINISHED: source_tracks={len(source_tracks)}, "
            f"desired_tracks={len(desired_tracks)}, excluded={len(excluded_tracks)}, "
            f"actions={len(diff['actions'])} (dry_run={dry_run})"
        )

        return {
            "replicated_id": replicated_id,
            "source_playlist_name": source_title,
            "destination_playlist_name": config.destination_playlist_name,
            "destination_playlist_id": dest_id,
            "revision": revision,
            "source_tracks_count": len(source_tracks),
            "desired_tracks_count": len(desired_tracks),
            "excluded_count": len(excluded_tracks),
            "excluded_tracks": excluded_tracks,
            "status": diff["status"],
            "actions": diff["actions"],
            "dry_run": dry_run
        }


playlist_replicator = PlaylistReplicatorService()
