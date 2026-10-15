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

**Dictionary — FreeDict hun-eng** (FreeDict 0.4.1, ``freedict-hun-eng-0.4.1.stardict.tar.xz``, 3,002,960 B, 139,935
headwords; Vony Attila and Bui Pol, GPL-2.0): a StarDict book, imported by ``import_stardict``. Hand-made, not
Wiktionary-derived: it defines preverb verbs wty-hu-en lacks (``elkap``, ``elenged``), 442 more of the OpenSubtitles
top 20,000 (L8 research, 2026-10-15). The URL names one release and the row is sha256-pinned.

ORDER: the download flow PREPENDS each dictionary it imports (``gui/utils/resource_setup.py``), so this row is
listed before ``wty-hu-en`` and ends below it in the chain: Wiktionary answers first and FreeDict fills its misses.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

HU_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="freedict-hun-eng",
        kind="dict",
        display_name="FreeDict (Hungarian-English)",
        url="https://download.freedict.org/dictionaries/hun-eng/0.4.1/freedict-hun-eng-0.4.1.stardict.tar.xz",
        license_note="FreeDict hun-eng 0.4.1, GPL-2.0; StarDict build downloaded from upstream source.",
        sha256="52a20e2254ec63bf7412ba5420813a1a1b9ac4fa29d485d4e2454baf2f06b6c8",
    ),
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
