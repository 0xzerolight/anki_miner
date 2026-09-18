"""Recommended downloadable Polish resources (spec B.6).

**Dictionary — wty-pl-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``, 16,078,249 B).
Wiktionary content extracted through kaikki.org, CC BY-SA 4.0; the zip's
``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: pl``. Its Grammar head lines carry gender with masculine
animacy (``stół m inan``, ``student m pers``) and verb aspect with its partner
(``robić impf (perfective zrobić)``). A card mined with it carries two licences
in its provenance: this dictionary's CC BY-SA 4.0 and the GPL-3.0 of the
``pl_core_news_sm`` model that chose the word (``licenses/pl_core_news_sm/``).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/pl/pl_50k.txt``, 676,499 B), content CC BY-SA 4.0 (the repo's
code is MIT): headerless ``word count`` lines over lowercased surface forms,
imported in occurrence mode and lemmatised in-app (``lemmatise=True``).

Not listed: the Leipzig Polish Newscrawl frequency build
(``StefanVukovic99/leipzig-to-yomitan``): its licence could not be verified and
every catalogue row is ticked by default (the ca precedent). SUBTLEX-PL:
research-only licence. Polish WSJP/SJP: no redistributable machine-readable
form. ``wty-pl-en-ipa`` waits for the pronunciation stage.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

PL_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-pl-en",
        kind="dict",
        display_name="Wiktionary (Polish-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/pl/en/wty-pl-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-pl",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Polish)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/pl/pl_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
