"""Recommended downloadable Spanish resources (A.4).

**Dictionary — wty-es-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``; 21,441,806 B).
Wiktionary content extracted through kaikki.org, CC BY-SA 4.0; the zip's
``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: es``. Its rows mark gender (``nieve f (plural nieves)``, a
``gender-feminine`` tag chip), which the ``noun_gender`` field reads, and carry a
form-of row for nearly every clitic combination (``dámelo`` → ``dar``).

**Not a row: wty-es-es** (the monolingual edition, same host, ``dict/es/es/``,
21,976,140 B). The setup wizard ticks every catalogue row, and a second default
dictionary of that size is not the optional extra A.4 describes; it stays a
manual import through the dictionary settings.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/es/es_50k.txt``), content CC BY-SA 4.0: headerless ``word count``
lines over lowercased surface forms, imported in occurrence mode and lemmatised
in-app (``lemmatise=True``) through the Spanish tagger, whose verb repair turns
``dime``/``déjame``/``cállate`` into ``decir``/``dejar``/``callar`` before ranking.
No Yomitan frequency dictionary exists for Spanish; SUBTLEX-ESP has no stated
licence and stays link-only.

Rejected: RAE/DLE (all rights reserved, no API; scrapers are a ToS risk).
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

ES_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-es-en",
        kind="dict",
        display_name="Wiktionary (Spanish-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/es/en/wty-es-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-es",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Spanish)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/es/es_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
