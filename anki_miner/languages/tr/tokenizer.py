"""Turkish tokenizer: B.1's regex over the parser's normalised line, zeyrek per word (spec §4.4, B.1).

Surfaces are verbatim match slices. A word whose apostrophe follows a capital-headed stem (``İstanbul'da``,
``Ankara’ya``) is a proper noun plus suffix (Turkish orthography): ``PROPN``, lemma = the head. Digits are ``NUM``,
any other non-letter ``PUNCT``; every other word takes ``TurkishAnalyzer``'s first reading unless the clause says
otherwise (``_pick``), and a word zeyrek cannot analyse is ``X`` (never mined), lemma = its Turkish casefold. A pick
carries ``feature.lemma_pos``, the classes of every reading of its lemma in rank order, for the parser's label pass
(``çocuk`` is Adj first, Noun second; ``HeadwordPosPass`` asks the dictionary which one it is). No
tagging copy is built, so §4.3's lowercased-copy rule has nothing to skip: zeyrek lowercases each word itself, the
Turkish way. zeyrek is imported inside ``build_tagger``, so this module, the profile and the registry load without
the Turkish pack.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from anki_miner.languages.token import LanguageToken
from anki_miner.languages.tr.morphology import TrAnalysis, tr_casefold
from anki_miner.services.tagger import LockedTagger

#: B.1, with its number branch widened: a word with at most one internal apostrophe (any of three shapes), a
#: number that keeps an apostrophe suffix (``5'te`` is one ``NUM``, not ``5`` plus the mineable word ``te``), or
#: one other character.
TOKEN_RE = re.compile(r"[^\W\d_]+(?:['’ʼ][^\W\d_]+)?|\d+(?:[.,]\d+)*(?:['’ʼ][^\W\d_]+)?|[^\s\w]")
_APOSTROPHE = re.compile(r"['’ʼ]")
#: Existential predicates keep their reading before ``!``: ``Yangın var!`` is "there is a fire", not ``varmak``.
_EXISTENTIAL = frozenset({"var", "yok"})
#: The question particle with no person ending. It also follows a noun predicate (``Köpekler mi?``); a person
#: ending (``misin``, ``miyiz``) is the ``-Ar mI`` request itself.
_BARE_QUESTION = frozenset({"mi", "mı", "mu", "mü"})
#: Tokens that may stand between a sentence's end and its first word: dashes, quotes, opening brackets.
_OPENERS = frozenset("-‐‑‒–—―\"'“”„‟«»‘’‚‛‹›([{")
#: Tokens that end a sentence. Turkish capitalises after a colon only when a new sentence follows it.
_SENTENCE_ENDS = frozenset(".!?…:")


def _proper_head(surface: str) -> str:
    """The stem of a capital-headed apostrophe word (``İstanbul'da`` → ``İstanbul``), else ``""``."""
    head = _APOSTROPHE.split(surface, maxsplit=1)[0]
    return head if head and head != surface and head[0].isupper() else ""


def _starts_sentence(surfaces: list[str], index: int) -> bool:
    """No word of its sentence comes before this token. A cue's lines arrive joined by a space, not a break."""
    for before in reversed(surfaces[:index]):
        if before not in _OPENERS:
            return before in _SENTENCE_ENDS
    return True


class TurkishTagger:
    """Callable with the fugashi tagger contract: ``tagger(text) -> list[LanguageToken]``."""

    def __init__(self, analyse: Callable[[str], list[TrAnalysis]]) -> None:
        self._analyse = analyse

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        surfaces = [match.group() for match in TOKEN_RE.finditer(text)]
        readings = [
            self._analyse(surface) if surface[0].isalpha() and not _proper_head(surface) else [] for surface in surfaces
        ]
        tokens: list[LanguageToken] = []
        for index, surface in enumerate(surfaces):
            pick = _pick(readings, index, surfaces, tokens[-1] if tokens else None) if readings[index] else None
            tokens.append(_token(surface, pick, readings[index]))
        return tokens

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def _token(surface: str, pick: TrAnalysis | None, own: list[TrAnalysis]) -> LanguageToken:
    if surface[0].isdigit():
        return LanguageToken(surface, "NUM", lemma=surface)
    if not surface[0].isalpha():
        return LanguageToken(surface, "PUNCT", lemma=surface)
    if head := _proper_head(surface):
        return LanguageToken(surface, "PROPN", lemma=head)
    if pick is None:
        return LanguageToken(surface, "X", lemma=tr_casefold(surface))
    token = LanguageToken(surface, pick.pos1, pick.pos2, lemma=pick.lemma)
    token.feature.lemma_pos = tuple(dict.fromkeys(reading.pos1 for reading in own if reading.lemma == pick.lemma))
    return token


def _pick(
    readings: list[list[TrAnalysis]], index: int, surfaces: list[str], previous: LanguageToken | None
) -> TrAnalysis:
    """The analyzer's first reading, unless the clause names another one of the word's own readings.

    - A capital inside a sentence (not all caps): the proper-noun reading that is the whole word, when zeyrek has
      one (``Merhaba Selin`` is ``Selin``, not ``sel`` + genitive). The analyzer ranks proper nouns last because
      it sees the lowercased word; mid-sentence, Turkish capitalises only proper nouns. A sentence's first word
      carries no evidence (``Selin nerede?`` stays ``sel``), nor does a name zeyrek lacks (``Deniz``).
    - Before ``!``: the imperative (``Yardım et!`` is ``etmek``, not ``et`` "meat"), unless a determiner makes
      the word a noun phrase (``Ne güzel bir yaz!``) or it is the existential ``var``/``yok``.
    - Before a question particle: the aorist, the ``-Ar mI`` request (``Beni bekler misin?`` is ``beklemek``); an
      optative or participle homograph is no request (``Kaza mı?`` stays ``kaza``). A bare ``mI`` also asks about
      a noun, so there the first reading stays after a determiner that is no pronoun (``Bu bir karar mı?``; the
      pronoun in ``Bu olur mu?`` is the verb's subject), and when the aorist is the ``-lA`` verb made from that
      reading's noun, whose plural it spells (``Köpekler mi havlıyor?`` is ``köpek``, not ``köpeklemek``).
    """
    own = readings[index]
    surface = surfaces[index]
    if surface[0].isupper() and not surface.isupper() and not _starts_sentence(surfaces, index):
        name = tr_casefold(surface)
        for reading in own:
            if reading.pos2 == "Prop" and tr_casefold(reading.lemma) == name:
                return reading
    following = surfaces[index + 1] if index + 1 < len(surfaces) else ""
    if following == "!":
        if own[0].lemma not in _EXISTENTIAL and not (previous and previous.feature.pos1 == "DET"):
            return next((reading for reading in own if reading.imperative), own[0])
    elif following and readings[index + 1] and readings[index + 1][0].pos1 == "PART":
        aorist = next((reading for reading in own if reading.aorist), own[0])
        determiner = (
            previous is not None
            and previous.feature.pos1 == "DET"
            and all(reading.pos1 != "PRON" for reading in readings[index - 1])
        )
        plural = aorist.lemma in (own[0].lemma + "lamak", own[0].lemma + "lemek")
        if not (tr_casefold(following) in _BARE_QUESTION and (determiner or plural)):
            return aorist
    return own[0]


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point: zeyrek's lexicon plus the family counts (4.3-5.6 s, once per process)."""
    from anki_miner.languages.tr.analyzer import TurkishAnalyzer

    return LockedTagger(TurkishTagger(TurkishAnalyzer().analyse))
