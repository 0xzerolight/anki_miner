"""Recommended downloadable Lithuanian resources (spec E.5) and the model licence audit.

**Dictionary — wty-lt-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``, 2,230,834 B). Wiktionary content extracted through kaikki.org, CC BY-SA 4.0. 188,425 of
its 197,640 rows are form-of rows (``knygos`` -> ``knyga``), which is what carries the weakest model in the
batch: an inflected front ``lt_core_news_sm`` failed to lemmatise still gets a definition naming its lemma.
No second dictionary to offer as a manual import: wiktionary-to-yomitan builds no monolingual ``wty-lt-lt`` and
no gloss-only ``wty-lt-en-gloss`` (``latest/dict/lt/en/`` holds only ``wty-lt-en`` and its ``-ipa`` twin,
checked 2026-09-23).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/lt/lt_50k.txt``, 610,944 B),
content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms, imported in occurrence mode
and lemmatised in-app (``lemmatise=True``, mandatory for Lithuanian's inflection, E.2.1). Not listed: the 15
Leipzig Lithuanian builds (``StefanVukovic99/leipzig-to-yomitan``, e.g.
``Leipzig.Lithuanian.Newscrawl.Rank.zip``, 8,877,356 B, CC BY 4.0) — surface-keyed rank lists that would add a
second ticked frequency source for no lemma coverage (the ca/nl precedent).

**Engine model — lt_core_news_sm 3.8.0** (downloaded as a language pack, never bundled): CC BY-SA 4.0, trained
on UD Lithuanian ALKSNIS v2.8 (CC BY-SA 4.0) and, for its NER component only, the TokenMill NER Corpus, which
Explosion licenses commercially; ``www.tokenmill.lt`` from the model's ``meta.json`` no longer resolves. No
corpus data is redistributed, only weights. ``licenses/lt_core_news_sm/`` ships the notice with the app.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

LT_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-lt-en",
        kind="dict",
        display_name="Wiktionary (Lithuanian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/lt/en/wty-lt-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-lt",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Lithuanian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/lt/lt_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
