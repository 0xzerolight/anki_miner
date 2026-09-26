"""th script gate, dictionary-key folding, mined-form policy, lookup ladder.

Thai has no inflection and no kana, so the policies stay small: the mined form
is the newmm segment as written, term keys fold with NFC + vowel reordering, and
the settings script-filter section has no options.
"""

from __future__ import annotations

import unicodedata

from anki_miner.languages.profile import ScriptFilterOption
from anki_miner.languages.th.normalize import fold_term_th, normalize_th
from anki_miner.languages.th.tiers import TH_MARK_CODE_POINTS

#: Thai letters and vowel/tone signs, MINUS the marks. U+0E2F PAIYANNOI and
#: U+0E46 MAIYAMOK sit INSIDE those two ranges (measured) and are not letters, so
#: a bare maiyamok or paiyannoi must not pass the ingestion gate. Digits
#: (U+0E50-U+0E59) are outside the ranges already. ``tokenizer._THAI_LETTERS``
#: subtracts exactly the same set, from the same constant -- one decision,
#: stated in both modules.
_THAI_LETTERS = frozenset(chr(cp) for cp in [*range(0x0E01, 0x0E3B), *range(0x0E40, 0x0E4F)]) - TH_MARK_CODE_POINTS
_PAIYANNOI = "\N{THAI CHARACTER PAIYANNOI}"


class ThaiScriptSupport:
    """No script toggles; the ingestion gate is "contains a Thai letter"."""

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(char in _THAI_LETTERS for char in text)


class ThaiDictKeyFolding:
    """NFC + reordered vowels for terms, casefolded Latin for Paiboon readings."""

    def fold_term(self, s: str) -> str:
        return fold_term_th(s)

    def fold_reading(self, s: str | None) -> str | None:
        """Paiboon and IPA readings are Latin, so the reading key casefolds.

        Safe only because it is applied SYMMETRICALLY -- the importer folds a
        row's reading key with this function before writing it and every lookup
        folds the query the same way. Fold on one side only and the row is there
        and is never found.
        """
        return fold_term_th(s).casefold() if s is not None else None

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule A of ``storage._homograph_keep_mask``, and nothing else.

        Rule A' (the tokenizer-lemma tier) could never fire: a Thai lemma IS its
        surface. Rule B is a kana filter and there is no kana.
        """
        term_exact = [term == word for term, _ in rows]
        if not any(term_exact):
            return [True] * len(rows)
        exact_contents = {content for (_, content), keep in zip(rows, term_exact, strict=True) if keep}
        return [keep or content in exact_contents for (_, content), keep in zip(rows, term_exact, strict=True)]

    def dedup_fold(self, s: str) -> str:
        """Duplicate-card key: the term key minus every format character.

        The stored sentence keeps its zero-width word breaks; a card key must
        not, or the same word mined from an e-book and from a subtitle would
        make two cards.
        """
        return "".join(char for char in fold_term_th(s) if unicodedata.category(char) != "Cf")


class ThaiMinedFormPolicy:
    """The newmm segment as written.

    No inflection, so the front is the surface. A trailing paiyannoi is KEPT:
    the abbreviated spelling of Bangkok is the headword every dictionary lists,
    and newmm emits it as one token.
    """

    def mined_form(
        self,
        pos: str | None,
        orth_base: str,
        lemma: str,
        surface: str,
        pronunciation: str | None = None,
    ) -> str:
        return surface or lemma or orth_base


class ThaiLookupStrategy:
    """Spelling variants of a query, ``conditions=0`` (no deinflection).

    Two rungs: the abbreviation mark stripped (some dictionaries file the
    abbreviated headword without it), and the normalised spelling when the raw
    one differs (imported data carries unreordered vowel sequences). The ``0``
    is the Yomitan deinflection bitmask value ``DefinitionService
    ._fallback_candidates`` already uses for pure spelling variants.
    """

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        out: list[str] = []
        if word.endswith(_PAIYANNOI):
            out.append(word.rstrip(_PAIYANNOI))
        out.append(normalize_th(word))
        return [(candidate, 0) for candidate in dict.fromkeys(out) if candidate and candidate != word]
