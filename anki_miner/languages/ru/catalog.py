"""Recommended downloadable Russian resources (spec B.6, plan D12).

**Dictionary — OpenRussian** (``ImenaOphelia/openrussian-to-yomitan``, revision 2026.03.01,
``opr-ru-en.zip``, 28,179,164 B): the OpenRussian.org learner dictionary, CC BY-SA 4.0. Every row
carries the stressed headword as its reading, which the stressed-headword seam (S24) puts on the card.
It holds no Grammar head line and no categorised tags, so for a word it defines the grammar hook reads
gender and aspect from the tagger and names no aspect partner.

**Dictionary — wty-ru-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.09.19, HuggingFace
``daxida/wty-release``, 26,133,529 B): Wiktionary via kaikki.org, CC BY-SA 4.0. Its Grammar head lines
name gender, animacy and the aspect partner (``читать • (čitátʹ) impf (perfective прочитать …)``).

ORDER: the download flow PREPENDS each dictionary it imports (``gui/utils/resource_setup.py``), so wty
is listed first and OpenRussian ends first in the chain (spec B.2: OpenRussian first).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/ru/ru_50k.txt``,
998,861 B), content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms,
imported in occurrence mode and lemmatised in-app (``lemmatise=True``).

Not listed: the Leipzig Russian frequency builds (every catalogue row is ticked by default and a
``ResourceSpec`` has no un-ticked state; the pl/ca precedent) and ``wty-ru-en-ipa`` (the
pronunciation stage).
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

RU_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-ru-en",
        kind="dict",
        display_name="Wiktionary (Russian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/ru/en/wty-ru-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opr-ru-en",
        kind="dict",
        display_name="OpenRussian (Russian-English)",
        url="https://github.com/ImenaOphelia/openrussian-to-yomitan/releases/latest/download/opr-ru-en.zip",
        license_note=(
            "OpenRussian.org, CC BY-SA 4.0; Yomitan build by openrussian-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-ru",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Russian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/ru/ru_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
