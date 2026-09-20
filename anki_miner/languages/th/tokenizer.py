"""PyThaiNLP newmm tokenizer producing fugashi-shaped duck tokens.

Emits ``LanguageToken`` (NOT ``morphology.SyntheticToken``): that class's
isinstance gates drive ja-only attested-reading and span-replacement merge
passes, and a Thai token caught by them would be swept into Japanese morphology.

Surfaces are the newmm segments verbatim -- newmm never respells, and
``morphology.iter_token_spans`` locates each token by ``str.find`` from a running
cursor and silently DROPS what it cannot find, so a normalised surface would
delete the word from the mined set with no error anywhere.

Zero-width characters are word-break hints, not words. U+200B survives newmm as
its own token even with ``keep_whitespace=False`` (it is not ``str.isspace()``),
and the model tags it VERB; the text is split on them first and they are never
emitted. ``iter_token_spans``' cursor walks past them.

``pos1`` is overridden for three cases the model gets wrong by construction: a
mark (PUNCT), a digit run in either numeral set (NUM) and a run with no Thai
letter (X -- perceptron/tud calls ``Netflix`` a VERB). ``pos2`` is this package's
tier, not a finer tagset: the model has no second level.
"""

from __future__ import annotations

import re

from anki_miner.languages.th import _engine  # noqa: F401  # env guard; must precede pythainlp
from anki_miner.languages.th.tiers import TH_MARK_CODE_POINTS, TH_MARKS, TH_STOPWORDS
from anki_miner.languages.token import LanguageToken
from anki_miner.services.tagger import LockedTagger

ZWSP = "\N{ZERO WIDTH SPACE}"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"

#: Authored word breaks in e-books and subtitles: split on them, never emit them.
_ZERO_WIDTH_RE = re.compile(f"[{ZWSP}{ZWNJ}]+")
#: Thai letters and vowel/tone signs, MINUS the marks (U+0E2F PAIYANNOI and
#: U+0E46 MAIYAMOK sit inside the two ranges and are not letters).
_THAI_LETTERS = frozenset(chr(cp) for cp in [*range(0x0E01, 0x0E3B), *range(0x0E40, 0x0E4F)]) - TH_MARK_CODE_POINTS
#: A run of Arabic or Thai digits, with group and decimal separators inside it.
_DIGIT_RUN_RE = re.compile(
    "^[0-9\N{THAI DIGIT ZERO}-\N{THAI DIGIT NINE}][0-9\N{THAI DIGIT ZERO}-\N{THAI DIGIT NINE},.]*$"
)
#: newmm-safe is the guard against the O(n^2) worst case on an unspaced run.
_SAFE_MODE_CHARS = 100


class ThaiTagger:
    """Callable with the fugashi ``Tagger`` surface the parser already consumes."""

    def __init__(self) -> None:
        from pythainlp.tag import pos_tag
        from pythainlp.tokenize import word_tokenize

        self._tokenize = word_tokenize
        self._pos_tag = pos_tag

    def _segments(self, text: str) -> list[str]:
        out: list[str] = []
        for chunk in _ZERO_WIDTH_RE.split(text):
            if not chunk:
                continue
            engine = "newmm-safe" if len(chunk) > _SAFE_MODE_CHARS and " " not in chunk else "newmm"
            out += [tok for tok in self._tokenize(chunk, engine=engine, keep_whitespace=False) if tok.strip()]
        return out

    def _tier(self, surface: str) -> str:
        if surface in TH_MARKS:
            return "mark"
        return "stopword" if surface in TH_STOPWORDS else ""

    def __call__(self, text: str) -> list[LanguageToken]:
        segments = self._segments(text)
        if not segments:
            return []
        tokens: list[LanguageToken] = []
        for surface, tag in self._pos_tag(segments, engine="perceptron", corpus="tud"):
            tier = self._tier(surface)
            if tier == "mark":
                pos1 = "PUNCT"
            elif _DIGIT_RUN_RE.match(surface):
                pos1 = "NUM"
            elif not any(char in _THAI_LETTERS for char in surface):
                pos1 = "X"
                tier = ""
            else:
                pos1 = tag
            tokens.append(LanguageToken(surface=surface, pos1=pos1, pos2=tier, lemma=surface, kana=""))
        return tokens

    def parse(self, text: str) -> list[LanguageToken]:
        """fugashi-compatible alias so ``LockedTagger.parse`` delegates cleanly."""
        return self(text)


def build_tagger() -> LockedTagger:
    """Build a lock-guarded Thai tokenizer.

    ``LockedTagger`` is reused verbatim from the ja stack: newmm builds its
    dictionary Trie lazily on the first cut and the perceptron model is a
    module-level singleton, which is the same hazard the ja lock already covers.
    """
    return LockedTagger(ThaiTagger())
