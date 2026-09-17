"""Recommended downloadable Norwegian Bokmål resources (E.5).

**Dictionary — wty-nb-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``; 3,255,676 bytes). English Wiktionary's Norwegian Bokmål entries extracted through
kaikki.org, CC BY-SA 4.0; the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: nb``. Its noun rows carry the masc/fem/neut tags and the ``Grammar`` head line (``bok f or m``)
the article hook reads, and its particle verbs are keyed apart (``stå opp``).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/no/no_50k.txt``, 601,916 bytes;
the folder is the downstream code ``no``, ``nb/nb_50k.txt`` does not exist), content CC BY-SA 4.0: headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode and lemmatised in-app
(``lemmatise=True``) before ranking. Not a row: Leipzig Corpora ``Leipzig.Norwegian.Bokmal.Newscrawl.Rank.zip``
(CC BY 4.0) — a surface-keyed RANK list, and rank lists cannot be lemma-aggregated, so under the pre-ticked wizard
it would put un-lemmatised ranks beside the lemmatised list; it stays a documented manual import.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

NB_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-nb-en",
        kind="dict",
        display_name="Wiktionary (Norwegian Bokmål)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/nb/en/wty-nb-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-no",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Norwegian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/no/no_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
