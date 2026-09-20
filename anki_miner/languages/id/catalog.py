"""Recommended downloadable Indonesian resources (spec C.5, plan D8/D11).

**Dictionary — wty-id-en** (``yomidevs/wiktionary-to-yomitan``, hosted on HuggingFace
``daxida/wty-release``; revision 2026.09.19 probed at 3,919,132 B, sha256
``d96ba303265daee6c46f7af109313c71276ae4a6767ea4f43770c85f7c72db90``). Wiktionary content
extracted through kaikki.org, CC BY-SA 4.0; ``index.json`` declares ``sourceLanguage: id``. Its
rows carry the inflected forms Yomitan relies on (``dibeli`` -> ``membeli``, ``nggak`` ->
``enggak``) and the etymology lines the Root and Affixes fields read (``From meng- + beli``).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/id/id_50k.txt``,
588,873 B), content CC BY-SA 4.0 (the repo's code is MIT): headerless ``word count`` lines over
lowercased surface forms, imported in occurrence mode through the in-app path
(``lemmatise=True``). The Indonesian front is the folded surface, so the aggregation only folds.
``lemmatise=True`` here carries the occurrence mode ALONE: Indonesian declares no
``lemmatised_frequency`` capability, unlike the eighteen tagger languages that set the flag, because
there is no tagger and the "lemma" the aggregation groups by is that fold (plan D8).
Translationese caveat: ``kau`` ranks #2.

Not listed: ``wty-id-id`` (id.wiktionary, formal, no colloquial forms) is a manual import; the
spec's colloquial min-rank re-ranking is not built (plan D8).
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

ID_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-id-en",
        kind="dict",
        display_name="Wiktionary (Indonesian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/id/en/wty-id-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-id",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Indonesian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/id/id_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
