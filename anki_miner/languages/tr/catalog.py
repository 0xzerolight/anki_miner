"""Recommended downloadable Turkish resources (B.6).

**Dictionary — wty-tr-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.09.19, hosted on HuggingFace
``daxida/wty-release``; 17,504,531 bytes). English Wiktionary's Turkish entries extracted through kaikki.org,
CC BY-SA 4.0; ``index.json`` carries ``attribution: https://kaikki.org/`` and ``sourceLanguage: tr``. Every inflected
form wty knows is its own ``non-lemma`` row pointing at its headword (``kitapları`` -> ``kitap``), so the surface rung
of the lookup ladder finds an entry even where the analyzer's pick is wrong.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/tr/tr_50k.txt``, 711,935 bytes),
content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms, imported in occurrence mode and
lemmatised in-app (``lemmatise=True``), which an agglutinative language needs. Not a row: Leipzig Corpora's Turkish rank
list, surface-keyed and so not lemma-aggregable (the Dutch/Hungarian ruling); it stays a documented manual import.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

TR_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-tr-en",
        kind="dict",
        display_name="Wiktionary (Turkish)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/tr/en/wty-tr-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-tr",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Turkish)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/tr/tr_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
