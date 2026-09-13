"""Recommended downloadable Portuguese resources (B.6).

**Dictionary — wty-pt-en** (``yomidevs/wiktionary-to-yomitan``, revision
2026.08.29, hosted on HuggingFace ``daxida/wty-release``, 11,230,214 B).
Wiktionary content extracted through kaikki.org, CC BY-SA 4.0; the zip's
``index.json`` declares ``sourceLanguage: pt`` and ``attribution:
https://kaikki.org/``. Noun rows carry ``masc``/``fem`` tags and a
``Grammar-content`` head line (``livro m (plural livros, …)``), which the gender
hook reads. Priberam and the Porto Editora dictionaries have no redistributable
machine-readable form (B.6) and are not offered.

**Frequency — OpenSubtitles 2018** (``hermitdave/FrequencyWords``, content
CC BY-SA 4.0): ``content/2018/pt_br/pt_br_50k.txt`` (650,066 B) and
``content/2018/pt/pt_50k.txt`` (652,361 B), headerless ``word count`` lines over
lowercased surface forms, imported in occurrence mode and lemmatised in-app
(``lemmatise=True``). Each list carries its ``script_variant`` id, so the setup
wizard pre-ticks the one matching the configured variety (B.3).

Rejected as rows: Leipzig Corpora ``Portuguese.Brazil.Newscrawl`` /
``Portuguese.Portugal.Newscrawl`` (``StefanVukovic99/leipzig-to-yomitan``) —
rank-mode Yomitan zips keyed on news-register surface forms, which in-app
lemmatisation does not reach, from a converter repository with no licence
file; they stay a manual import in Settings. SUBTLEX-PT: research-only licence.
"""

from __future__ import annotations

from anki_miner.services.resource_catalog import ResourceSpec

_HERMITDAVE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"
_OPENSUBTITLES_LICENSE = (
    "FrequencyWords by Hermit Dave, content CC BY-SA 4.0 (OpenSubtitles 2018); downloaded from upstream source."
)

PT_CATALOG: tuple[ResourceSpec, ...] = (
    ResourceSpec(
        id="wty-pt-en",
        kind="dict",
        display_name="Wiktionary (Portuguese)",
        url="https://huggingface.co/datasets/daxida/wty-release/resolve/main/latest/dict/pt/en/wty-pt-en.zip",
        license_note=(
            "Wiktionary via kaikki.org, CC BY-SA 4.0; Yomitan build by wiktionary-to-yomitan, "
            "downloaded from upstream source."
        ),
    ),
    ResourceSpec(
        id="opensubtitles-pt-br",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (Brazilian Portuguese)",
        url=f"{_HERMITDAVE}/pt_br/pt_br_50k.txt",
        license_note=_OPENSUBTITLES_LICENSE,
        lemmatise=True,
        variant="br",
    ),
    ResourceSpec(
        id="opensubtitles-pt",
        kind="freq",
        display_name="OpenSubtitles 2018 frequency (European Portuguese)",
        url=f"{_HERMITDAVE}/pt/pt_50k.txt",
        license_note=_OPENSUBTITLES_LICENSE,
        lemmatise=True,
        variant="pt",
    ),
)
