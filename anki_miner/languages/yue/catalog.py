"""Recommended downloadable Cantonese resources (spec F.1).

**Dictionaries.** Both take the zh ``cc-cedict`` shape: an upstream release
asset under a stable ``latest/download`` name, never mirrored. MarvNC rebuilds
the release daily, so digests change daily and neither row pins one, exactly as
zh's does not. Measured 2026-09-20 against the real term banks:

* ``CC-Canto.zip`` -- 1,785,659 B, 52,447 rows over 46,063 terms,
  ``"title": "CC-Canto [2017-02-02]"``, format 3, sequenced, isUpdatable, author
  "Pleco, Marv". **Zero** ``CL:`` rows.
* ``CC-CEDICT.Canto.zip`` -- 5,484,068 B, 166,267 rows over 163,280 terms,
  ``"title": "CC-CEDICT Canto [2026-09-19]"``, author "MDBG, CC-CEDICT, Marv,
  Pleco". **2,458** rows carry an inline ``CL:`` classifier, which is where the
  ``measure_word`` field comes from.

Neither declares ``sourceLanguage``, so neither trips the S19 import warning.
Both spell the reading as lower-case, syllable-spaced jyutping
(``jat1 gin6 waan4 jat1 gin6``), which is exactly what
``characters_to_jyutping`` emits -- the reading keys match without conversion.

**Every row starts TICKED**, including the small ``wty-yue-en``. Spec F.1 asked
for it un-ticked, but ``ResourceSpec.variant`` is the only pre-tick lever the
setup wizard has (``setup_wizard/pages.py:900``) and it means "belongs to this
script variant", which yue does not use. Overloading it to mean "off by default"
would be a new mechanism invented inside a language stage. It is 27,498 B of
rare hanzi, so ticking it costs one small download (orchestrator ruling,
2026-09-20).

**words.hk 粵典 is a documented manual import, never a row.** It is the richest
Cantonese dictionary (https://github.com/MarvNC/wordshk-yomitan), but its data
licence -- Non-Commercial Open Data License 1.0, https://words.hk/base/hoifong/,
香港辭書有限公司 -- is non-commercial with no sub-licensing, so it can never be
hosted or downloaded on the user's behalf. Its assets are also dated rather than
``latest``, so no stable URL exists to pin. The import receipt shows the
dictionary's own attribution line.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

YUE_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="cc-canto",
        kind="dict",
        display_name="CC-Canto (Cantonese-English)",
        url="https://github.com/MarvNC/cc-cedict-yomitan/releases/latest/download/CC-Canto.zip",
        license_note=(
            "CC-Canto - (c) Pleco Software, Creative Commons Attribution-ShareAlike 3.0 "
            "(cantonese.org/about.html); Yomitan build by MarvNC."
        ),
    ),
    ResourceSpec(
        id="cc-cedict-canto",
        kind="dict",
        display_name="CC-CEDICT Canto (Cantonese-English)",
        url="https://github.com/MarvNC/cc-cedict-yomitan/releases/latest/download/CC-CEDICT.Canto.zip",
        license_note=(
            "CC-CEDICT - CC BY-SA 3.0; Cantonese readings (c) Pleco, CC BY-SA 3.0; " "Yomitan build by MarvNC."
        ),
    ),
    # BUILT by scripts/build_yue_frequency.py out of the two corpora inside the
    # pycantonese wheel and published as release data (R30): Cantonese is not on
    # hermitdave, wordfreq has no yue, and words.hk publishes no counts under a
    # licence that allows hosting. rank-based rather than th's occurrence-based
    # counts because this list MERGES two corpora about seven times apart in
    # size, where a summed count is not a count of anything while the merged
    # rank is exactly what max_frequency_rank reads. The dated filename is never
    # reused -- a corrected list ships under a new date, so a URL that once
    # resolved always returns the same bytes, which is what the network-marked
    # test in test_yue_frequency_asset.py re-checks.
    ResourceSpec(
        id="hkcancor-yue",
        kind="freq",
        display_name="HKCanCor + CTCPC Cantonese frequency",
        url=(
            "https://github.com/0xzerolight/anki_miner/releases/download/"
            "resources-2026-09-20/hkcancor-yue-2026-09-20.zip"
        ),
        license_note=(
            "HKCanCor (Luke and Wong 2015), CC BY 4.0; CTCPC, CC0 1.0. Both bundled with "
            "PyCantonese. Built by scripts/build_yue_frequency.py."
        ),
    ),
    ResourceSpec(
        id="wty-yue-en",
        kind="dict",
        display_name="Wiktionary (Cantonese-English)",
        url=(
            "https://huggingface.co/datasets/daxida/wty-release/resolve/main/"
            "latest/dict/yue/en/wty-yue-en.zip?download=true"
        ),
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan. "
            "Small (3,061 entries, mostly rare hanzi) - the two CC dictionaries are the main chain."
        ),
    ),
)
