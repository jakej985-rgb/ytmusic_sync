#!/usr/bin/env python3
"""
Version bump utility for YTM Sync.
Updates version numbers consistently across backend, frontend, and package metadata.
"""

import sys
import re
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

INIT_PY = ROOT_DIR / "backend" / "ytm_service" / "__init__.py"
MAIN_PY = ROOT_DIR / "backend" / "ytm_service" / "main.py"
PUBSPEC_YAML = ROOT_DIR / "app" / "pubspec.yaml"
VERSION_JSON = ROOT_DIR / "backend" / "web_dist" / "version.json"


def get_current_version() -> str:
    content = INIT_PY.read_text()
    match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
    if not match:
        raise ValueError(f"Could not find __version__ in {INIT_PY}")
    return match.group(1)


def bump_version(new_version: str):
    # Normalize version: strip leading 'v' if present
    clean_version = new_version.lstrip("v").strip()
    if not clean_version:
        raise ValueError("Invalid version string.")

    print(f"Bumping version to: {clean_version}")

    # 1. Update backend/ytm_service/__init__.py
    init_content = INIT_PY.read_text()
    init_updated = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{clean_version}"',
        init_content
    )
    INIT_PY.write_text(init_updated)
    print(f"  ✓ Updated {INIT_PY.relative_to(ROOT_DIR)}")

    # 2. Update backend/ytm_service/main.py
    main_content = MAIN_PY.read_text()
    main_updated = re.sub(
        r'version\s*=\s*["\'][^"\']+["\']',
        f'version="{clean_version}"',
        main_content
    )
    MAIN_PY.write_text(main_updated)
    print(f"  ✓ Updated {MAIN_PY.relative_to(ROOT_DIR)}")

    # 3. Update app/pubspec.yaml (version: X.Y.Z-beta+build)
    pubspec_content = PUBSPEC_YAML.read_text()
    # Extract current build number or default to 1
    build_match = re.search(r'version:\s*[^\+\n]+\+(\d+)', pubspec_content)
    build_num = int(build_match.group(1)) + 1 if build_match else 1
    pubspec_updated = re.sub(
        r'version:\s*[^\n]+',
        f'version: {clean_version}+{build_num}',
        pubspec_content
    )
    PUBSPEC_YAML.write_text(pubspec_updated)
    print(f"  ✓ Updated {PUBSPEC_YAML.relative_to(ROOT_DIR)} (build {build_num})")

    # 4. Update backend/web_dist/version.json
    if VERSION_JSON.exists():
        try:
            v_data = json.loads(VERSION_JSON.read_text())
            v_data["version"] = clean_version
            v_data["build_number"] = str(build_num)
            VERSION_JSON.write_text(json.dumps(v_data, indent=2))
            print(f"  ✓ Updated {VERSION_JSON.relative_to(ROOT_DIR)}")
        except Exception as e:
            print(f"  ! Warning: could not parse {VERSION_JSON}: {e}")

    print(f"\nSuccessfully bumped project version to {clean_version} (Tag: v{clean_version})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        current = get_current_version()
        print(f"Current version: {current}")
        print("Usage: python3 scripts/bump_version.py <new_version>")
        print("Example: python3 scripts/bump_version.py 0.0.2-beta")
        sys.exit(1)

    bump_version(sys.argv[1])
