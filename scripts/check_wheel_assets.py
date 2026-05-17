"""Assert built wheel ships every GUI resource file on disk.

Fails CI if an asset type (e.g. new JSON theme, icon format) lands on disk but
is missing from the wheel. Also verifies the PyInstaller spec references the
same resource root, so both build paths share one source of truth.

Run after ``python -m build --wheel``.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESOURCE_DIR = REPO_ROOT / "anki_miner" / "gui" / "resources"
SPEC_FILE = REPO_ROOT / "anki_miner.spec"
DIST_DIR = REPO_ROOT / "dist"

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def fs_assets() -> set[str]:
    assets: set[str] = set()
    for path in RESOURCE_DIR.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDE_SUFFIXES:
            continue
        assets.add(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
    return assets


def wheel_assets(wheel_path: Path) -> set[str]:
    with zipfile.ZipFile(wheel_path) as zf:
        return {name for name in zf.namelist() if name.startswith("anki_miner/gui/resources/")}


def find_wheel() -> Path:
    if not DIST_DIR.exists():
        sys.exit(f"error: {DIST_DIR} does not exist; run 'python -m build --wheel' first")
    wheels = sorted(DIST_DIR.glob("anki_miner-*.whl"))
    if not wheels:
        sys.exit(f"error: no wheel found in {DIST_DIR}")
    return wheels[-1]


def check_spec_references_resources() -> None:
    if not SPEC_FILE.exists():
        sys.exit(f"error: {SPEC_FILE} missing")
    text = SPEC_FILE.read_text(encoding="utf-8")
    expected = '"anki_miner", "gui", "resources"'
    if expected not in text:
        sys.exit(
            f"error: {SPEC_FILE} does not reference {expected!r}; " "PyInstaller spec and MANIFEST.in have drifted"
        )


def main() -> int:
    check_spec_references_resources()

    wheel = find_wheel()
    on_disk = fs_assets()
    in_wheel = wheel_assets(wheel)

    missing = on_disk - in_wheel
    if missing:
        print(f"error: {len(missing)} asset(s) on disk but missing from {wheel.name}:")
        for entry in sorted(missing):
            print(f"  - {entry}")
        return 1

    stray = in_wheel - on_disk
    if stray:
        print(f"warning: {len(stray)} asset(s) in wheel not on disk:")
        for entry in sorted(stray):
            print(f"  - {entry}")

    print(f"ok: {len(on_disk)} resources shipped in {wheel.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
