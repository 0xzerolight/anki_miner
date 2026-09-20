"""Recommended downloadable Persian resources (spec C.2).

**Dictionary — wty-fa-en** (``yomidevs/wiktionary-to-yomitan``, hosted on HuggingFace
``daxida/wty-release``). Wiktionary content extracted through kaikki.org, CC BY-SA 4.0. The build
this branch was written against is revision **2026.09.19**, 3,328,547 B, sha256
``0654d5b24ae8b405b0959909a2947e13d7c6df8564607aba215f0edc2e5931a9`` — recorded here because the
URL is a moving ``latest/`` pointer and ``ResourceSpec`` has no digest field to pin it with (no
catalogue row in the project pins one). The same build is what
``anki_miner/languages/fa/data/compound_verbs.tsv`` was derived from, so the two agree about which
noun + light-verb pairs are real compounds. 16,965 of its 17,040 lemma rows carry the Grammar head
line the romanisation card field reads, and its 4,655 ``mi``-prefixed redirect rows carry no ZWNJ at
all, which is why the lookup ladder has a ZWNJ-stripped rung.

Manual imports, not rows (every catalogue row starts ticked in the setup wizard, and a second
dictionary should not download by default): **wty-fa-en-ipa**, the same build with IPA on the head
line, and the legacy **kty-fa-en** r2 build, both under ``latest/dict/fa/`` on the same host.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/fa/fa_50k.txt``),
content CC BY-SA 4.0: headerless ``word count`` lines over surface forms, imported in occurrence
mode and lemmatised in-app (``lemmatise=True``, R21). Persian needs that more than most: a single
verb spreads over hundreds of conjugated rows, and the list's very first entry is a bare Persian
comma, which the script gate drops. No converter script and no release asset exist for fa — the
spec's hand-built lemma list would have duplicated what the in-app lemmatiser does with the same
tables the tokenizer already loads.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

FA_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-fa-en",
        kind="dict",
        display_name="Wiktionary (Persian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/fa/en/wty-fa-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-fa",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Persian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/fa/fa_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
