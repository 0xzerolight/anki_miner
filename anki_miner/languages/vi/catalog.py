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
)
