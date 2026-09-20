"""Indonesian tokenizer: a regex word splitter producing fugashi-shaped duck tokens (spec C.5).

No tagger exists without sklearn or torch, so the classes are lexical: ``WORD``, or ``NUM`` (a digit,
except the R3 ``buku2`` reduplication), ``PUNCT``, ``PROPN`` (an acronym anywhere, a capitalised word
mid-sentence; heuristic), ``LATIN_OTHER`` (a small English list) and ``X`` (an abbreviation
before its dot). ``pos2 = "stopword"`` marks the function-word tier. Hyphenated words stay one token
(``buku-buku``, ``orang-orangan``, ``sayur-mayur``); a spaced dash is punctuation. Surfaces are
slices of the text as given: ``morphology.iter_token_spans`` finds tokens by ``str.find``.
"""

from __future__ import annotations

import re
from typing import Any

from anki_miner.languages._spaced.morphology import is_all_caps_cue
from anki_miner.languages.id.morphology import ID_ABBREVIATIONS, LATIN_OTHER, id_fold, is_stopword
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

#: A letter or digit, or a combining mark (an NFD accent left after the profile's NFC normalise).
_LETTER = r"(?:[^\W_]|[\N{COMBINING GRAVE ACCENT}-\N{COMBINING LATIN SMALL LETTER X}])"
#: What joins two letter runs into one word: a hyphen (reduplication) or an apostrophe (``Jum'at``).
_JOINER = r"[-'\N{RIGHT SINGLE QUOTATION MARK}]"
_TOKEN_RE = re.compile(rf"(?P<word>{_LETTER}+(?:{_JOINER}{_LETTER}+)*)|(?P<punct>[^\w\s]|_)")
_REDUPLICATED_DIGIT_RE = re.compile(r"^[^\W\d_]+2(?:nya|ku|mu)?$")
_SENTENCE_END = frozenset(".!?…")


def _pos(surface: str, lemma: str, *, sentence_start: bool, shouted: bool) -> str:
    if any(char.isdigit() for char in surface) and not _REDUPLICATED_DIGIT_RE.match(lemma):
        return "NUM"
    if lemma in LATIN_OTHER:
        return "LATIN_OTHER"
    acronym = len(surface) > 1 and surface.isupper()
    if not shouted and surface[:1].isupper() and (acronym or not sentence_start):
        return "PROPN"
    return "WORD"


class IndonesianTagger:
    """Callable with the fugashi ``Tagger`` surface the parser consumes; stateless."""

    def __call__(self, text: str, **_: Any) -> list[LanguageToken]:
        shouted = is_all_caps_cue(text)
        tokens: list[LanguageToken] = []
        sentence_start = True
        abbreviation_dot = False
        for match in _TOKEN_RE.finditer(text):
            surface = match.group()
            if match.lastgroup == "punct":
                tokens.append(LanguageToken(surface=surface, pos1="PUNCT", lemma=surface))
                if surface in _SENTENCE_END and not abbreviation_dot:
                    sentence_start = True
                abbreviation_dot = False
                continue
            lemma = id_fold(surface)
            abbreviation_dot = lemma in ID_ABBREVIATIONS and text[match.end() : match.end() + 1] == "."
            pos1 = "X" if abbreviation_dot else _pos(surface, lemma, sentence_start=sentence_start, shouted=shouted)
            pos2 = "stopword" if pos1 == "WORD" and is_stopword(lemma) else ""
            tokens.append(LanguageToken(surface=surface, pos1=pos1, pos2=pos2, lemma=lemma))
            sentence_start = False
        return tokens

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """``tagger_provider``'s entry point. Pure Python, nothing to load: the lock only keeps the shared surface."""
    return LockedTagger(IndonesianTagger())
