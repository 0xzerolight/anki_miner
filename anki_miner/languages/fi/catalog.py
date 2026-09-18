"""Recommended downloadable Finnish resources (spec E.5).

**Dictionary - wty-fi-en** (``yomidevs/wiktionary-to-yomitan``, revision 2026.08.29, hosted on HuggingFace
``daxida/wty-release``, 118,255,438 B, carried by the resumable downloader like wty-en-en). Wiktionary content
extracted through kaikki.org, CC BY-SA 4.0; the zip's ``index.json`` carries ``attribution: https://kaikki.org/`` and
``sourceLanguage: fi``. Most of its 17 million rows are form-of rows (``kirjassa`` -> ``kirja``, singular inessive),
which a lookup still reaches when the model leaves a front unlemmatised. No Finnish monolingual edition exists.

**Frequency - OpenSubtitles 2018** (``hermitdave/FrequencyWords``, ``content/2018/fi/fi_50k.txt``, 691,168 B),
content CC BY-SA 4.0 (the repository's code is MIT): headerless ``word count`` lines over lowercased surface forms,
imported in occurrence mode and lemmatised in-app (``lemmatise=True``). A fifteen-case language spreads one word over
many rows (``talo talon talossa taloon talosta taloa``); only the aggregate ranks the word.

The aggregate is approximate, and its error moves ranks. On the full list the model leaves 5.5 % of the occurrence
mass on the surface (``oletko``, ``haluatko``) and puts 6.3 % on a lemma that is not a word (``tiedtaa``,
``tarvitnen``): 15,348 of 33,230 aggregated entries are no dictionary term, 9.05 % of the mass. That mass is taken
from the true lemma rather than lost, so a frequent verb can rank about three times worse than it should
(``kuunnella`` 671 against an oracle 223, ``tarvita`` 100 against 54) and a ``max_frequency_rank`` cutoff can drop it.
Aggregating correctly would need a Finnish morphological analyser the app does not ship; the ranks are useful, not
exact.

Not listed: Leipzig Corpora ``Leipzig.Finnish.Newscrawl.Rank.zip`` (``StefanVukovic99/leipzig-to-yomitan``) is a
surface-keyed rank list that in-app lemmatisation cannot aggregate, from a converter repository with no licence file,
and the setup wizard ticks every catalogue row; it stays a manual import. Kielipankki lists sit behind a click-through
licence. ``wty-fi-en-ipa`` waits for the pronunciation stage.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

FI_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-fi-en",
        kind="dict",
        display_name="Wiktionary (Finnish-English)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/fi/en/wty-fi-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-fi",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Finnish)",
        url="https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/fi/fi_50k.txt",
        license_note=(
            "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
        ),
        lemmatise=True,
    ),
)
