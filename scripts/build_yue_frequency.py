#!/usr/bin/env python3
"""Build the Cantonese frequency dictionary from PyCantonese's own corpora.

Cantonese has no hermitdave list and no wordfreq data, and words.hk publishes no
counts under a licence that allows hosting, so ``yue``'s frequency source is a
first-party asset (R30) built out of the two corpora that ship inside the
``pycantonese`` wheel:

* **HKCanCor** -- the Hong Kong Cantonese Corpus (Luke and Wong 2015), 153,656
  hand-segmented tokens of conversational Cantonese recorded in 1997-98,
  CC BY 4.0.
* **CTCPC** -- the Cantonese-Traditional Chinese Parallel Corpus, 121,138
  sentences, CC0 1.0. Its sentences are unsegmented, so they run through the
  same ``pycantonese.segment`` the app mines with.

``data/ctcpc/`` is in the ``yue`` pack's ``exclude`` list, so it never reaches a
user's disk: this script runs against a plain ``pip install pycantonese`` at
build time, and imports nothing from ``anki_miner``.

**Token filter: every character is a CJK ideograph.** Spec F.1 says the script
"drops PUNCT, NUM and non-Han tokens"; this is a recorded deviation from that
line, ruled 2026-09-20. Measured over a 46,765-token CTCPC sample: **no** all-Han
token is tagged ``PUNCT``, so the Han gate already subsumes that half; and the
tagger calls ``多謝``, ``好多`` and ``一啲`` ``NUM``, so obeying the NUM clause
would delete common vocabulary from the list. That the single predicate also
removes the whole tagging pass is a bonus, not the justification. CTCPC's raw
text carries stray quotation marks glued to Han runs (``"...軍隊``) and control
characters, which the same gate removes.

The output declares ``frequencyMode: rank-based`` and carries ranks, not counts:
the list merges two corpora roughly seven times apart in kept-token count
(HKCanCor 122,934, CTCPC 914,269), where a summed count is not a count of
anything, while the merged rank is exactly what ``max_frequency_rank`` reads.
``services/frequency/source_importer.py`` stores a
declared rank-based value as the rank verbatim. The literal has to match
``mode_probe.RANK_BASED`` exactly: the importer takes the declared mode from the
zip and never validates it, so a near-miss like "rank" would be treated as an
occurrence count and invert the order, in an asset that is published once and
never edited.

Zip entries carry a fixed timestamp, so two builds of the same corpora are
byte-identical and the published asset's sha256 can be reproduced.

Usage:
  python scripts/build_yue_frequency.py OUT.zip
  python scripts/build_yue_frequency.py OUT.zip --revision 2026-09-20
"""

from __future__ import annotations

import argparse
import io
import json
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

#: The importer's own constant, duplicated here rather than imported: this
#: script runs against a checkout without the app installed. Any drift is caught
#: by tests/unit/test_build_yue_frequency.py, which compares the two.
RANK_BASED = "rank-based"

#: ``utils.ja_normalize.CJK_IDEOGRAPH_RANGES``, duplicated for the same reason
#: and pinned to it by the same test.
CJK_IDEOGRAPH_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),
    (0x3400, 0x4DBF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x30000, 0x3134F),
    (0x31350, 0x323AF),
    (0x2EBF0, 0x2EE5F),
    (0xF900, 0xFAFF),
    (0x2F800, 0x2FA1F),
)

TITLE = "HKCanCor + CTCPC Cantonese frequency"
ATTRIBUTION = (
    "HKCanCor (K. K. Luke and May L. Y. Wong, The Hong Kong Cantonese Corpus, 2015), CC BY 4.0; "
    "CTCPC (Cantonese-Traditional Chinese Parallel Corpus), CC0 1.0. "
    "Both bundled with PyCantonese; built by scripts/build_yue_frequency.py."
)

#: Fixed zip entry timestamp so a rebuild is byte-identical.
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def is_han(word: str) -> bool:
    """True iff *word* is non-empty and every character is a CJK ideograph."""
    return bool(word) and all(any(low <= ord(char) <= high for low, high in CJK_IDEOGRAPH_RANGES) for char in word)


def count_tokens(tokens: Iterator[str], counts: Counter[str]) -> tuple[int, int]:
    """Add the Han tokens of *tokens* to *counts*; return (seen, kept)."""
    seen = kept = 0
    for token in tokens:
        seen += 1
        term = unicodedata.normalize("NFC", token)
        if is_han(term):
            counts[term] += 1
            kept += 1
    return seen, kept


def hkcancor_tokens() -> Iterator[str]:
    """Every hand-segmented HKCanCor word."""
    import pycantonese

    return iter(pycantonese.hkcancor().words())


def ctcpc_tokens(source: Path | None = None) -> Iterator[str]:
    """Every CTCPC sentence segmented with the mining engine."""
    import pycantonese

    if source is None:
        source = Path(pycantonese.__file__).parent / "data" / "ctcpc" / "sents.json"
    sentences = json.loads(source.read_text(encoding="utf-8"))
    for sentence in sentences:
        yield from pycantonese.segment(sentence)


def rank(counts: Counter[str]) -> list[tuple[str, int]]:
    """``(term, rank)`` from 1, most frequent first, ties broken by codepoint."""
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(term, position) for position, (term, _count) in enumerate(ordered, 1)]


def build_zip(rows: list[tuple[str, int]], *, revision: str) -> bytes:
    """A Yomitan ``frequency`` dictionary as zip bytes."""
    index = {
        "format": 3,
        "revision": revision,
        "title": TITLE,
        "sourceLanguage": "yue",
        "frequencyMode": RANK_BASED,
        "attribution": ATTRIBUTION,
    }
    bank = [[term, "freq", position] for term, position in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in (
            ("index.json", json.dumps(index, ensure_ascii=False, indent=2)),
            ("term_meta_bank_1.json", json.dumps(bank, ensure_ascii=False)),
        ):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--revision", default="", help="default: today, UTC")
    parser.add_argument("--ctcpc", type=Path, default=None, help="read this sents.json instead of the wheel's")
    args = parser.parse_args()

    counts: Counter[str] = Counter()
    hk_seen, hk_kept = count_tokens(hkcancor_tokens(), counts)
    ct_seen, ct_kept = count_tokens(ctcpc_tokens(args.ctcpc), counts)
    rows = rank(counts)
    revision = args.revision or datetime.now(UTC).strftime("%Y-%m-%d")
    args.out.write_bytes(build_zip(rows, revision=revision))
    print(f"hkcancor: {hk_kept}/{hk_seen} tokens kept")
    print(f"ctcpc:    {ct_kept}/{ct_seen} tokens kept")
    print(f"{args.out}: {len(rows)} terms, revision {revision}")
    print("top 20: " + " ".join(term for term, _ in rows[:20]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
