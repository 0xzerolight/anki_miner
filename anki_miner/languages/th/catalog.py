"""Recommended downloadable Thai resources (spec C.3).

**Dictionary -- wty-th-en** (``yomidevs/wiktionary-to-yomitan``, hosted on
HuggingFace ``daxida/wty-release``, 2,704,086 B, sha256 87aa7ff2...): Wiktionary
content extracted through kaikki.org, CC BY-SA 4.0. 29,681 rows over 22,356
terms; 19,550 carry the Paiboon reading in their Grammar line and 1,628 name a
classifier, which is what the two th render hooks read.

Documented manual imports, deliberately not rows (a catalogue row starts ticked
in the setup wizard, and a second dictionary should not download by default):
**Volubilis** (windwerfer/volubilis_dict, ``volubilis_all_yomitan.zip``, 100,766
entries with Paiboon-like readings, classifiers and CEFR levels -- its own
bracket style, which the Paiboon hook also reads), **LEXiTRON** (NECTEC licence
with an acknowledgement clause) and the monolingual **wty-th-th**.

Rejected: hermitdave ``th`` (its "words" are whitespace-split clauses and the top
ranks carry double-encoded TIS-620 mojibake) and wordfreq (no Thai). Thai is one
of the two languages whose frequency list is self-hosted instead (R21): the Thai
National Corpus list ships inside the PyThaiNLP wheel, not as a plain
``word count`` file, so ``scripts/convert_tnc_thai_frequency.py`` converts it.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

TH_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-th-en",
        kind="dict",
        display_name="Wiktionary (Thai-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/th/en/wty-th-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    # Both frequency assets are BUILT by scripts/convert_tnc_thai_frequency.py out
    # of the lists inside the pythainlp wheel and published as release data. The
    # dated filename is never reused: a corrected list ships under a new date, so
    # a URL that once resolved always returns the same bytes, which is what the
    # network-marked test in test_th_frequency_asset.py re-checks.
    ResourceSpec(
        id="tnc-th",
        kind="freq",
        display_name="Thai National Corpus frequency",
        url=(
            "https://github.com/0xzerolight/anki_miner/releases/download/" "resources-2026-09-20/tnc-th-2026-09-20.zip"
        ),
        license_note="Thai National Corpus word list via PyThaiNLP, CC0. Built by scripts/convert_tnc_thai_frequency.py.",
    ),
    ResourceSpec(
        id="ttc-th",
        kind="freq",
        display_name="Thai textbook corpus frequency",
        url=(
            "https://github.com/0xzerolight/anki_miner/releases/download/" "resources-2026-09-20/ttc-th-2026-09-20.zip"
        ),
        license_note="Thai Textbook Corpus word list via PyThaiNLP, CC0. Built by scripts/convert_tnc_thai_frequency.py.",
    ),
)
