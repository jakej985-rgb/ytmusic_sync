import os
import shutil
import hashlib
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from .config import settings
from .database import db as default_db

logger = logging.getLogger("ytm_sync.recovery")

POSSIBLY_CORRUPTED_STATUS = "POSSIBLY CORRUPTED / REPLACED"
RESTORED_STATUS = "RESTORED"
VERIFIED_STATUS = "VERIFIED"


async def audit_and_flag_suspicious_files(database=None) -> List[Dict[str, Any]]:
    """
    Phase 14 — Audit and Recovery Procedure
    Identifies suspicious file replacements caused by bad downloader behavior.
    Flags affected records as 'POSSIBLY CORRUPTED / REPLACED' until verified.
    """
    target_db = database or default_db
    suspicious_records: List[Dict[str, Any]] = []

    # 1. Query all recorded file replacements from audit history
    replacements = await target_db.get_file_replacements(limit=1000)

    for rep in replacements:
        orig_path = rep["original_path"]
        orig_sha = rep["original_sha256"]
        rep_source = rep["replacement_source_id"]
        rep_time = rep["replacement_timestamp"]
        backup_p = rep.get("backup_path")

        local_file = Path(orig_path)
        current_sha = None
        if local_file.exists():
            h = hashlib.sha256()
            with open(local_file, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            current_sha = h.hexdigest()

        # Check backup existence
        backup_exists = False
        if backup_p and Path(backup_p).exists():
            backup_exists = True
        else:
            # Look in backups_dir for stem match
            cand_backups = list(settings.backups_dir.glob(f"{local_file.stem}*.bak"))
            if cand_backups:
                backup_p = str(cand_backups[0])
                backup_exists = True

        # Flag file in music_files table
        async with target_db.get_connection() as conn:
            await conn.execute(
                """
                UPDATE music_files
                SET verification_status = ?, verification_reason = ?
                WHERE path = ?
                """,
                (
                    POSSIBLY_CORRUPTED_STATUS,
                    f"Recorded in file replacement history from source {rep_source} at {rep_time}",
                    orig_path
                )
            )
            await conn.commit()

        suspicious_records.append({
            "path": orig_path,
            "verification_status": POSSIBLY_CORRUPTED_STATUS,
            "original_sha256": orig_sha,
            "current_sha256": current_sha,
            "replacement_timestamp": rep_time,
            "replacement_source_id": rep_source,
            "backup_available": backup_exists,
            "backup_path": backup_p if backup_exists else None,
            "reason": f"File was replaced on {rep_time} from source {rep_source}"
        })

    # 2. Check matches table for blocked / incident-flagged matches
    async with target_db.get_connection() as conn:
        async with conn.execute(
            """
            SELECT mf.path, mf.file_hash, m.sync_decision, m.decision_reason, m.ytm_upload_id
            FROM matches m
            JOIN music_files mf ON m.music_file_id = mf.id
            WHERE m.sync_decision = 'BLOCKED'
            """
        ) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                p = r["path"]
                if not any(item["path"] == p for item in suspicious_records):
                    local_f = Path(p)
                    c_sha = None
                    if local_f.exists():
                        h = hashlib.sha256()
                        with open(local_f, "rb") as f:
                            while chunk := f.read(65536):
                                h.update(chunk)
                        c_sha = h.hexdigest()

                    # Flag in database
                    await conn.execute(
                        """
                        UPDATE music_files
                        SET verification_status = ?, verification_reason = ?
                        WHERE path = ?
                        """,
                        (
                            POSSIBLY_CORRUPTED_STATUS,
                            f"Matched track flagged as BLOCKED: {r['decision_reason']}",
                            p
                        )
                    )

                    suspicious_records.append({
                        "path": p,
                        "verification_status": POSSIBLY_CORRUPTED_STATUS,
                        "original_sha256": r["file_hash"],
                        "current_sha256": c_sha,
                        "replacement_timestamp": None,
                        "replacement_source_id": r["ytm_upload_id"],
                        "backup_available": False,
                        "backup_path": None,
                        "reason": f"Match blocked: {r['decision_reason']}"
                    })
        await conn.commit()

    logger.warning(f"RECOVERY AUDIT COMPLETE: Flagged {len(suspicious_records)} file(s) as {POSSIBLY_CORRUPTED_STATUS}")
    return suspicious_records


async def restore_corrupted_file(file_path: str, database=None) -> Dict[str, Any]:
    """
    Restore a file flagged as POSSIBLY CORRUPTED / REPLACED from its pre-replacement backup.
    """
    target_db = database or default_db
    local_p = Path(file_path)

    # 1. Look up backup path in file_replacements
    async with target_db.get_connection() as conn:
        async with conn.execute(
            "SELECT backup_path, original_sha256 FROM file_replacements WHERE original_path = ? ORDER BY id DESC LIMIT 1",
            (str(local_p),)
        ) as cursor:
            row = await cursor.fetchone()

    backup_file: Optional[Path] = None
    orig_sha = None
    if row and row["backup_path"]:
        bp = Path(row["backup_path"])
        if bp.exists():
            backup_file = bp
            orig_sha = row["original_sha256"]

    if not backup_file:
        # Check backups dir
        cand_backups = list(settings.backups_dir.glob(f"{local_p.stem}*.bak"))
        if cand_backups:
            backup_file = cand_backups[0]

    if not backup_file or not backup_file.exists():
        raise FileNotFoundError(f"No valid backup found for {file_path}")

    # 2. Restore file
    local_p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_file, local_p)

    # 3. Update verification status to RESTORED
    async with target_db.get_connection() as conn:
        await conn.execute(
            """
            UPDATE music_files
            SET verification_status = ?, verification_reason = ?
            WHERE path = ?
            """,
            (
                RESTORED_STATUS,
                f"Restored from backup {backup_file.name}",
                str(local_p)
            )
        )
        await conn.commit()

    logger.info(f"Successfully restored {local_p} from {backup_file}")
    return {
        "status": "success",
        "path": str(local_p),
        "restored_from": str(backup_file),
        "verification_status": RESTORED_STATUS
    }
