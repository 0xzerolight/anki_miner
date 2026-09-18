"""Recommended downloadable Swedish resources (B.6).

**Dictionary — wty-sv-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``; 8,197,851 bytes).
English Wiktionary's Swedish entries extracted through kaikki.org, CC BY-SA 4.0;
the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: sv``. Its noun rows carry the gender chips and the
``Grammar-content`` head line (``apa c (plural apor)``) the en/ett hook reads.
Rejected: SAOL/SO (no redistributable machine-readable form), ``wty-sv-sv``
(monolingual; a manual import for advanced learners).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/sv/sv_50k.txt``, 623,913 bytes), content CC BY-SA 4.0: headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode
and lemmatised in-app (``lemmatise=True``) before ranking. Not a row: Leipzig
Corpora (CC BY 4.0) — a surface-keyed RANK list, and rank lists cannot be
lemma-aggregated, so under the pre-ticked wizard it would put un-lemmatised ranks
beside the lemmatised list; it stays a documented manual import.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

SV_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-sv-en",
        kind="dict",
        display_name="Wiktionary (Swedish)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/sv/en/wty-sv-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-sv",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Swedish)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/sv/sv_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
