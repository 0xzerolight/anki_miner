"""Greek tokenizer: ``el_core_news_sm`` through the shared spaCy adapter.

No parser (Greek has no separable verbs; ``labels.parser`` has no ``compound:prt``) and no hyphen
join (``ελληνο-τουρκική`` is already one token; glued dashes still split through the shared dash
infix). Curly and modifier apostrophes are folded in the tagging copy: the model's elision exceptions
know ``'`` and ``’`` but not U+02BC, so ``Θʼ`` would tag ADJ and mine as a word. The abbreviation set
is the sentence splitter's, so ``κ.``/``χλμ.`` stay tokenizer exceptions and ``Νικ.``/``αν.`` do not.

A proparoxytone before an enclitic takes a second acute (``το αυτοκίνητό μου``, ``άκουσέ με``), a
spelling neither the model nor wty-el-en knows: ``αυτοκίνητό`` lemmatises ``αυτοκίνητός``, ``Άκουσέ``
tags NOUN, and neither front has a dictionary row. ``fold_enclitic_accent`` drops it from the tagging
copy, so the lemma (and with it the card front) is the dictionary spelling while the surface stays
as written.

A capitalised content word whose lemma is its surface is re-lemmatised lowercase (Ruling S2 R; UD GDT
dev+test lemma exact 80.99 % -> 81.92 %, POS untouched).
"""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages._spaced.morphology import APOSTROPHE_FOLD
from anki_miner.languages._spaced.tokenizer import build_spacy_tagger
from anki_miner.languages.el.morphology import EL_ABBREVIATIONS, EL_MODEL_PACKAGE
from anki_miner.services.tagger import LockedTagger

_ACUTE = "́"
_LETTER_RUN = re.compile(r"[^\W\d_]+")


def _without_acute(char: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", char).replace(_ACUTE, ""))


#: Every precomposed Greek letter with an acute (tonos), to the same letter without it: one code point
#: each (``ό`` -> ``ο``, ``ΐ`` -> ``ϊ``), so the tagging copy keeps its length.
_UNACCENTED: dict[str, str] = {
    char: _without_acute(char)
    for char in map(chr, range(0x0370, 0x0400))
    if _ACUTE in unicodedata.normalize("NFD", char) and len(_without_acute(char)) == 1
}


def _fold_run(match: re.Match[str]) -> str:
    run = match.group()
    accents = [index for index, char in enumerate(run) if char in _UNACCENTED]
    if len(accents) < 2:
        return run
    chars = list(run)
    for index in accents[1:]:
        chars[index] = _UNACCENTED[chars[index]]
    return "".join(chars)


def fold_enclitic_accent(text: str) -> str:
    """Every accented letter after the first in a letter run loses its acute; the length never changes.

    A monotonic Greek word carries one accent, and a second one only before an enclitic, always
    later in the word. Per letter run, not per whitespace token: ``νωρίς—αλλά`` is two words.
    """
    return _LETTER_RUN.sub(_fold_run, text)


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point."""
    return build_spacy_tagger(
        EL_MODEL_PACKAGE,
        tag_char_map=APOSTROPHE_FOLD,
        tag_fold=fold_enclitic_accent,
        abbreviations=EL_ABBREVIATIONS,
        relemmatise_capitalised=True,
    )
