"""Recommended downloadable zh resources (spec 10.1).

Same contract as ``services/resource_catalog.RECOMMENDED_DEFAULT_SET``: ``id``
is the PINNED on-disk slot the importer writes to, so re-downloading a title
whose name embeds a release date overwrites in place instead of forking a new
directory.

**Frequency - opensubtitles-zh-word.** The ported word lists (SUBTLEX-CH, BCC)
are cut by somebody else's tokenizer and none has a stable per-file download
URL, so ``scripts/build_zh_frequency.py`` re-segments OPUS OpenSubtitles v2024
(zh_CN and zh_TW together) with this package's tokenizer; the asset is pinned by
a versioned filename and by the digest a network test re-checks. Keys are the
``script_key`` fold, which is the front a simplified run mines and one a
traditional front reaches through ``term_variants``, so no lemmatising import.

The Yomitan SUBTLEX-CH port stays a documented manual import (Settings ->
Frequency): it is distributed through a shared dictionary folder with no stable
per-file URL, and the app recommends nothing it cannot pin.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

#: The published asset, verbatim: a versioned filename on a pre-release that is never re-uploaded,
#: so this URL always returns the same bytes. The release TAG is the upload date and the FILENAME
#: the corpus build date; they differ on purpose. Pinned by digest in test_zh_frequency_asset.py.
OPENSUBTITLES_ZH_WORD_URL = (
    "https://github.com/0xzerolight/anki_miner/releases/download/"
    "resources-2026-09-21/opensubtitles-zh-word-2026.09.20.zip"
)

ZH_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="cc-cedict",
        kind="dict",
        display_name="CC-CEDICT",
        url="https://github.com/MarvNC/cc-cedict-yomitan/releases/latest/download/CC-CEDICT.zip",
        license_note="CC-CEDICT — Creative Commons Attribution-ShareAlike 3.0; downloaded from upstream source.",
    ),
    ResourceSpec(
        id="opensubtitles-zh-word",
        kind="freq",
        display_name="OpenSubtitles 2024 word frequency (Chinese)",
        url=OPENSUBTITLES_ZH_WORD_URL,
        license_note=(
            "OPUS OpenSubtitles v2024, ODC-BY 1.0 (P. Lison and J. Tiedemann, 2016; opensubtitles.org); "
            "segmented into words with jieba by Anki Miner and hosted on its releases."
        ),
    ),
)
