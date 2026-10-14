#!/usr/bin/env python3
"""Translation catalog tooling for anki_miner (Discussion #76).

Subcommands:
  extract  — regenerate anki_miner/gui/resources/translations/anki_miner_en.ts
             from all anki_miner/**/*.py sources via pylupdate6, then strip
             <location> lines so the committed catalog is deterministic
             (pylupdate6 has no -locations option, and raw line numbers churn
             on every unrelated edit, which would make `check` false-positive).
  compile  — compile each *.ts to *.qm via pyside6-lrelease.
  check    — regenerate to a temp file and diff against the committed .ts;
             exit 1 on drift. Used by CI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# This checkout's package, not wherever the shared editable install points (a
# worktree run would otherwise read the main checkout's language registry).
sys.path.insert(0, str(REPO_ROOT))

from anki_miner.gui.i18n import available_languages  # noqa: E402

SRC_ROOT = REPO_ROOT / "anki_miner"
TS_DIR = SRC_ROOT / "gui" / "resources" / "translations"

# Every shipped catalog as (source .ts, compiled .qm), from the UI language
# registry, so a registered language is always synced and compiled. "en" (the
# registry's first key) is the source language (all entries unfinished); the
# rest carry translations. pylupdate6 updates-or-creates a .ts, preserving
# existing translations on merge, so the same primitive regenerates en and
# syncs translated catalogs without data loss.
_CATALOGS: list[tuple[Path, Path]] = [
    (TS_DIR / f"anki_miner_{code}.ts", TS_DIR / f"anki_miner_{code}.qm") for code in available_languages()
]


def _python_sources() -> list[str]:
    return [str(p) for p in sorted(SRC_ROOT.rglob("*.py")) if "__pycache__" not in p.parts]


def _strip_locations(ts_path: Path) -> None:
    """Drop `<location .../>` lines so the committed .ts is line-number-stable."""
    kept = [ln for ln in ts_path.read_text(encoding="utf-8").splitlines() if "<location " not in ln]
    ts_path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def _lrelease() -> str:
    exe = shutil.which("pyside6-lrelease") or shutil.which("lrelease")
    if exe is None:
        sys.exit("error: neither pyside6-lrelease nor lrelease found (pip install 'pyside6-essentials')")
    return exe


def _update_ts(ts_path: Path) -> None:
    """Create-or-merge ``ts_path`` from sources, then strip locations.

    pylupdate6 merges into an existing .ts (keeps finished translations, adds new
    sources as ``type="unfinished"``, drops removed ones via ``--no-obsolete``),
    so a translated catalog is synced in place without losing work.
    """
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pylupdate6", "--no-obsolete", "--ts", str(ts_path), *_python_sources()], check=True)
    _strip_locations(ts_path)


def extract() -> None:
    for ts_path, _ in _CATALOGS:
        _update_ts(ts_path)


def compile_ts() -> None:
    for ts_path, qm_path in _CATALOGS:
        subprocess.run([_lrelease(), str(ts_path), "-qm", str(qm_path)], check=True)


def check() -> int:
    """CI guard: every catalog must be in sync with the sources.

    For each catalog the committed .ts is seeded into a temp dir and re-synced;
    any diff means it is stale. A newly-added English string surfaces here as an
    extra ``unfinished`` entry the translated .ts lacks — flagging JA drift (the
    string falls back to English at runtime until translated).
    """
    rc = 0
    with tempfile.TemporaryDirectory() as td:
        for ts_path, _ in _CATALOGS:
            tmp_ts = Path(td) / ts_path.name
            if ts_path.exists():
                shutil.copy2(ts_path, tmp_ts)  # seed so merge preserves translations
            _update_ts(tmp_ts)
            committed = ts_path.read_text(encoding="utf-8") if ts_path.exists() else ""
            if tmp_ts.read_text(encoding="utf-8") != committed:
                print(
                    f"error: {ts_path.name} is stale. "
                    "Run: python scripts/i18n.py extract && python scripts/i18n.py compile"
                )
                rc = 1
    return rc


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "extract":
        extract()
    elif cmd == "compile":
        compile_ts()
    elif cmd == "check":
        return check()
    else:
        print("usage: python scripts/i18n.py extract|compile|check")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
