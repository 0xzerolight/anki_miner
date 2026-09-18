"""Recommended downloadable Croatian resources (spec E.5).

**Dictionary - wty-sh-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``, 8,555,841 B, 457,883 terms). Wiktionary content extracted through kaikki.org,
CC BY-SA 4.0. There is no ``hr`` tree: Wiktionary files Croatian, Serbian and Bosnian under Serbo-Croatian,
and the zip's ``index.json`` declares ``sourceLanguage: "sh"`` - which is why the profile's
``wiktionary_code`` is ``sh`` while everything else Croatian keys on ``hr``. The dictionary therefore carries
Cyrillic spellings and sr/bs senses; the Cyrillic spelling of a term lives inside its entry (the head line's
"Cyrillic spelling of ..."), never as a card front, because the mined form is the Latin lemma the tagger
returns.

**Frequency - OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/hr/hr_50k.txt``,
630,560 B, 202,438,751 occurrences), content CC BY-SA 4.0 (the repo's code is MIT): headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode and lemmatised in-app
(``lemmatise=True``). Croatian nouns inflect for seven cases and verbs for person and tense, so an
unaggregated import ranks each inflected surface separately; the aggregation is what makes
``max_frequency_rank`` mean what it means for a lemma-keyed list. The probe rows in
``services/frequency/mode_probe.py`` are what tell the importer this list counts occurrences.

Not listed: hrLex 1.3 (a lemma-frequency list, deferred with the other treebank-derived lists) and Leipzig's
Croatian corpora, whose licence could not be verified while the setup wizard ticks every catalogue row (the
ca precedent). ``wty-sh-en-ipa`` waits for the pronunciation stage.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

HR_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-sh-en",
        kind="dict",
        display_name="Wiktionary (Serbo-Croatian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/sh/en/wty-sh-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-hr",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Croatian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/hr/hr_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
