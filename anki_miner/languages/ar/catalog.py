"""Recommended downloadable Arabic resources (spec C.1, R21).

**Dictionary — wty-ar-en** (``yomidevs/wiktionary-to-yomitan``, hosted on HuggingFace
``daxida/wty-release``): revision 2026.09.19 measured 13,584,376 B, 1,019,643 rows, ``sourceLanguage: ar``
(the spec's 2026-08-29 build was 13,386,336 B; the slot id is pinned, so a re-download overwrites in
place). Wiktionary content through kaikki.org, CC BY-SA 4.0. Its ~980 k form-of rows resolve broken
plurals and conjugations a user types into the curator. Manual imports, not rows: ``wty-ar-en-ipa`` and
the dialect editions ``wty-arz/apc/ajp/afb-en`` on the same host.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/ar/ar_50k.txt``,
805,779 B), content CC BY-SA 4.0: headerless ``word count`` surface lines, lemmatised in-app with the
Arabic tagger (``lemmatise=True``, R21 — no self-hosted converter asset).

**Engine data — calima-msa-r13** (the language pack, ``languages/ar/pack.py``): GPL-2.0, notice in
``licenses/calima-msa-r13/``.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

AR_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-ar-en",
        kind="dict",
        display_name="Wiktionary (Arabic-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ar/en/wty-ar-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-ar",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Arabic)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ar/ar_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
