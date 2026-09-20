"""Recommended downloadable Hebrew resources (spec F.2).

**Dictionary -- wty-he-en** (``yomidevs/wiktionary-to-yomitan``, hosted on HuggingFace
``daxida/wty-release``). Wiktionary content extracted through kaikki.org, CC BY-SA 4.0. The build
this branch was written against is revision **2026.09.19**, 3,310,540 B, sha256
``ae5371d9b401687949d460c9ae81389249dc2f7424f80444426f02894de2e0b4`` -- recorded here because the
URL is a moving ``latest/`` pointer and ``ResourceSpec`` has no digest field to pin it with (no
catalogue row in the project pins one). 161,727 rows: 15,308 lemma rows over 12,865 distinct keys
and 146,419 ``non-lemma`` form rows, which is what ``languages/he/morphology.py`` resolves card
fronts against. Hebrew is the one language here that cannot mine well WITHOUT its dictionary: with
none installed every front is the folded surface.

**Frequency -- OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/he/he_50k.txt``),
content CC BY-SA 4.0: 50,000 headerless ``word count`` lines over surface forms, imported in
occurrence mode with ``lemmatise=False``. Hebrew has no tagger, so there is nothing to lemmatise
the list WITH -- it stays surface-keyed, and ``lemmatised_frequency`` is deliberately not declared.

Not rows, because every catalogue row starts ticked in the setup wizard and a second dictionary
should not download by default: **wty-he-en-ipa** (the same build with IPA on the head line) and
the larger non-English targets ``wty-he-ru`` and ``wty-he-zh``, all under ``latest/dict/he/`` on
the same host; and the Leipzig corpora builds
(``StefanVukovic99/leipzig-to-yomitan``, Newscrawl / Wikipedia / News, CC BY 4.0). There is no
he-he monolingual: he.wiktionary is not a wty source edition.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

__all__ = ["HE_CATALOG"]

HE_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-he-en",
        kind="dict",
        display_name="Wiktionary (Hebrew-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/he/en/wty-he-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-he",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Hebrew)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/he/he_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
    ),
)
