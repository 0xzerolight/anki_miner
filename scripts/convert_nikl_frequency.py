#!/usr/bin/env python3
"""Convert the NIKL frequency list into an app input.

Emits a CSV whose header DECLARES its direction, so the importer never falls
back to the statistical probe (services/frequency/mode_probe.py). ``count`` is
the occurrence-based marker recognised by
``services/frequency/csv_parse.py::_header_frequency_mode``; it must sit beside
``term`` or ``_is_frequency_header`` rejects the row and the declaration is lost.

Source: NIKL 현대 국어 사용 빈도 조사 2 (2005), 김한샘, 국립국어원 — KOGL Type 1
(출처표시), which permits derivatives and commercial use with attribution. The
member this reads is ``일반어휘통계.txt`` inside that survey's zip; its
spreadsheet siblings hold the same table and need no conversion step.

The learner-vocabulary list (한국어 학습용 어휘 목록) is NOT converted here. NIKL
publishes it only as a scanned PDF with no text layer, under KOGL Type 4
(non-commercial, NO derivatives), so nothing derived from it may ship — which
left the grade map with no input it could ever legally be given.

Every line marked "Ledger" below is a shape recorded by the Task 3.9 spike
against the real archive, not a guess: change it here if the source layout ever
changes.

Usage:
  python scripts/convert_nikl_frequency.py FREQ.tsv OUT.csv
"""

from __future__ import annotations

import argparse
import codecs
import csv
import re
import unicodedata
from pathlib import Path

#: Ledger A1 — decode order recorded by the spike. The primary member is UTF-16
#: LE with a BOM; its siblings are cp949 (euc-kr fails on the extended code
#: points). utf-8 leads so a hand-reconverted member is not silently
#: mojibake'd by cp949's permissiveness.
_ENCODINGS = ("utf-8-sig", "utf-16", "cp949")
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)

#: Ledger A3 — headwords carry a two-digit homograph index (하다01, 곡차02) that
#: the miner never produces. Exactly two digits: the eight one-digit tails in the
#: source (레짐8, 한가닥1) are corpus typos belonging to the term.
_HOMOGRAPH_INDEX_RE = re.compile(r"\d{2}$")

#: Ledger A2/A4 — tab-delimited columns 순위, 빈도, 어휘, 풀이, 품사. Only the
#: first three indices are stable: 풀이 carries tab-separated variant hanja, so
#: rows run to 9 cells and any rule keyed off the LAST cell reads the gloss.
_COUNT_COLUMN = 1
_TERM_COLUMN = 2


def _decode(data: bytes, path: Path) -> str:
    """Decode *path*'s bytes with the recorded ladder (ledger A1)."""
    for encoding in _ENCODINGS:
        if encoding == "utf-16" and data[:2] not in _UTF16_BOMS:
            # BOM-less bytes "decode" as UTF-16 LE garbage instead of raising,
            # so cp949 members must never be offered to it.
            continue
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"error: could not decode {path} as any of {_ENCODINGS}")


def _read_rows(path: Path) -> list[list[str]]:
    """Split *path* into cells (ledger A4: tab-delimited, CRLF line endings)."""
    text = _decode(path.read_bytes(), path)
    lines = text.splitlines()
    delimiter = "\t" if lines and "\t" in lines[0] else ","
    # QUOTE_NONE: the source is TSV with no quoting convention, so a stray " in
    # a 풀이 gloss must not swallow the rest of the row.
    return [row for row in csv.reader(lines, delimiter=delimiter, quoting=csv.QUOTE_NONE) if row]


def _cell_int(cell: str) -> int | None:
    cell = cell.strip().replace(",", "")
    return int(cell) if cell.isdigit() else None


def _normalize_term(cell: str) -> str:
    """Strip whitespace and the homograph index, then normalise (ledger A3)."""
    return _HOMOGRAPH_INDEX_RE.sub("", unicodedata.normalize("NFC", cell.strip())).strip()


def convert(freq_path: Path, out_csv: Path) -> int:
    """Write the declared-direction frequency CSV. Returns the row count."""
    totals: dict[str, int] = {}
    for row in _read_rows(freq_path):
        if len(row) <= _TERM_COLUMN:
            continue
        count = _cell_int(row[_COUNT_COLUMN])
        term = _normalize_term(row[_TERM_COLUMN])
        if count is None or not term:  # also skips the header row (빈도 is not a number)
            continue
        # Stripping the index collides ~6,200 terms across ~8,700 rows
        # (하다01 + 하다02 + …). A surface-form miner wants their sum; first-wins
        # would keep one row and silently discard the rest.
        totals[term] = totals.get(term, 0) + count

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["term", "count"])  # the direction declaration
        writer.writerows(totals.items())
    return len(totals)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("freq")
    parser.add_argument("out_csv")
    args = parser.parse_args()
    rows = convert(Path(args.freq), Path(args.out_csv))
    print(f"wrote {rows} frequency rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
