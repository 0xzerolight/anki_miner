"""Hebrew script support: the character ranges, the P3 normaliser, the key fold, the gate, sentence rules, SDH regex.

``he_normalize`` is what the parser applies to every line. Niqqud STAYS: vocalised children's
content and Tanakh text keep their points in the stored card sentence (S5), and it is the fold at
the comparison and lookup seams -- not the normaliser -- that makes a pointed spelling and a bare
one meet. NBSP becomes a space and every format character but ZWNJ/ZWJ goes, which is what removes
the RLM/RLE Windows subtitle tools inject (the ar precedent).

``he_fold`` is the dictionary-key and comparison fold (R33): NFC, drop every ``Cf``, drop every
combining mark of the Hebrew block. The maqaf, geresh and gershayim are kept -- they spell
construct forms and abbreviations, and ``kelev-`` resolves through the dictionary's own form table
-- and a final letter is never folded to its medial form.

**Every codepoint bound here is an integer over ``ord``, never a character range and never a
backslash-u escape.** The harness decodes such an escape inside a comparison into the literal
character; the result is visible, so no scan catches it, and the next reader cannot see what the
bound is. ``ar/script.py`` is the shipped precedent for the ordinal-tuple plus class-body shape.
"""

from __future__ import annotations

import unicodedata

from anki_miner.languages._spaced.script import BRACKETS_PATTERN, MUSIC_PATTERN, PARENS_PATTERN
from anki_miner.languages.profile import ScriptFilterOption, SentenceRules

#: The COMBINING marks of the Hebrew block. U+0591-U+05C7 is NOT one run of them: U+05BE MAQAF is
#: ``Pd``, and U+05C0 PASEQ, U+05C3 SOF PASUQ and U+05C6 NUN HAFUKHA are ``Po``. Putting the raw
#: range in the tokenizer's word class made a sof-pasuq-terminated word one token and carried the
#: terminator onto the card front. These 51 codepoints are exactly the ``Mn`` members.
HE_MARK_RANGES: tuple[tuple[int, int], ...] = (
    (0x0591, 0x05BD),
    (0x05BF, 0x05BF),
    (0x05C1, 0x05C2),
    (0x05C4, 0x05C5),
    (0x05C7, 0x05C7),
)

#: The Hebrew letters: the alphabet with its five final forms, plus the three Yiddish ligatures.
#: A Yiddish deck therefore passes the gate, which is what the S15 first-switch checklist is for.
HE_LETTER_RANGES: tuple[tuple[int, int], ...] = ((0x05D0, 0x05EA), (0x05EF, 0x05F2))

#: R33's fold window: the whole block, narrowed to ``Mn`` at the category test.
HE_POINT_WINDOW: tuple[int, int] = (0x0591, 0x05C7)

MAQAF = "\N{HEBREW PUNCTUATION MAQAF}"
GERESH = "\N{HEBREW PUNCTUATION GERESH}"
GERSHAYIM = "\N{HEBREW PUNCTUATION GERSHAYIM}"
SOF_PASUQ = "\N{HEBREW PUNCTUATION SOF PASUQ}"
NBSP = "\N{NO-BREAK SPACE}"
_JOINERS = frozenset("\N{ZERO WIDTH NON-JOINER}\N{ZERO WIDTH JOINER}")


def _class_body(ranges: tuple[tuple[int, int], ...]) -> str:
    """One regex class body built FROM the ordinals, so the two cannot drift (ar's ``_char_class``)."""
    return "".join(chr(low) if low == high else f"{chr(low)}-{chr(high)}" for low, high in ranges)


#: The tokenizer's in-word mark class (``languages/he/tokenizer.py``), derived, never hand-written.
HE_MARK_CLASS = _class_body(HE_MARK_RANGES)


def _in_ranges(char: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in ranges)


def is_he_mark(char: str) -> bool:
    """True for a combining mark of the Hebrew block (the tokenizer's in-word class)."""
    return _in_ranges(char, HE_MARK_RANGES)


def is_he_letter(char: str) -> bool:
    """True for a Hebrew letter, final forms and Yiddish ligatures included (the script gate)."""
    return _in_ranges(char, HE_LETTER_RANGES)


def he_normalize(text: str) -> str:
    """S5: NFC, NBSP to a space, every ``Cf`` but ZWNJ/ZWJ removed. One line in, one line out.

    Niqqud, the maqaf, the geresh and the gershayim all stay: the card stores the line as it was
    written, and the fold at the lookup and comparison seams is what makes the keys meet.

    Whitespace is NOT collapsed, though the spec's prose says it is. Surfaces are slices of the
    normalised line and a cue's own line breaks are what the sentence splitter reads, so collapsing
    them here would change spans for no gain; ``ar_normalize`` and ``fa_normalize`` both leave
    whitespace alone for the same reason. Idempotent by construction.
    """
    text = unicodedata.normalize("NFC", text).replace(NBSP, " ")
    return "".join(char for char in text if char in _JOINERS or unicodedata.category(char) != "Cf")


def he_fold(text: str) -> str:
    """R33: NFC, every ``Cf`` dropped, every Hebrew combining mark dropped. Idempotent."""
    low, high = HE_POINT_WINDOW
    folded = []
    for char in unicodedata.normalize("NFC", text):
        category = unicodedata.category(char)
        if category == "Cf":
            continue
        if category == "Mn" and low <= ord(char) <= high:
            continue
        folded.append(char)
    return "".join(folded)


class HebrewScript:
    """ScriptSupport: no toggles; the gate is "has a Hebrew letter".

    Yiddish and Judaeo-Arabic decks pass it too (S15): the first-switch deck checklist and the
    scoped ``excluded_decks`` carry that load, as for every shared-script language.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_he_letter(char) for char in text)


class HebrewDictKeys:
    """DictKeyFolding: ``he_fold`` term keys, NFC readings, Rule-A-only homograph mask (the ko/zh shape).

    ``fold_reading`` is a passthrough in practice: the reading column is empty in every row of
    ``wty-he-en``, so nothing is ever keyed on it.
    """

    def fold_term(self, s: str) -> str:
        return he_fold(s)

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        exact = [term == word for term, _ in rows]
        if not any(exact):
            return [True] * len(rows)
        contents = {content for (_, content), hit in zip(rows, exact, strict=True) if hit}
        return [hit or content in contents for (_, content), hit in zip(rows, exact, strict=True)]


HE_SENTENCE_RULES = SentenceRules(
    terminators=frozenset(".!?" + SOF_PASUQ),
    ellipses=frozenset("…‥"),
    openers=frozenset("([{“„«'"),
    closers=frozenset(")]}”»'"),
    space_aware=True,
)
"""S8. ``abbreviations`` stays empty and the dot mechanism is inert for Hebrew: every Hebrew
abbreviation is built from a gershayim or a geresh, carries no dot, and is one token under the
tokenizer regex, so the mechanism never sees one."""

#: A dialogue dash at the cue start, or after a Latin or Hebrew terminator and a space.
HE_DIALOGUE_DASH_PATTERN = r"(?:^|(?<=[.!?…" + SOF_PASUQ + r"]\s))[-–—]\s+"
#: The SDH default: the script-neutral bracket/paren/music patterns plus the Hebrew dash rule.
#: No speaker-label pattern: Hebrew has no capitals to tell a ``name:`` label from speech.
HE_SUBTITLE_REGEX = "|".join((BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, HE_DIALOGUE_DASH_PATTERN))
