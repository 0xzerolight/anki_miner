"""Recommended downloadable Romanian resources (spec E.5).

**Dictionary - wty-ro-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``, 12,377,651 B). Wiktionary content extracted through kaikki.org, CC BY-SA 4.0; the
zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and ``sourceLanguage: ro``. Lemma rows are
keyed in comma-below spelling. Form-of rows are keyed WITHOUT diacritics and carry the real spelling as their
reading (``carti`` / ``cărți`` -> ``carte``); lookups match the reading too, so an inflected word still
reaches its lemma.

**Frequency - OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/ro/ro_50k.txt``,
654,282 B), content CC BY-SA 4.0 (the repo's code is MIT): headerless ``word count`` lines over lowercased
surface forms, imported in occurrence mode and lemmatised in-app (``lemmatise=True``). Most of its words
spelt with s-comma or t-comma use the legacy cedilla instead (4,266,899 occurrences beside 392,140); the
tagger reads a comma-below copy and the key fold unifies both spellings, so the two rows sum under one lemma.
Clitic-joined rows tokenize to more than one token and keep their own spelling.

Not listed: the Leipzig Romanian Newscrawl and Moldova builds (``StefanVukovic99/leipzig-to-yomitan``), whose
licence could not be verified, while the setup wizard ticks every catalogue row (the ca precedent). No
SUBTLEX-RO exists. ``wty-ro-en-ipa`` waits for the pronunciation stage.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

RO_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-ro-en",
        kind="dict",
        display_name="Wiktionary (Romanian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ro/en/wty-ro-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-ro",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Romanian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ro/ro_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
