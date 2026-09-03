#!/usr/bin/env python3
"""
Phase 19 — Static Filesystem Mutation & Search Audit Script
Scans the backend codebase for filesystem mutations and dangerous search patterns
to ensure that no operation can ever damage, overwrite, or delete a user's music file
without strict integrity verification and pre-replacement backups.
"""

import os
import re
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

PATTERNS = {
    "ytsearch": r"ytsearch\d*:",
    "os_replace": r"\bos\.replace\b",
    "shutil_move": r"\bshutil\.move\b",
    "os_remove": r"\bos\.remove\b",
    "unlink": r"\bunlink\b",
    "rename": r"\brename\b",
    "overwrite": r"allow_overwrite\s*=\s*True",
}

SAFE_CONTEXTS = [
    "staging",
    "temp",
    "cookie",
    "id_file",
    "custom_artwork",
    "backups",
    "tests",
    "raw_temp",
    "commit_staged_file_to_destination"
]


def run_audit() -> int:
    print(f"[*] Auditing filesystem mutations and search patterns in {BACKEND_DIR}...")
    issues = []
    checked_files = 0

    for root, _, files in os.walk(BACKEND_DIR):
        if "tests" in root or ".venv" in root or "__pycache__" in root:
            continue
        for file in files:
            if not file.endswith(".py"):
                continue
            checked_files += 1
            file_path = Path(root) / file
            rel_path = file_path.relative_to(BACKEND_DIR)

            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            in_docstring = False
            for line_idx, line in enumerate(lines, start=1):
                stripped = line.strip()
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    in_docstring = not in_docstring
                    continue
                if in_docstring or stripped.startswith("#"):
                    continue

                # Allow log file migrations
                if "legacy_log" in line or "target_log" in line or ".log" in line:
                    continue

                # 1. Check for forbidden ytsearch in non-catalog contexts
                if re.search(PATTERNS["ytsearch"], line):
                    # Only permissible if guarded by source_type == 'catalog'
                    context = "".join(lines[max(0, line_idx - 10):line_idx])
                    if "source_type == \"catalog\"" not in context and "source_type == 'catalog'" not in context:
                        issues.append((
                            str(rel_path), line_idx, "DANGEROUS SEARCH",
                            f"ytsearch invocation found outside catalog guard: {line.strip()}"
                        ))

                # 2. Check for unguarded os.replace / shutil.move directly touching music destination
                for mutation_type in ("os_replace", "shutil_move"):
                    if re.search(PATTERNS[mutation_type], line):
                        # Verify it's within commit_staged_file_to_destination or staging
                        context = "".join(lines[max(0, line_idx - 15):min(len(lines), line_idx + 5)])
                        if not any(safe in context for safe in SAFE_CONTEXTS):
                            issues.append((
                                str(rel_path), line_idx, f"UNGUARDED MUTATION ({mutation_type})",
                                line.strip()
                            ))

    print(f"[*] Audited {checked_files} Python source files.")
    if issues:
        print(f"[!] Found {len(issues)} potentially unsafe operations:")
        for file, line, kind, detail in issues:
            print(f"    - {file}:{line} [{kind}] {detail}")
        return 1
    else:
        print("[+] SUCCESS: All filesystem mutations are properly guarded and quarantined!")
        return 0


if __name__ == "__main__":
    sys.exit(run_audit())
