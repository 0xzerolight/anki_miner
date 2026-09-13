"""Recommended downloadable German resources (A.4).

**Dictionary — wty-de-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``, 17 MB). English
Wiktionary's German entries through kaikki.org, CC BY-SA 4.0; ``index.json``
carries ``attribution: https://kaikki.org/`` and ``sourceLanguage: de``. Its
per-lexeme Grammar head line (``Fuchs m (strong, genitive Fuchses, plural
Füchse)``) is what the gender and plural card fields read.

Not a row: **wty-de-de** (German Wiktionary, 53 MB). The setup wizard ticks
every catalogue row, so listing it would download a monolingual dictionary by
default; it also carries no Grammar head line (genders only as ``Mask``/
``Fem``/``Neut`` tags, no plural). A user who wants it imports
``https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/de/de/wty-de-de.zip``
through Settings -> Dictionaries.

Rejected: DWDS (its API terms close dictionary text to reuse), Duden
(commercial), GermaNet (research licence). yomitan.wiki lists no other German
dictionary.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/de/de_50k.txt``, 662 KB), content CC BY-SA 4.0: headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode
and lemmatised in-app (``lemmatise=True``; about 100 s for the 50k list with the
parser the German tokenizer keeps). SUBTLEX-DE: no licence text published, a
link-only mention. No Yomitan frequency dictionary exists for German.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

DE_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-de-en",
        kind="dict",
        display_name="Wiktionary (German-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/de/en/wty-de-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-de",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (German)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/de/de_50k.txt",
        license_note="FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source.",
        lemmatise=True,
    ),
)
