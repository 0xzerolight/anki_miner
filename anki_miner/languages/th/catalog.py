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
)
