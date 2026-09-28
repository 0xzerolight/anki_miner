#!/usr/bin/env python3
"""Build yue's spoken-jyutping override table from HKCanCor.

``pycantonese.characters_to_jyutping`` reads many common one-character words,
and a few compounds, with a literary or rare reading: 聽 ting3, 返 faan2,
行 hong6, 平 ping4, 嗰個 go3 go3. HKCanCor -- the Hong Kong Cantonese Corpus
(Luke and Wong 2015, CC BY 4.0), hand-transcribed conversation that ships inside
the same wheel -- records what speakers said: 聽 teng1 176 of 176 times, 返 faan1
347 of 347. ``anki_miner.languages.yue.reading.word_jyutping`` consults the
table this script writes before it asks the engine, so the card reading and the
definition-ranking boost both carry the spoken reading.

A word gets a row when:

* every character is a CJK ideograph and HKCanCor transcribes it at least
  ``MIN_TOKENS`` times;
* one reading holds at least ``MIN_SHARE`` of those tokens;
* that reading differs from the engine's;
* its dominant HKCanCor tag is not a sentence-final particle or an interjection
  (``PARTICLE_TAGS``). HKCanCor spells those with its own characters (喇 for
  laa1, where current writing has 啦 laa1 and 喇 laa3), so the reading records
  the transcriber's convention, not the character's;
* the hand review below did not reject it (``REVIEWED_OUT``).

The table is this script's output, byte for byte: change the script and rerun
it, never edit the table. ``tests/unit/languages/test_yue_jyutping_overrides.py``
rebuilds it and compares.

Usage:
  python scripts/build_yue_jyutping_overrides.py
  python scripts/build_yue_jyutping_overrides.py --out PATH
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from anki_miner.utils.ja_normalize import is_cjk_ideograph

DEFAULT_OUT = (
    Path(__file__).resolve().parents[1] / "anki_miner" / "languages" / "yue" / "data" / "jyutping_overrides.txt"
)

MIN_TOKENS = 10
MIN_SHARE = 0.8

#: HKCanCor's sentence-final particle (y, y1) and interjection (e) tags.
PARTICLE_TAGS = frozenset({"y", "y1", "e"})

#: The hand review (2026-09-28): rows the thresholds admit whose corpus reading
#: is not the one to teach. The engine's reading stays for these.
REVIEWED_OUT: dict[str, str] = {
    # Lazy sounds: the corpus transcribes the speaker's reduction, not the word.
    "來": "lai4 is the loi4 -> lai4 reduction",
    "出來": "lai4 is the loi4 -> lai4 reduction",
    "上來": "lai4 is the loi4 -> lai4 reduction",
    "越來越": "lai4 is the loi4 -> lai4 reduction",
    "啱": "aam1 drops the ng- of ngaam1",
    "噏": "ap1 drops the ng- of ngap1",
    # HKCanCor spellings a current text does not use for that reading.
    "哩": "HKCanCor writes 'this' (ni1) as 哩; current writing has 呢",
    "舋": "HKCanCor borrows the glyph for a colloquial man3",
    "哋": "the plural suffix is dei6 (我哋 ngo5 dei6); dei2 is a tone variant",
    # Readings the dictionaries do not settle.
    "黏": "CC-Canto lists nim1, nim4 and zim1; CC-CEDICT-Canto nim1",
    "寧願": "CC-CEDICT-Canto, the one catalogue dictionary with the word, reads ning4 jyun6",
    # The engine segments differently from HKCanCor.
    "下": "HKCanCor's bare 下 is 試下 (haa5); the engine also bares 下個月 'next month' (haa6)",
}

_SYLLABLE = re.compile(r"[a-z]+[1-6]")


def is_han(word: str) -> bool:
    """True iff *word* is non-empty and every character is a CJK ideograph."""
    return bool(word) and all(is_cjk_ideograph(char) for char in word)


def spaced(jyutping: str) -> str:
    """HKCanCor's unspaced ``go2go3`` as the engine's ``go2 go3``."""
    return " ".join(_SYLLABLE.findall(jyutping))


def tally(tokens: Iterable[tuple[str, str, str]]) -> tuple[dict[str, Counter[str]], dict[str, Counter[str]]]:
    """Per-word reading counts and tag counts over ``(word, jyutping, pos)`` tokens."""
    readings: dict[str, Counter[str]] = defaultdict(Counter)
    tags: dict[str, Counter[str]] = defaultdict(Counter)
    for word, jyutping, pos in tokens:
        word = unicodedata.normalize("NFC", word)
        if not jyutping or not is_han(word):
            continue
        reading = spaced(jyutping)
        if len(reading.split()) != len(word):
            continue
        readings[word][reading] += 1
        tags[word][pos] += 1
    return readings, tags


def candidates(
    readings: dict[str, Counter[str]], tags: dict[str, Counter[str]], engine: dict[str, str]
) -> list[tuple[str, str, int, int]]:
    """``(word, reading, tokens with it, tokens)`` for every row the thresholds and tags admit."""
    rows = []
    for word, counts in readings.items():
        total = sum(counts.values())
        reading, count = counts.most_common(1)[0]
        if total < MIN_TOKENS or count < MIN_SHARE * total or reading == engine.get(word, ""):
            continue
        if tags[word].most_common(1)[0][0] in PARTICLE_TAGS:
            continue
        rows.append((word, reading, count, total))
    return sorted(rows, key=lambda row: (-row[3], row[0]))


def render(rows: list[tuple[str, str, int, int]], *, engine_version: str) -> str:
    """The committed table's text."""
    header = [
        "# Spoken jyutping for yue words whose engine reading HKCanCor contradicts.",
        "#",
        "# Built by scripts/build_yue_jyutping_overrides.py from HKCanCor (Luke and Wong",
        f"# 2015, CC BY 4.0), as bundled with pycantonese {engine_version}. Do not edit by hand:",
        "# change the script and rerun it; it records the thresholds and the hand review.",
        "#",
        "# word<TAB>jyutping<TAB>tokens read so/HKCanCor tokens",
    ]
    return "\n".join([*header, *(f"{word}\t{reading}\t{count}/{total}" for word, reading, count, total in rows)]) + "\n"


def build() -> tuple[str, list[tuple[str, str, int, int]]]:
    """The table text from the installed pycantonese, and the rows the hand review dropped."""
    import pycantonese

    corpus = pycantonese.hkcancor().tokens()
    readings, tags = tally((token.word, token.jyutping or "", token.pos or "") for token in corpus)
    words = sorted(readings)
    engine = {word: reading or "" for word, reading in pycantonese.characters_to_jyutping(words)}
    rows = candidates(readings, tags, engine)
    kept = [row for row in rows if row[0] not in REVIEWED_OUT]
    dropped = [row for row in rows if row[0] in REVIEWED_OUT]
    return render(kept, engine_version=pycantonese.__version__), dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    text, dropped = build()
    args.out.write_text(text, encoding="utf-8")
    kept = sum(1 for line in text.splitlines() if line and not line.startswith("#"))
    print(f"{args.out}: {kept} rows")
    for word, reading, count, total in dropped:
        print(f"  reviewed out: {word} {reading} {count}/{total} -- {REVIEWED_OUT[word]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
