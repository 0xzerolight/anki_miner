"""Recommended downloadable Hungarian resources (E.5).

**Dictionary — wty-hu-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``; 13,063,865 bytes). English Wiktionary's Hungarian entries extracted through kaikki.org,
CC BY-SA 4.0; the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and ``sourceLanguage: hu``.
Many preverb verbs have no row (``elkap``, ``elenged``); the lookup rung ``preverb_less_verb`` falls back to the
bare verb. No ``wty-hu-hu`` edition exists; ``wty-hu-en-ipa`` belongs to the unbuilt pronunciation stage.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/hu/hu_50k.txt``, 698,652 bytes),
content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms, imported in occurrence mode and
lemmatised in-app (``lemmatise=True``), which an agglutinative language needs: one lemma spreads over dozens of
surface rows. Not a row: Leipzig Corpora ``Leipzig.Hungarian.Mixed.Rank.zip`` (CC BY 4.0, 12,232,736 bytes), a
surface-keyed rank list that cannot be lemma-aggregated (the Dutch ruling); it stays a documented manual import.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

HU_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-hu-en",
        kind="dict",
        display_name="Wiktionary (Hungarian)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/hu/en/wty-hu-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-hu",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Hungarian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/hu/hu_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
