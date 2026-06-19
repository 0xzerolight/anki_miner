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
SRC_ROOT = REPO_ROOT / "anki_miner"
TS_DIR = SRC_ROOT / "gui" / "resources" / "translations"
EN_TS = TS_DIR / "anki_miner_en.ts"
EN_QM = TS_DIR / "anki_miner_en.qm"


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


def extract(ts_path: Path = EN_TS) -> None:
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pylupdate6", "--no-obsolete", "--ts", str(ts_path), *_python_sources()], check=True)
    _strip_locations(ts_path)


def compile_ts() -> None:
    subprocess.run([_lrelease(), str(EN_TS), "-qm", str(EN_QM)], check=True)


def check() -> int:
    with tempfile.TemporaryDirectory() as td:
        tmp_ts = Path(td) / "anki_miner_en.ts"
        extract(tmp_ts)
        if tmp_ts.read_text(encoding="utf-8") != EN_TS.read_text(encoding="utf-8"):
            print(
                "error: anki_miner_en.ts is stale. Run: python scripts/i18n.py extract && python scripts/i18n.py compile"
            )
            return 1
    return 0


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
