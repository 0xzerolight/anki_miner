"""Recommended downloadable Catalan resources (spec E.5).

**Dictionary — wty-ca-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``, 4,630,799 B).
Wiktionary content extracted through kaikki.org, CC BY-SA 4.0; the zip's
``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: ca``. No Catalan monolingual edition exists (no ``ca/ca``
tree). A card mined with it carries two licences in its provenance: this
dictionary's CC BY-SA 4.0 and the GPL-3.0 of the ``ca_core_news_sm`` model that
chose the word (``licenses/ca_core_news_sm/``).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/ca/ca_50k.txt``, 564,792 B), content CC BY-SA 4.0 (the repo's
code is MIT): headerless ``word count`` lines over lowercased surface forms,
imported in occurrence mode and lemmatised in-app (``lemmatise=True``). Known
gap: the list was tokenised with the interpunct as punctuation, so no ``l·l``
word has a rank of its own (``tranquil·la`` is counted as ``tranquil`` +
``la``); those words stay unranked and follow ``frequency_keep_unranked``.

Not listed: the Leipzig Catalan Newscrawl and Andorra frequency builds
(``StefanVukovic99/leipzig-to-yomitan``) keep the interpunct, but their licence
could not be verified (the Leipzig download terms sit behind a bot wall and the
converter repository states none), and every catalogue row is ticked by
default. SUBTLEX-CAT: licence unverified, no public download; link-only.
``wty-ca-en-ipa`` waits for the pronunciation stage.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

CA_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-ca-en",
        kind="dict",
        display_name="Wiktionary (Catalan-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ca/en/wty-ca-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-ca",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Catalan)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ca/ca_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
