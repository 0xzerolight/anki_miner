"""Recommended downloadable vi resources (spec C.4).

Same contract as ``services/resource_catalog.RECOMMENDED_DEFAULT_SET``: ``id`` is
the pinned on-disk slot, ``url`` goes to ``resource_downloader`` and ``kind``
routes it.

**Dictionary - wty-vi-en.** English Wiktionary via kaikki.org, CC BY-SA 4.0;
Yomitan build by wiktionary-to-yomitan, downloaded from the upstream release.
Revision 2026.09.19: 4,325,049 B, 77,838 rows over 48,661 terms, no readings and
no IPA. Lemmas use new-style tone placement and carry the old-style spelling as a
form row ("Traditional tone placement spelling of ..."); ``VI_KEYS`` folds both to
one key. 12,520 rows carry "Sino-Vietnamese word from <hanzi>", which the Han Viet
card field reads.

**Frequency - opensubtitles-vi-word.** No free word-level subtitle list exists
(hermitdave and wordfreq rank syllables), so ``scripts/build_vi_frequency.py``
re-segments OPUS OpenSubtitles v2024 with this package's tokenizer; the asset is
pinned by a versioned filename and by the digest a network test re-checks. Keys
are folded fronts, so no lemmatising import.

**Documented manual imports** (Settings -> Dictionaries or the frequency
import; the app recommends none of them by name): the VNEDICT v4 Yomitan build
(thu-tram/viet-yomitan, 54,345 headwords, data CC BY 3.0 - credit the VNEDICT
authors); Tu dien Tieng Viet thong dung (42,012 POS-tagged monolingual
entries; its data licence is unverified); the Leipzig Corpora Collection
News/Mixed word-frequency Yomitan builds (CC BY 4.0; they carry punctuation
rows and name bigrams, so set a minimum rank when importing). Never
recommended: OVDP/FVDP (GPL or unclear terms) and rips of Babylon, Apple or
Lac Viet dictionaries.
"""

from __future__ import annotations

from anki_miner.languages.profile import ResourceSpec

#: The published asset, verbatim: a versioned filename on a pre-release that is never re-uploaded,
#: so this URL always returns the same bytes. The release TAG is the upload date and the FILENAME
#: the corpus build date; they differ on purpose. Pinned by digest in test_vi_frequency_asset.py.
OPENSUBTITLES_VI_WORD_URL = (
    "https://github.com/0xzerolight/anki_miner/releases/download/"
    "resources-2026-09-20/opensubtitles-vi-word-2026.09.19.zip"
)

VI_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-vi-en",
        kind="dict",
        display_name="Wiktionary (Vietnamese)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/vi/en/wty-vi-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-vi-word",
        kind="freq",
        display_name="OpenSubtitles 2024 word frequency (Vietnamese)",
        url=OPENSUBTITLES_VI_WORD_URL,
        license_note=(
            "OPUS OpenSubtitles v2024, ODC-BY 1.0 (P. Lison and J. Tiedemann, 2016; opensubtitles.org); "
            "segmented into words with underthesea by Anki Miner and hosted on its releases."
        ),
    ),
)
