"""Recommended downloadable Ukrainian resources (spec B.6, plan P7, P12, P13).

**Dictionary — wty-uk-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.09.19, HuggingFace
``daxida/wty-release``, 9,780,618 B): Wiktionary via kaikki.org, CC BY-SA 4.0. Its Grammar head
lines name gender, animacy and the aspect partner (``чита<acute>ти • (čytáty) impf (perfective
прочита<acute>ти)``), and although its LEMMA rows carry no reading, every non-lemma row does --
which is where the S24 stressed headword on the card comes from (plan P7). It is the only
Ukrainian dictionary offered: the app needs a Yomitan artefact and nothing else redistributable
exists (spec B.6; the Ukrainian СУМ has no machine-readable form).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/uk/uk_50k.txt``,
887,259 B), content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms,
imported in occurrence mode and lemmatised in-app (``lemmatise=True``). Two known limits, recorded
rather than worked around: the list strips the Ukrainian apostrophe instead of folding it
(``пять``, ``мяч``, ``імя`` -- no apostrophe character appears anywhere in the file), so an
apostrophe front never finds a rank; and it carries Russian rows from mixed subtitle files
(``что``, ``ты`` inside the top ten). Folding the apostrophe away in the key would win the ranks
back and lose every dictionary hit, which is the worse trade.

Not listed: the Leipzig Ukrainian builds (every catalogue row is ticked by default and a
``ResourceSpec`` has no un-ticked state; the pl/ca/ru precedent), ``wty-uk-en-ipa`` (the
pronunciation stage), and ``dmklinger.github.io/ukrainian`` -- CC BY-SA 3.0, Wiktionary and dbnary
data with stress, but published only as three bespoke web-app JSON blobs with no release artefact
the importer can read, under a repository whose licence field is NOASSERTION (plan P13).
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

UK_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-uk-en",
        kind="dict",
        display_name="Wiktionary (Ukrainian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/uk/en/wty-uk-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-uk",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Ukrainian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/uk/uk_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
