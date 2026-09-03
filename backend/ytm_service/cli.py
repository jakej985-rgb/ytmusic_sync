"""
CLI tool for ytmusic_sync:
Supports running dry-run and live playlist replication from command line
as specified in Section 29 of plan.md.

Usage:
    python -m ytm_service.cli playlist replicate [--dry-run] [--id ID]
"""

import sys
import asyncio
import argparse
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ytm_service.database import db
from ytm_service.playlist_replicator import playlist_replicator


def format_replica_diff(res: dict) -> str:
    lines = []
    lines.append(f"Source:\n{res.get('source_playlist_name', 'Unknown')}\n")

    actions = res.get("actions", [])
    adds = [a for a in actions if a.get("action") == "ADD"]
    removes = [a for a in actions if a.get("action") == "REMOVE"]
    moves = [a for a in actions if a.get("action") == "MOVE"]
    excludes = res.get("excluded_tracks", [])

    prefix = "Would " if res.get("dry_run") else ""

    if adds:
        lines.append(f"{prefix}ADD:")
        for a in adds:
            t = a.get("title") or "Unknown"
            art = a.get("artist") or "Unknown"
            lines.append(f"+ {art} - {t}")
        lines.append("")

    if removes:
        lines.append(f"{prefix}REMOVE:")
        for r in removes:
            t = r.get("title") or "Unknown"
            art = r.get("artist") or "Unknown"
            lines.append(f"- {art} - {t}")
        lines.append("")

    if moves:
        lines.append(f"{prefix}MOVE:")
        for m in moves:
            t = m.get("title") or "Unknown"
            art = m.get("artist") or "Unknown"
            frm = m.get("from_position", "?")
            to = m.get("to_position", "?")
            lines.append(f"{art} - {t}")
            lines.append(f"    position {frm} -> position {to}")
        lines.append("")

    if excludes:
        lines.append(f"{prefix}EXCLUDE:")
        for ex in excludes:
            t = ex.get("title") or "Unknown"
            art = ex.get("artist") or "Unknown"
            reason = ex.get("human_reason") or ex.get("reason") or "not uploaded"
            lines.append(f"{art} - {t}")
            lines.append(f"    {reason}")
        lines.append("")

    if not adds and not removes and not moves and not excludes:
        lines.append("[IN SYNC] No changes needed.\n")

    return "\n".join(lines)


async def run_replicate(args):
    await db.init_db()

    replicas = []
    if args.id:
        r = await db.get_replicated_playlist(args.id)
        if not r:
            print(f"Error: Replicated playlist #{args.id} not found.")
            sys.exit(1)
        replicas = [r]
    else:
        replicas = await db.get_replicated_playlists(enabled_only=True)
        if not replicas:
            print("No enabled replicated playlists found.")
            return

    for r in replicas:
        try:
            res = await playlist_replicator.reconcile_playlist(r.id, dry_run=args.dry_run)
            formatted = format_replica_diff(res)
            print(formatted)
        except Exception as e:
            print(f"Error reconciling playlist #{r.id} ('{r.source_playlist_name}'): {e}")


def main():
    parser = argparse.ArgumentParser(prog="ytmusic-sync", description="YTM Sync Management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # playlist subcommands
    playlist_parser = subparsers.add_parser("playlist", help="Playlist replication and management")
    playlist_sub = playlist_parser.add_subparsers(dest="subcommand", required=True)

    replicate_parser = playlist_sub.add_parser("replicate", help="Reconcile replicated playlists")
    replicate_parser.add_argument("--dry-run", action="store_true", help="Preview changes without modifying playlists")
    replicate_parser.add_argument("--id", type=int, default=None, help="Target specific replicated playlist ID")

    args = parser.parse_args()

    if args.command == "playlist" and args.subcommand == "replicate":
        asyncio.run(run_replicate(args))


if __name__ == "__main__":
    main()
