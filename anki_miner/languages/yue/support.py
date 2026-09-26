"""yue script gate, dictionary-key folding, mined-form policy, lookup ladder.

The shapes are zh's, REIMPLEMENTED rather than imported (R32): the only symbols
this package takes from elsewhere are ``utils.ja_normalize``'s two shared folds
and ``zh.render.ZhMeasureWordHook``.
"""

from __future__ import annotations

from anki_miner.languages.profile import ScriptFilterOption
from anki_miner.languages.yue.normalize import fold_term_yue, normalize_yue
from anki_miner.languages.yue.variants import hk_variant_candidates
from anki_miner.utils.ja_normalize import is_cjk_ideograph, normalize_radicals


class YueScriptSupport:
    """No script toggles; the ingestion gate is "contains a Han ideograph"."""

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_cjk_ideograph(char) for char in text)


class YueDictKeyFolding:
    """Folded term keys, casefolded jyutping reading keys, Rule-A homograph scope."""

    def fold_term(self, s: str) -> str:
        return fold_term_yue(s)

    def fold_reading(self, s: str | None) -> str | None:
        """Fold a jyutping reading key: the term fold, then casefold.

        Both catalogue rows spell jyutping lower-case and syllable-spaced
        (measured: ``jat1 gin6 waan4 jat1 gin6``), which is exactly what
        ``characters_to_jyutping`` emits, so the casefold is a belt for imported
        data that capitalises. Safe only because it is SYMMETRIC: the importer
        folds a row's reading key with this function before writing it and every
        lookup folds the query the same way. Fold on one side only and the row
        is there and is never found.
        """
        return fold_term_yue(s).casefold() if s is not None else None

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule A of ``storage._homograph_keep_mask``, and nothing else.

        Rule A' (the tokenizer-lemma tier) could never fire: a Cantonese lemma
        is its own spelling. Rule B is a kana filter and there is no kana.
        ``lemma`` is accepted to keep the one cross-language signature.
        """
        term_exact = [term == word for term, _ in rows]
        if not any(term_exact):
            return [True] * len(rows)
        exact_contents = {content for (_, content), keep in zip(rows, term_exact, strict=True) if keep}
        return [keep or content in exact_contents for (_, content), keep in zip(rows, term_exact, strict=True)]

    def dedup_fold(self, s: str) -> str:
        """Duplicate-card key. yue is traditional-only, so the term key IS the card key."""
        return fold_term_yue(s)


class YueMinedFormPolicy:
    """The segmented word, LEMMA first.

    The one deliberate difference from ``ZhMinedFormPolicy`` (``zh/support.py:77``,
    ``surface or lemma or orth_base``): a yue surface is the verbatim slice of
    the line, which carries an interior space when the segmenter joined across
    one (``今 日``). The lemma is the space-free spelling and is what belongs on
    the card front. Identity otherwise -- Cantonese is isolating.
    """

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return lemma or surface or orth_base


class YueLookupStrategy:
    """Spelling variants of a query, ``conditions=0`` (no deinflection).

    Two rungs, both pure spelling. First the radical-normalised form, which
    matters only against an index built from OCR or legacy sources that
    substituted a Kangxi radical glyph for the ideograph. Then the Hong Kong
    variant pairs, both ways. No per-character fallback (zh has none either), so
    a multi-character miss yields no card; dictionary-driven decompounding and
    glued-particle joins stay deferred (spec section 9).

    The ``0`` is the Yomitan deinflection bitmask value
    ``DefinitionService._fallback_candidates`` already uses for pure spelling
    variants. ``orth_base`` and ``ctype`` are part of the one cross-language
    signature and are unused here.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        out = [normalize_radicals(normalize_yue(word)), *hk_variant_candidates(word)]
        return [(candidate, 0) for candidate in dict.fromkeys(out) if candidate and candidate != word]
