"""The CAMeL morphological analyzer, analysis with no backoff (ported, see ``__init__``).

``Analyzer.analyze(word)`` returns every analysis the database licenses for one
word: a list of feature dicts (``diac lex root pattern pos prc0-3 enc0 d3tok
pos_lex_logprob source gloss ...``). An unanalysable Arabic word returns ``[]``.
Choosing between analyses is the caller's job; the language package takes the
highest ``pos_lex_logprob``, which is what CAMeL's MLE disambiguator falls back
to for a word it has no statistics for.

Upstream's optional ``cachetools.LFUCache`` wrapper is not ported: its default
is off, and caching whole analysis lists would hold far more memory than the
small per-word summary the tokenizer caches itself.
"""

from __future__ import annotations

import copy
import itertools
import re
from collections.abc import Iterator
from typing import Any

from .charsets import AR_CHARSET, dediac_ar, is_punct_or_symbol
from .database import Entry, MorphologyDB
from .morph_utils import merge_features

__all__ = ["Analyzer"]

_IS_DIGIT_RE = re.compile("^.*[0-9\u0660-\u0669]+.*$")

_COPY_FEATS = frozenset(["gloss", "atbtok", "atbseg", "d1tok", "d1seg", "d2tok", "d2seg", "d3tok", "d3seg", "bwtok"])
_UNDEFINED_LEX_FEATS = frozenset(["root", "pattern", "caphi"])

#: Upstream ``DEFAULT_NORMALIZE_MAP``: alef variants to bare alef, alef maqsura
#: to ya, ta marbuta to ha, tatweel removed. Applied to the lookup key only.
_NORMALIZE_TABLE = str.maketrans(
    {
        "\u0625": "\u0627",
        "\u0623": "\u0627",
        "\u0622": "\u0627",
        "\u0671": "\u0627",
        "\u0649": "\u064a",
        "\u0629": "\u0647",
        "\u0640": "",
    }
)


def _is_digit(word: str) -> bool:
    return _IS_DIGIT_RE.match(word) is not None


def _is_punc(word: str) -> bool:
    return all(is_punct_or_symbol(char) for char in word)


def _has_punc(word: str) -> bool:
    return any(is_punct_or_symbol(char) for char in word)


def _is_ar(word: str) -> bool:
    return all(char in AR_CHARSET for char in word)


def _segments(word: str, max_prefix: int, max_suffix: int) -> Iterator[tuple[str, str, str]]:
    length = len(word)
    for p in range(0, min(max_prefix, length - 1) + 1):
        prefix = word[:p]
        for s in range(max(1, length - p - max_suffix), length - p + 1):
            yield prefix, word[p : p + s], word[p + s :]


class Analyzer:
    """Morphological analyzer over one :class:`MorphologyDB`."""

    def __init__(self, db: MorphologyDB) -> None:
        self._db = db

    def _special(
        self, word: str, default: str, *, pos: str | None, bw: str, source: str, marker: str, catib6: str, ud: str
    ) -> dict[str, Any]:
        """The single analysis of a digit, punctuation or foreign word."""
        result = copy.copy(self._db.defaults[default])
        if pos is not None:
            result["pos"] = pos
        result["diac"] = word
        result["stem"] = word
        result["stemgloss"] = word
        result["stemcat"] = None
        result["lex"] = word
        result["bw"] = word + bw
        result["source"] = source
        for feat in _COPY_FEATS:
            if feat in self._db.defines:
                result[feat] = word
        for feat in _UNDEFINED_LEX_FEATS:
            if feat in self._db.defines:
                result[feat] = marker
        if "catib6" in self._db.defines:
            result["catib6"] = catib6
        if "ud" in self._db.defines:
            result["ud"] = ud
        result["pos_logprob"] = -99.0
        result["lex_logprob"] = -99.0
        result["pos_lex_logprob"] = -99.0
        if "form_gen" in self._db.defines and result["gen"] == "-":
            result["gen"] = result["form_gen"]
        if "form_num" in self._db.defines and result["num"] == "-":
            result["num"] = result["form_num"]
        return result

    def _combined(
        self, word_dediac: str, prefixes: list[Entry], stems: list[Entry], suffixes: list[Entry]
    ) -> list[dict[str, Any]]:
        db = self._db
        combined: list[dict[str, Any]] = []
        for (prefix_cat, prefix_feats), (stem_cat, stem_feats) in itertools.product(prefixes, stems):
            if stem_cat not in db.prefix_stem_compat.get(prefix_cat, ()):
                continue
            for suffix_cat, suffix_feats in suffixes:
                if (
                    stem_cat not in db.stem_suffix_compat
                    or prefix_cat not in db.prefix_suffix_compat
                    or suffix_cat not in db.stem_suffix_compat[stem_cat]
                    or suffix_cat not in db.prefix_suffix_compat[prefix_cat]
                ):
                    continue
                merged = merge_features(db, prefix_feats, stem_feats, suffix_feats)
                merged["stem"] = stem_feats["diac"]
                merged["stemcat"] = stem_cat
                if word_dediac.replace("\u0640", "") != dediac_ar(merged["diac"]):
                    merged["source"] = "spvar"
                combined.append(merged)
        return combined

    def analyze(self, word: str) -> list[dict[str, Any]]:
        """Every analysis of *word*; ``[]`` when the database licenses none."""
        word = word.strip()
        if word == "":
            return []
        if _is_digit(word):
            return [
                self._special(
                    word, "digit", pos=None, bw="/NOUN_NUM", source="digit", marker="DIGIT", catib6="NOM", ud="NUM"
                )
            ]
        if _is_punc(word):
            return [
                self._special(
                    word, "punc", pos=None, bw="/PUNC", source="punc", marker="PUNC", catib6="PNX", ud="PUNCT"
                )
            ]
        if _has_punc(word):
            return []
        if not _is_ar(word):
            return [
                self._special(
                    word,
                    "latin",
                    pos="foreign",
                    bw="/FOREIGN",
                    source="foreign",
                    marker="FOREIGN",
                    catib6="FOREIGN",
                    ud="X",
                )
            ]

        db = self._db
        word_dediac = dediac_ar(word)
        word_normal = word_dediac.translate(_NORMALIZE_TABLE)
        analyses: list[dict[str, Any]] = []
        for prefix, stem, suffix in _segments(word_normal, db.max_prefix_size, db.max_suffix_size):
            prefixes = db.prefix_hash.get(prefix)
            suffixes = db.suffix_hash.get(suffix)
            if prefixes is None or suffixes is None:
                continue
            stems = db.stem_hash.get(stem)
            if stems is not None:
                analyses.extend(self._combined(word_dediac, prefixes, stems, suffixes))
        return analyses
