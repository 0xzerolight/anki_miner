"""Recommended downloadable Danish resources (E.5).

**Dictionary - wty-da-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``; 3,192,528 bytes). English Wiktionary's Danish entries extracted through kaikki.org, CC BY-SA
4.0; the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and ``sourceLanguage: da``. Its noun rows
carry the ``Grammar`` head line (``bog c (singular definite bogen, ...)``) the en/et hook reads, and its two-word
verb headwords (``stå op``) are what a particle join is attested against. wty publishes no ``wty-da-da`` edition.

**Frequency - OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/da/da_50k.txt``, 616,396 bytes),
content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms, imported in occurrence mode and
lemmatised in-app (``lemmatise=True``) before ranking. Not a row: Leipzig Corpora
``Leipzig.Danish.Wikipedia.Rank.zip`` (``StefanVukovic99/leipzig-to-yomitan``, CC BY 4.0), a surface-keyed RANK list
that cannot be lemma-aggregated; the setup wizard ticks every row, so it would rank un-lemmatised surfaces beside
the lemmatised list. It stays a manual import. Rejected: DSL and KorpusDK lists (licence unverified, E.5.3).
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

DA_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-da-en",
        kind="dict",
        display_name="Wiktionary (Danish)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/da/en/wty-da-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-da",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Danish)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/da/da_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream "
            "source."
        ),
        lemmatise=True,
    ),
)
