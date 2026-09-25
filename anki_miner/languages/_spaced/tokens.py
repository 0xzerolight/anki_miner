"""spaCy tokens → fugashi-shaped ``LanguageToken``s (spec §4.2).

Engine-free on purpose (it only reads token attributes), so it is unit-tested
with stub tokens. ``surface`` is ALWAYS ``text[tok.idx : tok.idx + len(tok.text)]``
over the ORIGINAL line: ``morphology.iter_token_spans`` locates tokens with a
case-sensitive ``str.find`` and silently drops what it cannot find, from mining
and from count_lemmas alike. The model may have tagged a lowercased,
apostrophe-folded copy (``morphology.tagging_copy``); that copy is the same
length, so every offset holds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from anki_miner.languages._spaced.morphology import case_lemma
from anki_miner.languages.token import LanguageToken

#: Heads a separable particle can belong to (§4.3 item 2(a)).
PARTICLE_HEAD_POS: frozenset[str] = frozenset({"VERB", "AUX"})


_LETTER_DOT_LETTER = re.compile(r"[^\W\d_]\.[^\W\d_]")


def is_dotted_abbreviation(surface: str) -> bool:
    """An abbreviation token: longer than one character, holds a letter, and ends in ``.`` or has letter-dot-letter.

    A trailing dot survives only through a tokenizer exception or an initial
    (de ``z.B.``/``Dr.`` NOUN, en ``a.m.`` NOUN, ``e.g.`` ADV, fr ``etc.`` NOUN,
    it ``ecc.`` ADV). Several tokenizers split the FINAL dot off instead and tag
    the rest as content (es/pt/it/fr ``a.m`` NOUN, pt ``p.ex`` NOUN, fr ``p.ex``
    VERB, ca/de ``a.m`` PROPN), so an internal letter-dot-letter counts too.
    None is vocabulary. Numbers (``3.5``) hold no letter; URLs are ``like_url``.
    """
    return (
        len(surface) > 1
        and any(char.isalpha() for char in surface)
        and (surface.endswith(".") or _LETTER_DOT_LETTER.search(surface) is not None)
    )


def to_duck_tokens(
    doc: Iterable[Any],
    text: str,
    *,
    title_case_pos: frozenset[str] = frozenset(),
    particle_deps: frozenset[str] = frozenset(),
) -> list[LanguageToken]:
    """Convert one parsed line.

    ``pos1`` is UPOS, or ``"X"`` for a URL/e-mail token (spaCy's lexical
    ``like_url``/``like_email``) or a dotted abbreviation
    (``is_dotted_abbreviation``); each passes a Latin script gate and none is
    vocabulary. ``pos2`` is the fine tag when it says more than UPOS.
    ``particle_deps`` is the language's separable-verb dependency labels: a
    token carrying one, whose head is a different VERB/AUX token, stashes its
    casefolded surface on the head as ``feature.particle`` (first one wins) and
    is demoted to ``pos1="PART"``, outside every allowed class.
    ``SeparableVerbPass`` consumes the stash on the parser side.
    """
    kept = [tok for tok in doc if not tok.is_space]
    by_index: dict[int, LanguageToken] = {}
    out: list[LanguageToken] = []
    for tok in kept:
        surface = text[tok.idx : tok.idx + len(tok.text)]
        pos1 = "X" if (tok.like_url or tok.like_email or is_dotted_abbreviation(surface)) else tok.pos_
        token = LanguageToken(
            surface=surface,
            pos1=pos1,
            pos2=tok.tag_ if tok.tag_ != tok.pos_ else "",
            lemma=case_lemma(tok.lemma_ or surface, pos1, title_case_pos),
            kana="",
            morph=str(tok.morph),
        )
        by_index[tok.i] = token
        out.append(token)
    if particle_deps:
        for tok in kept:
            head = tok.head
            if tok.dep_ not in particle_deps or head.i == tok.i or head.pos_ not in PARTICLE_HEAD_POS:
                continue
            head_token = by_index.get(head.i)
            if head_token is None or getattr(head_token.feature, "particle", None):
                continue
            head_token.feature.particle = tok.text.casefold()
            by_index[tok.i].feature.pos1 = "PART"
    return out
