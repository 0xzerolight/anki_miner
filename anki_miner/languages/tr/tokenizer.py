"""Turkish tokenizer: B.1's regex over the parser's normalised line, zeyrek per word (spec §4.4, B.1).

Surfaces are verbatim match slices. A word whose apostrophe follows a capital-headed stem (``İstanbul'da``,
``Ankara’ya``) is a proper noun plus suffix (Turkish orthography): ``PROPN``, lemma = the head. Digits are ``NUM``,
any other non-letter ``PUNCT``; every other word takes ``TurkishAnalyzer``'s first reading, and a word zeyrek cannot
analyse is ``X`` (never mined), lemma = its Turkish casefold. No tagging copy is built, so §4.3's lowercased-copy rule
has nothing to skip: zeyrek lowercases each word itself, the Turkish way. zeyrek is imported inside ``build_tagger``,
so this module, the profile and the registry load without the Turkish pack.
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


class TurkishTagger:
    """Callable with the fugashi tagger contract: ``tagger(text) -> list[LanguageToken]``."""

    def __init__(self, analyse: Callable[[str], list[TrAnalysis]]) -> None:
        self._analyse = analyse

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        return [self._token(match.group()) for match in TOKEN_RE.finditer(text)]

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)

    def _token(self, surface: str) -> LanguageToken:
        if surface[0].isdigit():
            return LanguageToken(surface, "NUM", lemma=surface)
        if not surface[0].isalpha():
            return LanguageToken(surface, "PUNCT", lemma=surface)
        head = _APOSTROPHE.split(surface, maxsplit=1)[0]
        if head != surface and head[0].isupper():
            return LanguageToken(surface, "PROPN", lemma=head)
        readings = self._analyse(surface)
        if not readings:
            return LanguageToken(surface, "X", lemma=tr_casefold(surface))
        first = readings[0]
        return LanguageToken(surface, first.pos1, first.pos2, lemma=first.lemma)


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point: zeyrek's lexicon plus the family counts (4.3-5.6 s, once per process)."""
    from anki_miner.languages.tr.analyzer import TurkishAnalyzer

    return LockedTagger(TurkishTagger(TurkishAnalyzer().analyse))
