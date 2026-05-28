"""Build a minimal Yomitan-format pitch-accent zip for tests."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from typing import Any


def build_yomitan_pitch_zip(
    zip_path: Path,
    *,
    title: str = "Test Pitch",
    revision: str = "v1",
    format_version: int | None = 3,
    meta_banks: list[list[Any]] | None = None,
) -> Path:
    """Create a Yomitan pitch zip at ``zip_path``. Returns ``zip_path``.

    Each item in ``meta_banks`` is one bank file. Entries are ``[term, mode, data]``
    triples passed through verbatim — callers control mode and data shape.
    Pass ``format_version=None`` to omit the ``format`` key entirely (used to
    test the importer's missing-format rejection path).
    """
    if meta_banks is None:
        meta_banks = [_default_pitch_entries()]

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    index: dict[str, Any] = {
        "title": title,
        "revision": revision,
    }
    if format_version is not None:
        index["format"] = format_version

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.json", json.dumps(index))
        for i, bank in enumerate(meta_banks, 1):
            zf.writestr(f"term_meta_bank_{i}.json", json.dumps(bank))
    return zip_path


def _default_pitch_entries() -> list[Any]:
    """Default fixture content: kanji+reading, multi-position, kana-only, display-only-skipped."""
    return [
        # kanji+reading with single drop position
        ["猫", "pitch", {"reading": "ねこ", "pitches": [{"position": 1}]}],
        # multi-position entry (homograph with two accents)
        ["箸", "pitch", {"reading": "はし", "pitches": [{"position": 1}, {"position": 2}]}],
        # kana-only (term == reading)
        ["ありがとう", "pitch", {"reading": "ありがとう", "pitches": [{"position": 2}]}],
        # display-only (empty pitches array) — should be skipped
        ["犬", "pitch", {"reading": "いぬ", "pitches": []}],
    ]


def main(argv: list[str]) -> int:
    """Emit a default fixture zip at ``argv[1]``."""
    if len(argv) < 2:
        print("usage: build_yomitan_pitch_fixture.py <out.zip>", file=sys.stderr)
        return 2
    out = Path(argv[1])
    build_yomitan_pitch_zip(out)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
