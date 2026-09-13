"""Recommended downloadable French resources (A.4), with the licence audit.

**Dictionary — wty-fr-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``, 10,927,921 B).
French entries of the English Wiktionary via kaikki.org, CC BY-SA 4.0;
``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: fr``. Headwords use the ASCII apostrophe (``aujourd'hui``);
noun rows carry ``masc``/``fem`` tags and a ``Grammar-content`` head line
(``chaise f (plural chaises)``) that the gender hook reads.
**Not a row: wty-fr-fr** (the French Wiktionary edition, 75,996,286 B). The
setup wizard ticks every catalogue row and has no optional-row mechanism, so a
second 76 MB monolingual dictionary would download by default; it stays a manual
import (Settings -> Add Dictionary).
Rejected: Le Robert, Larousse and the TLFi (all rights reserved, no licence for
redistribution or bulk use), Littré (public domain but no Yomitan build), MarvNC
``wikipedia-yomitan`` (encyclopaedic, 180-330 MB).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/fr/fr_50k.txt``, 653,452 B), content CC BY-SA 4.0: headerless
``word count`` lines over lowercased surface forms (elided ``c' l' j'`` are
their own words), imported in occurrence mode and lemmatised in-app
(``lemmatise=True``). Lexique 3.83 (lemma + film frequency, CC BY-SA) needs a
converter script and is deferred (§9). No Yomitan frequency dictionary exists for
French.

**Engine** — ``fr_core_news_sm`` 3.8.0 (``languages/fr/pack.py``): model licence
LGPL-LR (UD French Sequoia 2.8), WikiNER CC BY 4.0, spaCy lookups MIT. Nothing is
bundled; the user downloads the pinned wheel at runtime.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

FR_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-fr-en",
        kind="dict",
        display_name="Wiktionary (French-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/fr/en/wty-fr-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-fr",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (French)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/fr/fr_50k.txt",
        license_note="FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source.",
        lemmatise=True,
    ),
)
