"""Korean script classification, normalisation, sentence rules, dict folding.

NFC normalisation is not cosmetic: macOS filenames and some subtitle sources
carry NFD hangul, where 학 is three conjoining jamo. Un-composed text compares
unequal to every dictionary key, so normalisation runs before any lookup,
storage or known-word comparison.

contains_target_script is the ingestion/mining gate: Korean accepts hangul OR
han, because Sino-Korean words appear in hanja in subtitles and dictionaries and
a hangul-only gate would silently drop them.
"""

from __future__ import annotations

import unicodedata

from anki_miner.languages.profile import ScriptFilterOption, SentenceRules
from anki_miner.utils.ja_normalize import is_cjk_ideograph

#: Hangul syllables plus every jamo block: conjoining (U+1100-11FF),
#: compatibility (U+3130-318F), extended-A (U+A960-A97F), extended-B
#: (U+D7B0-D7FF). Decomposed text hits the jamo blocks, not the syllable block.
#: No shared helper covers hangul, so the table lives here.
_HANGUL_RANGES: tuple[tuple[int, int], ...] = (
    (0xAC00, 0xD7A3),
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xD7B0, 0xD7FF),
)


def is_hangul(char: str) -> bool:
    """True when *char* is a hangul syllable or jamo."""
    code = ord(char)
    return any(low <= code <= high for low, high in _HANGUL_RANGES)


def is_hanja(char: str) -> bool:
    """True when *char* is a CJK ideograph.

    Delegates to the shared ported range table (utils/ja_normalize.py:126)
    rather than keeping a second copy: it already carries U+F900-FAFF, the
    compatibility block where Korean sources put the dual-reading hanja
    (樂 = U+F914 락 / U+F95C 악), plus every extension block. Ideograph
    membership is language-neutral; only the name here is Korean.
    """
    return is_cjk_ideograph(char)


def ko_normalize(text: str) -> str:
    """Normalise Korean text to NFC (jamo composition)."""
    return unicodedata.normalize("NFC", text)


#: Korean prose mixes CJK terminators (rips from CJK tooling) with the ASCII set,
#: and unlike Japanese it is space-delimited, so the splitter rejoins fragments
#: on spaces instead of concatenating them bare.
KO_SENTENCE_RULES = SentenceRules(
    terminators=frozenset("。｡！？!?‼⁉⁇⁈."),
    ellipses=frozenset("…‥"),
    openers=frozenset("「｢『（〔［｛〈《【([{｟〝"),
    closers=frozenset("」｣』）〕］｝〉》】)]}｠〟"),
    space_aware=True,
)


class KoreanScript:
    """ScriptSupport for Korean.

    The two boolean config fields are the language-scoped script-exclusion
    slots; their names are JA-historical (they are in LANGUAGE_SCOPED_FIELDS and
    each profile decides what its own options mean), and no new setting is
    introduced for Korean.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        """Script filters offered in Settings -> Filtering for Korean."""
        return (
            ScriptFilterOption(
                option_id="hangul_only",
                label="Exclude hangul-only words",
                config_field="exclude_hiragana_only_words",
            ),
            ScriptFilterOption(
                option_id="hanja_containing",
                label="Exclude words containing hanja",
                config_field="exclude_katakana_only_words",
            ),
        )

    def matches(self, option_id: str, form: str) -> bool:
        """True when *form* belongs to the class named by *option_id*."""
        text = ko_normalize(form)
        if not text:
            return False
        if option_id == "hangul_only":
            return all(is_hangul(c) for c in text)
        if option_id == "hanja_containing":
            return any(is_hanja(c) for c in text)
        return False

    def contains_target_script(self, text: str) -> bool:
        """True when *text* contains hangul or hanja."""
        return any(is_hangul(c) or is_hanja(c) for c in text)


class KoreanDictKeys:
    """DictKeyFolding for Korean: NFC only, Rule-A-only homograph mask.

    No kana ladder and no Rule B: Japanese Rules A'/B reconcile kana spellings
    against kanji headwords, which has no Korean analogue. Rule A - term-exact
    rows win, plus the same-content carve-out that preserves the dedup-before-cap
    tag union - is the whole policy. Arity and return mirror
    services/dictionary/storage.py:247 exactly, ``lemma`` default included.
    """

    def fold_term(self, s: str) -> str:
        """Fold a lookup/storage term key."""
        return unicodedata.normalize("NFC", s)

    def fold_reading(self, s: str | None) -> str | None:
        """Fold a reading key (Korean readings are hangul; NFC only)."""
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule-A-only keep mask aligned to *rows* ((term, content) pairs)."""
        term_exact = [term == word for term, _ in rows]
        if not any(term_exact):
            return [True] * len(rows)
        exact_contents = {content for (_, content), ex in zip(rows, term_exact, strict=True) if ex}
        return [ex or content in exact_contents for (_, content), ex in zip(rows, term_exact, strict=True)]
