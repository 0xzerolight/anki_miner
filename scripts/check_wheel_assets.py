"""Assert built wheel ships every bundled resource file on disk.

Fails CI if an asset type (e.g. new JSON theme, icon format, dictionary card-style
preset) lands on disk but is missing from the wheel. Also verifies the PyInstaller
spec references the same resource roots, so both build paths share one source of truth.

Run after ``python -m build --wheel``.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Every package resource tree that must ship intact in the wheel. The dictionary
# resources hold the universal glossary stylesheet (glossary.css) loaded at
# runtime via importlib.resources; a dropped/mistyped package-data glob would
# ship a broken install while CI stayed green, which this gate prevents.
RESOURCE_DIRS = [
    REPO_ROOT / "anki_miner" / "gui" / "resources",
    REPO_ROOT / "anki_miner" / "services" / "dictionary" / "resources",
    REPO_ROOT / "anki_miner" / "resources",
]
SPEC_FILE = REPO_ROOT / "anki_miner.spec"
DIST_DIR = REPO_ROOT / "dist"

# Assets that must exist on disk AND in the wheel, named one by one. The generic
# disk-vs-wheel comparison below cannot catch these: deleting a file from disk
# and from the wheel at the same time leaves the two sets equal and the gate
# green. The bundled Japanese fallback is exactly that kind of asset — nothing
# in the test suite fails when it disappears, and the symptom (tofu on a bare
# Linux install) only shows up on a machine with no CJK font at all. Its licence
# and provenance record are required for the same reason: shipping the font
# without the OFL beside it would breach the licence.
REQUIRED_ASSETS = [
    "anki_miner/gui/resources/fonts/NotoSansJP-Regular.otf",
    "anki_miner/gui/resources/fonts/OFL.txt",
    "anki_miner/gui/resources/fonts/PROVENANCE.md",
]

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def _wheel_prefixes() -> list[str]:
    return [f"{d.relative_to(REPO_ROOT).as_posix()}/" for d in RESOURCE_DIRS]


def fs_assets() -> set[str]:
    assets: set[str] = set()
    for resource_dir in RESOURCE_DIRS:
        for path in resource_dir.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            assets.add(path.relative_to(REPO_ROOT).as_posix())
    return assets


def wheel_assets(wheel_path: Path) -> set[str]:
    prefixes = tuple(_wheel_prefixes())
    with zipfile.ZipFile(wheel_path) as zf:
        return {name for name in zf.namelist() if name.startswith(prefixes)}


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
    expected = [
        '"anki_miner", "gui", "resources"',
        '"anki_miner", "services", "dictionary", "resources"',
        '"anki_miner", "resources"',
    ]
    for token in expected:
        if token not in text:
            sys.exit(
                f"error: {SPEC_FILE} does not reference {token!r}; "
                "PyInstaller spec and packaging config have drifted"
            )


def check_required_assets(on_disk: set[str], in_wheel: set[str], wheel_name: str) -> int:
    """Assert each named asset is present on disk and in the wheel."""
    failures = 0
    for asset in REQUIRED_ASSETS:
        if asset not in on_disk:
            print(f"error: required asset missing from the repository: {asset}")
            failures += 1
        elif asset not in in_wheel:
            print(f"error: required asset missing from {wheel_name}: {asset}")
            failures += 1
    return failures


def main() -> int:
    check_spec_references_resources()

    wheel = find_wheel()
    on_disk = fs_assets()
    in_wheel = wheel_assets(wheel)

    if check_required_assets(on_disk, in_wheel, wheel.name):
        return 1

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
