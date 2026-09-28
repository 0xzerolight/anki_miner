"""Turkish tokenizer: B.1's regex over the parser's normalised line, zeyrek per word (spec §4.4, B.1).

Surfaces are verbatim match slices. A word whose apostrophe follows a capital-headed stem (``İstanbul'da``,
``Ankara’ya``) is a proper noun plus suffix (Turkish orthography): ``PROPN``, lemma = the head. Digits are ``NUM``,
any other non-letter ``PUNCT``; every other word takes ``TurkishAnalyzer``'s first reading unless the clause says
otherwise (``_pick``), and a word zeyrek cannot analyse is ``X`` (never mined), lemma = its Turkish casefold. No
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


def _proper_head(surface: str) -> str:
    """The stem of a capital-headed apostrophe word (``İstanbul'da`` → ``İstanbul``), else ``""``."""
    head = _APOSTROPHE.split(surface, maxsplit=1)[0]
    return head if head != surface and head[0].isupper() else ""


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
            tokens.append(_token(surface, pick))
        return tokens

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def _token(surface: str, pick: TrAnalysis | None) -> LanguageToken:
    if surface[0].isdigit():
        return LanguageToken(surface, "NUM", lemma=surface)
    if not surface[0].isalpha():
        return LanguageToken(surface, "PUNCT", lemma=surface)
    if head := _proper_head(surface):
        return LanguageToken(surface, "PROPN", lemma=head)
    if pick is None:
        return LanguageToken(surface, "X", lemma=tr_casefold(surface))
    return LanguageToken(surface, pick.pos1, pick.pos2, lemma=pick.lemma)


def _pick(
    readings: list[list[TrAnalysis]], index: int, surfaces: list[str], previous: LanguageToken | None
) -> TrAnalysis:
    """The analyzer's first reading, unless the clause names another one of the word's own readings.

    - Before ``!``: the imperative (``Yardım et!`` is ``etmek``, not ``et`` "meat"), unless a determiner makes
      the word a noun phrase (``Ne güzel bir yaz!``) or it is the existential ``var``/``yok``.
    - Before a question particle: the aorist, the ``-Ar mI`` request (``Beni bekler misin?`` is ``beklemek``); an
      optative or participle homograph is no request (``Kaza mı?`` stays ``kaza``).
    """
    own = readings[index]
    following = surfaces[index + 1] if index + 1 < len(surfaces) else ""
    if following == "!":
        if own[0].lemma not in _EXISTENTIAL and not (previous and previous.feature.pos1 == "DET"):
            return next((reading for reading in own if reading.imperative), own[0])
    elif following and readings[index + 1] and readings[index + 1][0].pos1 == "PART":
        return next((reading for reading in own if reading.aorist), own[0])
    return own[0]


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point: zeyrek's lexicon plus the family counts (4.3-5.6 s, once per process)."""
    from anki_miner.languages.tr.analyzer import TurkishAnalyzer

    return LockedTagger(TurkishTagger(TurkishAnalyzer().analyse))
