#!/usr/bin/env python3
"""Build the ``opensubtitles-vi-word`` frequency asset (spec C.4, R21; DECIDED 3).

No free word-level subtitle frequency list exists for Vietnamese (hermitdave's and
wordfreq's are syllable lists), so this re-segments a subtitle corpus with the app's
own tokenizer. Input: OPUS OpenSubtitles v2024 Vietnamese monolingual text, one cue
per line (``https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/mono/vi.txt.gz``,
918,883,464 B; ODC-BY 1.0; P. Lison and J. Tiedemann, 2016, OpenSubtitles2016,
LREC; subtitles from opensubtitles.org). Each line goes through ``vi_normalize`` and
the vi tagger, so a key is exactly the front mining looks up: NFC, old-style tone
placement, lower case, multi-syllable words with their spaces. Dropped: ``Np``
(names), ``CH`` (punctuation), tokens without a letter and tokens with a digit.
Stopwords are kept: they are real words and the list ranks words, not fronts.

Output: a Yomitan frequency dictionary (``frequencyMode: rank-based``), ranks
1..cap by count, ties broken by the term so the same corpus gives the same bytes.

Usage (the published build, 8 processes under nice):
  nice -n 19 python scripts/build_vi_frequency.py vi.txt.gz opensubtitles-vi-word-2026.09.19.zip \\
      --revision 2026.09.19 --processes 8
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import sys
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator
from itertools import islice
from pathlib import Path
from typing import Any

DEFAULT_CAP = 50_000
BANK_SIZE = 10_000
#: Every member gets this timestamp: identical input, identical zip bytes.
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
_DROPPED_POS = frozenset({"Np", "CH"})
ATTRIBUTION = (
    "OPUS OpenSubtitles v2024 (vi), ODC-BY 1.0: P. Lison and J. Tiedemann (2016), OpenSubtitles2016, LREC; "
    "subtitles from opensubtitles.org. Segmented with underthesea (Apache-2.0) by Anki Miner."
)

_TAGGER: Any = None


def frequency_key(token: Any) -> str | None:
    """The folded front a token counts under, or None for a name, punctuation or a digit-bearing token."""
    if token.feature.pos1 in _DROPPED_POS:
        return None
    key = str(token.feature.lemma or "")
    if not any(char.isalpha() for char in key) or any(char.isdigit() for char in key):
        return None
    return key


def count_lines(tagger: Any, lines: Iterable[str]) -> Counter[str]:
    from anki_miner.languages.vi.script import vi_normalize

    counts: Counter[str] = Counter()
    for raw in lines:
        line = vi_normalize(raw).strip()
        if not line:
            continue
        for token in tagger(line):
            key = frequency_key(token)
            if key is not None:
                counts[key] += 1
    return counts


def _init_worker() -> None:
    global _TAGGER
    from anki_miner.languages.vi.tokenizer import build_tagger

    _TAGGER = build_tagger()


def _count_chunk(lines: list[str]) -> Counter[str]:
    return count_lines(_TAGGER, lines)


def _chunks(lines: Iterable[str], size: int) -> Iterator[list[str]]:
    iterator = iter(lines)
    while chunk := list(islice(iterator, size)):
        yield chunk


def count_corpus(lines: Iterable[str], processes: int, chunk: int = 5000) -> Counter[str]:
    """Count every line; ``processes`` > 1 fans chunks out to a pool (run as a script, not imported)."""
    if processes <= 1:
        _init_worker()
        return count_lines(_TAGGER, lines)
    total: Counter[str] = Counter()
    with multiprocessing.Pool(processes, initializer=_init_worker) as pool:
        for done, counts in enumerate(pool.imap_unordered(_count_chunk, _chunks(lines, chunk)), start=1):
            total.update(counts)
            if done % 200 == 0:
                print(f"{done * chunk:,} lines", file=sys.stderr, flush=True)
    return total


def rank_rows(counts: Counter[str], cap: int = DEFAULT_CAP) -> list[tuple[str, int]]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:cap]
    return [(term, rank) for rank, (term, _count) in enumerate(ordered, start=1)]


def _write_member(zf: zipfile.ZipFile, name: str, payload: object) -> None:
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def write_yomitan(rows: list[tuple[str, int]], out: Path, *, revision: str, lines: int) -> None:
    index = {
        "title": "OpenSubtitles 2024 word frequency (Vietnamese)",
        "revision": revision,
        "format": 3,
        "frequencyMode": "rank-based",
        "sourceLanguage": "vi",
        "author": "Anki Miner (scripts/build_vi_frequency.py)",
        "attribution": ATTRIBUTION,
        "description": (
            f"{lines:,} lines of OPUS OpenSubtitles v2024 vi re-segmented into words; "
            f"{len(rows):,} ranks; names, punctuation and numbers dropped."
        ),
    }
    with zipfile.ZipFile(out, "w") as zf:
        _write_member(zf, "index.json", index)
        for number, start in enumerate(range(0, len(rows), BANK_SIZE), start=1):
            bank = [[term, "freq", rank] for term, rank in rows[start : start + BANK_SIZE]]
            _write_member(zf, f"term_meta_bank_{number}.json", bank)


def _read_lines(path: Path, limit: int | None) -> Iterator[str]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
        stripped = (line.rstrip("\n") for line in handle)
        yield from (islice(stripped, limit) if limit else stripped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus", type=Path, help="OpenSubtitles v2024 vi.txt.gz")
    parser.add_argument("out", type=Path, help="output .zip")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="read only the first N lines")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP)
    args = parser.parse_args(argv)
    lines = 0

    def counted() -> Iterator[str]:
        nonlocal lines
        for line in _read_lines(args.corpus, args.limit):
            lines += 1
            yield line

    counts = count_corpus(counted(), args.processes)
    write_yomitan(rank_rows(counts, args.cap), args.out, revision=args.revision, lines=lines)
    print(f"{lines:,} lines, {len(counts):,} types -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
