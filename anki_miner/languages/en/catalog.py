"""Recommended downloadable English resources (A.4).

**Dictionary — wty-en-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``). Wiktionary content
extracted through kaikki.org, CC BY-SA 4.0; the zip's ``index.json`` carries
``attribution: https://kaikki.org/`` and ``sourceLanguage: en``. English
Wiktionary is monolingual for English, so this is the only edition. 102 MB.
Rejected: GCIDE/Webster and WordNet (permissive, but no Yomitan build exists),
MarvNC ``wikipedia-yomitan`` (encyclopaedic, 180-330 MB), CMUdict (ARPAbet, not a
dictionary).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/en/en_50k.txt``), content CC BY-SA 4.0: headerless ``word count``
lines over lowercased surface forms, so it is imported in occurrence mode and
lemmatised in-app (``lemmatise=True``) before ranking. SUBTLEX-US is the better
list but its licence is research-only: a documented manual import, never a row.
No Yomitan frequency dictionary exists for English.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

EN_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-en-en",
        kind="dict",
        display_name="Wiktionary (English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/en/en/wty-en-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-en",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (English)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_50k.txt",
        license_note="FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source.",
        lemmatise=True,
    ),
)
