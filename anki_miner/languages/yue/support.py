"""yue script gate, dictionary-key folding, mined-form policy, lookup ladder.

The shapes are zh's, REIMPLEMENTED rather than imported (R32): the only symbols
this package takes from elsewhere are ``utils.ja_normalize``'s two shared folds
and ``zh.render.ZhMeasureWordHook``.
"""

from __future__ import annotations

import re

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


# zh's CC-CEDICT row rank (``zh/support.py``, ``ZhDictKeyFolding.sense_rank``),
# copied. CC-CEDICT Canto is CC-CEDICT with jyutping, rendered into the same
# ``gloss-sc-li`` items, so it files the same surname and pointer rows ahead of
# the sense rows: 仲 opens on "surname Zhong" and 平 on "surname Ping". Measured
# over its 166,233 rows: 1,360 rank 1, 8,141 rank 2. CC-Canto writes its
# surnames "a surname" / "(noun) Chinese surname" and wty-yue-en has no such
# rows, so every row of both ranks 0 and keeps its index order.
_GLOSS_ITEM = re.compile(r'<li class="gloss-sc-li">(.*?)</li>', re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
_SURNAME_GLOSS = re.compile(r"surname [A-Z]")
_CROSS_REFERENCE_GLOSS = re.compile(
    r"(?:old |archaic |erhua |Japanese |\(old\) )?variant of\s|erhua form of\s|see\s|used in\s",
    re.IGNORECASE,
)
_REGISTER_MARKER = r"\((?:old|archaic|arch\.|classical|literary|obsolete)\)"
_ARCHAIC_GLOSS = re.compile(rf"^{_REGISTER_MARKER}|{_REGISTER_MARKER}$", re.IGNORECASE)


def _points_elsewhere(gloss: str) -> bool:
    """True iff ``gloss`` only names another entry (a Han referent, not "see you next time")."""
    return bool(_CROSS_REFERENCE_GLOSS.match(gloss)) and any(is_cjk_ideograph(char) for char in gloss)


def _states_no_live_sense(gloss: str) -> bool:
    """True iff ``gloss`` is a family name, a register-marked survival or a pointer."""
    return bool(_SURNAME_GLOSS.match(gloss)) or bool(_ARCHAIC_GLOSS.search(gloss)) or _points_elsewhere(gloss)


class YueDictKeyFolding:
    """Folded term keys, casefolded jyutping reading keys, Rule-A homograph scope, CEDICT row rank."""

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

    def sense_rank(self, content: str, tags: str, pos: str | None) -> int:
        """Where this row sorts among the rows sharing its reading priority.

        ``0`` for a row stating a live sense; ``1`` when its every gloss is a
        family name or a register-marked survival; ``2`` when its every gloss
        only points at another entry. Read by ``storage._sense_rank_fn``: it
        reorders, drops nothing. A row mixing a surname with a sense ("surname
        Wang; king") ranks 0, and so does content with no glossary items.
        ``tags`` and ``pos`` belong to the one cross-language signature and are
        unused: CC-CEDICT rows carry no part-of-speech tag.
        """
        glosses = [_HTML_TAG.sub("", item).strip() for item in _GLOSS_ITEM.findall(content)]
        present = [gloss for gloss in glosses if gloss]
        if not present or not all(_states_no_live_sense(gloss) for gloss in present):
            return 0
        return 2 if all(_points_elsewhere(gloss) for gloss in present) else 1


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
    variant pairs, both ways. No per-character fallback (zh has none either): a
    glued multi-character miss is split upstream, by the parser's
    ``YueDecompoundPass``, into the words it is made of when the dictionary
    attests every one, and a miss it cannot split yields no card.

    The ``0`` is the Yomitan deinflection bitmask value
    ``DefinitionService._fallback_candidates`` already uses for pure spelling
    variants. ``orth_base`` and ``ctype`` are part of the one cross-language
    signature and are unused here.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        out = [normalize_radicals(normalize_yue(word)), *hk_variant_candidates(word)]
        return [(candidate, 0) for candidate in dict.fromkeys(out) if candidate and candidate != word]
