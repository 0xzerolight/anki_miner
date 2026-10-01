"""Greek SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``).

``el_core_news_sm`` keeps most inflected forms as their own lemma (``έχεις``, ``πήγες`` lemmatised
``πήγος``) and tags many verbs NOUN or ADJ, so a card fronted an inflected form with a noun gender.
wty-el-en names the lemma in two shapes: a ``non-lemma`` row (``πήγες`` -> ``πηγαίνω``), and a row
tagged as a headword whose every gloss is an inflection of another word ("second-person singular
present of έχω (écho)"). ``greek_row_targets`` reads the second shape as a form row too; a row
with any sense of its own (``βρέχει`` "it rains"), or whose gloss is a derivation rather than an
inflection (``δηλώνομαι`` "passive of δηλώνω", a participle, a synonym, a variant), stays a headword.

The same pass recovers the content words the model tags X or PROPN, mostly a capitalised cue-initial
verb (``Κλείσε την πόρτα.``, ``Θυμάσαι;``): the dictionary decides, never the capital, so
Ruling S2's rejection of lowercasing the tagging copy stands. ``admit_front`` keeps names out.

A surface with an enclitic second accent (``τηλέφωνό μου``) is no dictionary key, and the model can
still mislemmatise the folded copy (``τηλέφωνος``), so the pass also reads the surface without that
accent (``dictionary_spelling``).

The it-style attested-lemma pass was measured and left out: on UD Greek GDT test it moves lemma
accuracy from 81.1 % to 81.2 % (plan 2026-09-17-el, "Lemma quality").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from anki_miner.languages._spaced.form_of import lemma_row_targets, rendered_text
from anki_miner.languages.el.morphology import EL_RECOVERED_POS, fold_enclitic_accent

_GLOSSES = re.compile(r'<ol class="gloss-sc-ol" data-sc-content="glosses">(.*?)</ol>', re.S)
_GLOSS = re.compile(r'<li class="gloss-sc-li">(.*?)</li>', re.S)
_DETAILS = re.compile(r"<details.*?</details>", re.S)
#: "... of X (translit)" after a person, number or nonfinite marker: an inflection, never a derivation.
_INFLECTION_OF = re.compile(
    r"\b(?:first-person|second-person|third-person|singular|plural|nonfinite)\b[^()]*?\bof ([^\s(),:;]+) \("
)


def _inflection_targets(content: str) -> list[str] | None:
    """The lemma every gloss of a headword-tagged row names as an inflection of, or ``None`` if one does not."""
    block = _GLOSSES.search(content)
    glosses = _GLOSS.findall(block.group(1)) if block is not None else []
    targets: list[str] = []
    for gloss in glosses:
        match = _INFLECTION_OF.search(rendered_text(_DETAILS.sub("", gloss)))
        if match is None:
            return None
        targets.append(match.group(1))
    return targets or None


def greek_row_targets(content: str, tags: str) -> list[str] | None:
    """``lemma_row_targets``, with a headword-tagged row whose every gloss is an inflection read as a form row."""
    targets = lemma_row_targets(content, tags)
    return targets if targets is not None else _inflection_targets(content)


def admit_front(token: Any, front: str, heads: Sequence[tuple[str, str]]) -> bool:
    """A content token takes any front; a recovered X/PROPN token only a lowercase one no name row files.

    ``Μαρία`` has a ``name`` row, ``σοφία`` (wisdom) one beside its noun row, and ``Γιάννης`` names the
    capitalised ``Ιωάννης``. A name filed only as a common noun (``Ελπίδα``, hope) still passes.
    """
    if token.feature.pos1 not in EL_RECOVERED_POS:
        return True
    return not front[:1].isupper() and not any("name" in tags.split(" ") for _content, tags in heads)


def dictionary_spelling(token: Any) -> list[str]:
    """The lowered surface without its enclitic second accent, read right after the surface itself."""
    return [fold_enclitic_accent(token.surface.lower())]


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass
    from anki_miner.languages.registry import get_profile

    kwargs.setdefault(
        "token_post_pass",
        FormOfLemmaPass(
            row_targets=greek_row_targets,
            recover_pos=EL_RECOVERED_POS,
            accept=admit_front,
            extra_candidates=dictionary_spelling,
        ),
    )
    # Bilingual cues put an English translation line under the native one, and the
    # flattened cue becomes the card's Sentence (ZH-046, KO-06): the script gate drops it.
    kwargs.setdefault("has_target_script", get_profile(config.language).script.contains_target_script)
    return create_spaced_parser(config, **kwargs)
