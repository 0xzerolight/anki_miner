#!/usr/bin/env python3
"""Build the ``opensubtitles-zh-word`` frequency asset.

Every catalogued mining language but ko ships a frequency row; zh shipped none,
because the ported word lists (SUBTLEX-CH, BCC) are cut by somebody else's
tokenizer and have no stable per-file download URL. th, vi and yue each answered
that by building their own list, and this does the same for Chinese: a key is
exactly what jieba cuts, folded the way a card front is folded.

Input: OPUS OpenSubtitles v2024 Chinese monolingual text, one cue per line
(``https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/mono/zh_CN.txt.gz``,
348,802,784 B, and the ``zh_TW.txt.gz`` beside it, 32,369,324 B; ODC-BY 1.0;
P. Lison and J. Tiedemann, 2016, OpenSubtitles2016, LREC; subtitles from
opensubtitles.org). Both files are counted into one list: ``script_key`` folds
這裏 onto 这里, so the Taiwan file mostly reinforces ranks the mainland file
already carries, and the vocabulary that is genuinely Taiwanese (計程車, 捷運,
便當) would otherwise be unranked — which, with "skip unranked" on, means never
mined.

Keys: each line is NFC-normalised and cut by the app's own tagger, and the key
is ``script_key(surface)`` — the front a simplified run mines, and one a
traditional front reaches through the ``term_variants`` ladder the frequency
provider already asks for on a miss.

Token filter: the token must contain a Han ideograph, which is the gate
``ZhScriptSupport.contains_target_script`` applies when deciding a token is
Chinese at all. Nothing else is dropped. Function words stay, because the list
ranks words rather than card fronts; the name classes stay too, which is where
this parts company with ``build_vi_frequency.py`` and its dropped ``Np``.
underthesea's ``Np`` is a proper-name class, jieba's ``nr``/``ns``/``nrt`` are
not: over 80,000 corpus lines, dropping them takes 大王, 王后, 东西, 谢谢, 小姐,
明白, 哥哥, 城市, 多谢 and 拜托 with them — 380 of the top 3,000 keys, the same
measurement that shaped ``languages/zh/pos.py``. A real name that survives costs
one rank slot and nothing else: no offline dictionary lists it, so it never
reaches a card.

OpenCC is mandatory here, unlike at runtime where its absence only costs the
script variants: without it ``to_simplified`` returns its input, so traditional
lines are cut by a simplified-only dictionary and no key folds.

Output: a Yomitan frequency dictionary (``frequencyMode: rank-based``), ranks
1..cap by count, ties broken by the term so the same corpus gives the same
bytes. The cap is vi's 50,000 — the length of the hermitdave ``_50k`` lists
every other catalogued language ships, and far past any usable
``max_frequency_rank``.

Usage (the published build, 4 processes under nice):
  nice -n 19 python scripts/build_zh_frequency.py zh_CN.txt.gz zh_TW.txt.gz \\
      opensubtitles-zh-word-2026.09.20.zip --revision 2026.09.20 --processes 4
"""

from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing
import sys
import zipfile
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from itertools import islice
from pathlib import Path
from typing import Any

from anki_miner.languages.zh.support import ZhScriptSupport
from anki_miner.languages.zh.variants import normalize_zh, script_key

DEFAULT_CAP = 50_000
BANK_SIZE = 10_000
#: Every member gets this timestamp: identical input, identical zip bytes.
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
ATTRIBUTION = (
    "OPUS OpenSubtitles v2024 (zh_CN + zh_TW), ODC-BY 1.0: P. Lison and J. Tiedemann (2016), "
    "OpenSubtitles2016, LREC; subtitles from opensubtitles.org. Segmented with jieba (MIT) by Anki Miner."
)

_ZH_SCRIPT = ZhScriptSupport()
_TAGGER: Any = None


def require_opencc() -> None:
    """Stop the build when OpenCC is missing: its absence corrupts keys silently."""
    try:
        import opencc  # noqa: F401
    except ImportError as exc:
        raise SystemExit("OpenCC is required: without it traditional lines mis-cut and no key folds.") from exc


def frequency_key(surface: str) -> str | None:
    """The folded front a token counts under, or None when it carries no Han ideograph."""
    if not _ZH_SCRIPT.contains_target_script(surface):
        return None
    return script_key(surface)


def count_lines(tagger: Any, lines: Iterable[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for raw in lines:
        line = normalize_zh(raw).strip()
        if not line:
            continue
        for token in tagger(line):
            key = frequency_key(token.surface)
            if key is not None:
                counts[key] += 1
    return counts


def _init_worker() -> None:
    global _TAGGER
    from anki_miner.languages.zh.tokenizer import build_tagger

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
        "title": "OpenSubtitles 2024 word frequency (Chinese)",
        "revision": revision,
        "format": 3,
        "frequencyMode": "rank-based",
        "sourceLanguage": "zh",
        "author": "Anki Miner (scripts/build_zh_frequency.py)",
        "attribution": ATTRIBUTION,
        "description": (
            f"{lines:,} lines of OPUS OpenSubtitles v2024 zh_CN and zh_TW re-segmented with jieba; "
            f"{len(rows):,} ranks, keyed on the simplified fold."
        ),
    }
    with zipfile.ZipFile(out, "w") as zf:
        _write_member(zf, "index.json", index)
        for number, start in enumerate(range(0, len(rows), BANK_SIZE), start=1):
            bank = [[term, "freq", rank] for term, rank in rows[start : start + BANK_SIZE]]
            _write_member(zf, f"term_meta_bank_{number}.json", bank)


def _read_lines(paths: Sequence[Path], limit: int | None) -> Iterator[str]:
    """Every line of every corpus in turn; ``limit`` caps each file, not the run."""
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            stripped = (line.rstrip("\n") for line in handle)
            yield from (islice(stripped, limit) if limit else stripped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("corpus", type=Path, nargs="+", help="OpenSubtitles v2024 zh_CN.txt.gz and zh_TW.txt.gz")
    parser.add_argument("out", type=Path, help="output .zip")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None, help="read only the first N lines of each corpus")
    parser.add_argument("--cap", type=int, default=DEFAULT_CAP)
    args = parser.parse_args(argv)
    require_opencc()
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
