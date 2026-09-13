"""Recommended downloadable Italian resources (A.4) and the model licence audit.

**Dictionary — wty-it-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``, 17,202,945 B).
Wiktionary content extracted through kaikki.org, CC BY-SA 4.0; the zip's
``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: it``. It carries the combined clitic forms (``dammelo``,
``dimmi``, ``trovarlo``) as headwords. **wty-it-it** (the monolingual edition,
12,888,615 B) is a documented manual import, not a row: every catalogue row is
pre-ticked in the setup wizard, and a second dictionary should not download by
default. Rejected: De Mauro, Treccani, Zingarelli (commercial; no Yomitan build
may be redistributed).

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``,
``content/2018/it/it_50k.txt``, 653,205 B), content CC BY-SA 4.0: headerless
``word count`` lines over lowercased surface forms, imported in occurrence mode
and lemmatised in-app before ranking. SUBTLEX-IT publishes no licence text: a
link, never a row. No Yomitan frequency dictionary exists for Italian.

**Engine model — it_core_news_sm 3.8.0** (downloaded as a language pack, never
bundled): CC BY-NC-SA 3.0, trained on UD Italian ISDT v2.8 (Bosco, Lenci,
Montemagni, Simi; CC BY-NC-SA 3.0) and WikiNER (Nothman, Ringland, Radford,
Murphy, Curran; CC BY 4.0). The wheel carries both licence texts inside the
extracted package; ``licenses/it_core_news_sm/`` ships them with the app.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

IT_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-it-en",
        kind="dict",
        display_name="Wiktionary (Italian-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/it/en/wty-it-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-it",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Italian)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/it/it_50k.txt",
        license_note="FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source.",
        lemmatise=True,
    ),
)
