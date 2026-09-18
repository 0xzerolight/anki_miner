"""Recommended downloadable Greek resources (spec E.5) and the model licence audit.

**Dictionary — wty-el-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on
HuggingFace ``daxida/wty-release``, 6,009,897 B). Wiktionary content extracted through kaikki.org,
CC BY-SA 4.0; the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: el``. 265,071 of its 295,359 rows are form-of rows (``έγραψα`` → ``γράφω``), so an
inflected front the small model could not lemmatise still gets a definition naming its lemma. Manual
imports, not rows (every catalogue row starts ticked in the setup wizard, and a second dictionary
should not download by default): **wty-el-el**, the monolingual edition (37,592,051 B), and
**wty-el-en-gloss**, the gloss-only edition (782,712 B), both at
``latest/dict/el/el/wty-el-el.zip`` and ``latest/dict/el/en/wty-el-en-gloss.zip`` on the same host.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/el/el_50k.txt``,
1,018,575 B), content CC BY-SA 4.0: headerless ``word count`` lines over lowercased surface forms,
imported in occurrence mode and lemmatised in-app (``lemmatise=True``). Not listed: the Leipzig
Modern Greek Newscrawl build (``StefanVukovic99/leipzig-to-yomitan``,
``Leipzig.Modern.Greek.1453-.Newscrawl.Rank.zip``, 7,788,854 B; its licence could not be verified,
the ca precedent) and SUBTLEX-GR (licence unverified; link-only, https://www.bcbl.eu/en/subtlex-gr).
``wty-el-en-ipa`` waits for the pronunciation stage.

**Engine model — el_core_news_sm 3.8.0** (downloaded as a language pack, never bundled):
CC BY-NC-SA 3.0, trained on UD Greek GDT v2.8 (Prokopis Prokopidis; CC BY-NC-SA 3.0) and the Greek
NER Corpus (Google Summer of Code 2018; MIT). The wheel carries both licence texts;
``licenses/el_core_news_sm/`` ships them with the app.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

EL_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-el-en",
        kind="dict",
        display_name="Wiktionary (Greek-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/el/en/wty-el-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-el",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Greek)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/el/el_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); "
            "downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
