#!/usr/bin/env python3
"""Convert PyThaiNLP's bundled Thai frequency lists into Yomitan dictionaries.

The Thai National Corpus list and the textbook corpus list ship inside the
pythainlp wheel as two-column ``term<TAB>count`` files, not as anything the app
can import, which is why Thai is one of the two languages that keeps a converter
(R21). Both are CC0; the attribution line names PyThaiNLP and the corpus.

The output declares ``frequencyMode: occurrence-based`` and carries the raw
counts. ``services/frequency/source_importer.py`` converts occurrence counts to
ranks on import, exactly as it does for every hermitdave list, so publishing
counts keeps the asset equal to the corpus rather than to one tie-breaking
order. The literal has to match ``mode_probe.OCCURRENCE_BASED`` exactly: the
importer takes the declared mode from the zip and never validates it, so a
near-miss like "occurrence" would store a raw count as a rank, in an asset that
is published once and never edited.

Usage:
  python scripts/convert_tnc_thai_frequency.py tnc OUT.zip
  python scripts/convert_tnc_thai_frequency.py ttc OUT.zip
  python scripts/convert_tnc_thai_frequency.py tnc OUT.zip --source /path/to/tnc_freq.txt
"""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

#: The importer's own constant, duplicated here rather than imported: this
#: script runs against a checkout without the app installed. Any drift is caught
#: by tests/unit/test_convert_tnc_thai_frequency.py, which compares the two.
OCCURRENCE_BASED = "occurrence-based"

_CORPORA = {
    "tnc": (
        "corpus/tnc_freq.txt",
        "TNC Thai frequency",
        "Thai National Corpus word list, bundled with PyThaiNLP (CC0).",
    ),
    "ttc": (
        "corpus/ttc_freq.txt",
        "TTC Thai textbook frequency",
        "Thai Textbook Corpus word list, bundled with PyThaiNLP (CC0).",
    ),
}


def parse_rows(text: str) -> list[tuple[str, int]]:
    """``term<TAB>count`` lines, malformed ones dropped."""
    rows: list[tuple[str, int]] = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0].strip():
            continue
        try:
            rows.append((parts[0].strip(), int(parts[1].strip())))
        except ValueError:
            continue
    return rows


def build_zip(rows: list[tuple[str, int]], *, title: str, revision: str, attribution: str = "") -> bytes:
    """A Yomitan ``frequency`` dictionary as zip bytes."""
    index = {
        "format": 3,
        "revision": revision,
        "title": title,
        "sourceLanguage": "th",
        "frequencyMode": OCCURRENCE_BASED,
        "attribution": attribution or "PyThaiNLP (CC0)",
    }
    bank = [[term, "freq", count] for term, count in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.json", json.dumps(index, ensure_ascii=False, indent=2))
        archive.writestr("term_meta_bank_1.json", json.dumps(bank, ensure_ascii=False))
    return buffer.getvalue()


def _read_corpus(member: str, source: Path | None) -> str:
    if source is not None:
        return source.read_text(encoding="utf-8")
    import pythainlp

    return (Path(pythainlp.__file__).parent / member).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", choices=sorted(_CORPORA))
    parser.add_argument("out", type=Path)
    parser.add_argument("--source", type=Path, default=None, help="read this file instead of the installed wheel")
    args = parser.parse_args()
    member, title, attribution = _CORPORA[args.corpus]
    rows = parse_rows(_read_corpus(member, args.source))
    revision = datetime.now(UTC).strftime("%Y-%m-%d")
    args.out.write_bytes(build_zip(rows, title=title, revision=revision, attribution=attribution))
    print(f"{args.out}: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
