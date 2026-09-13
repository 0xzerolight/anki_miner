"""Recommended downloadable Dutch resources (B.6).

**Dictionary — wty-nl-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``; 8,531,805 bytes).
English Wiktionary's Dutch entries extracted through kaikki.org, CC BY-SA 4.0;
the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: nl``. Its noun rows carry the masc/fem/neut tags and the
``Grammar`` head line the de/het hook reads. Rejected: Van Dale (commercial, no
redistributable machine-readable form), ``wty-nl-nl`` (monolingual; a manual
import for advanced learners).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/nl/nl_50k.txt``, 650,762 bytes), content CC BY-SA 4.0: headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode
and lemmatised in-app (``lemmatise=True``) before ranking. Not a row: Leipzig
Corpora ``Leipzig.Dutch.Mixed.zip`` (CC BY 4.0) — a surface-keyed RANK list, and
rank lists cannot be lemma-aggregated, so under the pre-ticked wizard it would
put un-lemmatised ranks beside the lemmatised list; it stays a documented manual
import. SUBTLEX-NL: licence unverified.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

NL_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-nl-en",
        kind="dict",
        display_name="Wiktionary (Dutch)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/nl/en/wty-nl-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-nl",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Dutch)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/nl/nl_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
