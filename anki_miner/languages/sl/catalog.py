"""Recommended downloadable Slovenian resources (spec E.5).

**Dictionary - wty-sl-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.09.19, hosted on
HuggingFace ``daxida/wty-release``, 828,222 B, 56,797 rows over 54,245 terms). Wiktionary content
extracted through kaikki.org, CC BY-SA 4.0. It is the THINNEST Wiktionary dictionary of the wave -
hr's wty-sh-en carries 457,883 terms - so a Slovenian deck will meet unmatched fronts: ``prebrati``
has no entry at all, and only 207 of 3,035 noun rows (6.8 %) carry a Grammar head line. The card's
gender and aspect fields therefore come from the tagger's ``morph`` first and the dictionary's own
chips second, which is the shipped ``GENDER_SOURCES`` order; the head line is the last resort, not
the first.

A Slovenian verb that is BOTH aspects carries no ``aspect_pair`` field, and that is deliberate. The
shared hook answers from chips only when there is exactly one (two chips can be two lexemes), and
neither other source can help here: this dictionary has 0 head lines saying "impf or pf" over all
56,797 rows, and the model tags a biaspectual verb ``Vmb*`` with no ``Aspect`` feature at all
(``videti`` -> ``Vmbn VerbForm=Inf``). Printing nothing beats guessing one of the two.

**Frequency - OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/sl/sl_50k.txt``,
619,289 B, 100,952,811 occurrences), content CC BY-SA 4.0 (the repo's code is MIT): headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode and lemmatised
in-app (``lemmatise=True``). Slovenian nouns inflect for six cases across three numbers, so an
unaggregated import ranks each inflected surface separately; the aggregation is what makes
``max_frequency_rank`` mean what it means for a lemma-keyed list. The probe rows in
``services/frequency/mode_probe.py`` are what tell the importer this list counts occurrences.

Not listed: Sloleks 3.0 and the other CLARIN.SI lemma lists (R30/D16 defer treebank- and
CLARIN-derived lists to the resource stage) and Leipzig's Slovenian corpora, whose licence could not
be verified while the setup wizard ticks every catalogue row (the ca precedent). ``wty-sl-en-ipa``
waits for the pronunciation stage.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

SL_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-sl-en",
        kind="dict",
        display_name="Wiktionary (Slovene-English, small)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/sl/en/wty-sl-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source. About 54,000 terms: some words have no entry."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-sl",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Slovenian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/sl/sl_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
