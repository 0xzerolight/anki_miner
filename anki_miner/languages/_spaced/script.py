"""Latin, Greek and Cyrillic script gates for spaCy languages (and the Latin subtitle regex defaults)."""

from __future__ import annotations

import unicodedata

from anki_miner.languages.profile import ScriptFilterOption

#: Latin letter blocks beyond U+0000-024F (Basic Latin through Latin Extended-B):
#: Latin Extended Additional, Extended-C, Extended-D, Extended-E.
_EXTENDED_LATIN: tuple[tuple[int, int], ...] = ((0x1E00, 0x1EFF), (0x2C60, 0x2C7F), (0xA720, 0xA7FF), (0xAB30, 0xAB6F))


def is_latin_letter(char: str) -> bool:
    """True when *char* is an alphabetic character from a Latin block."""
    if not char.isalpha():
        return False
    code = ord(char)
    return code < 0x0250 or any(low <= code <= high for low, high in _EXTENDED_LATIN)


def nfc_normalize(text: str) -> str:
    """The Latin ``LanguageProfile.normalize`` (S5): NFC only — NFD subtitles compose, nothing else moves.

    A language with more (fr NBSP/NNBSP, ro comma-below) wraps this in its own function.
    """
    return unicodedata.normalize("NFC", text)


class LatinScript:
    """ScriptSupport: no script toggles; the ingestion/mining gate is "has a Latin letter".

    The gate cannot tell two Latin-script languages apart (S15): a French deck
    passes an English scan. The first-switch deck checklist and the scoped
    ``excluded_decks`` carry that load, not this class.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_latin_letter(char) for char in text)


#: Greek and Coptic, Greek Extended (polytonic). Letters only: U+037E (question mark), U+0387 (ano
#: teleia) and the numeral signs are not alphabetic, so they never make a line "Greek".
_GREEK_BLOCKS: tuple[tuple[int, int], ...] = ((0x0370, 0x03FF), (0x1F00, 0x1FFF))


def is_greek_letter(char: str) -> bool:
    """True when *char* is an alphabetic character from a Greek block."""
    if not char.isalpha():
        return False
    code = ord(char)
    return any(low <= code <= high for low, high in _GREEK_BLOCKS)


class GreekScript:
    """ScriptSupport for Greek (E.10 D3): no script toggles; the gate is "has a Greek letter".

    Unlike :class:`LatinScript` it tells its language apart from every other
    mining language: a Latin- or Cyrillic-script deck never passes a Greek scan.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_greek_letter(char) for char in text)


#: Cyrillic, Cyrillic Supplement, Extended-C, Extended-A, Extended-B. Letters only: the titlo and the
#: other combining marks (U+0483-0489) are not alphabetic, so they never make a line "Cyrillic".
_CYRILLIC_BLOCKS: tuple[tuple[int, int], ...] = (
    (0x0400, 0x04FF),
    (0x0500, 0x052F),
    (0x1C80, 0x1C8F),
    (0x2DE0, 0x2DFF),
    (0xA640, 0xA69F),
)


def is_cyrillic_letter(char: str) -> bool:
    """True when *char* is an alphabetic character from a Cyrillic block."""
    if not char.isalpha():
        return False
    code = ord(char)
    return any(low <= code <= high for low, high in _CYRILLIC_BLOCKS)


class CyrillicScript:
    """ScriptSupport for Cyrillic languages (ru, uk): no script toggles; the gate is "has a Cyrillic letter".

    Like :class:`LatinScript` it cannot tell two languages of its script apart (S15): a Bulgarian or
    Serbian deck passes a Russian scan. The first-switch deck checklist and the scoped
    ``excluded_decks`` carry that load, not this class.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_cyrillic_letter(char) for char in text)


# --- S10: the Latin subtitle-cleanup default ---------------------------------
# Applied per cue after markup strip and whitespace flattening, so ``^`` is the
# cue start. No inline flags: presets are ``|``-joined, and a global flag not at
# position 0 is a hard ``re.error`` that the parser swallows into "filter
# disabled". The GUI keeps its own literal of the dash preset (gui never imports
# _spaced); tests/unit/languages/test_spaced_subtitle_regex.py pins the two equal.
BRACKETS_PATTERN = r"\[[^\]]*\]"
PARENS_PATTERN = r"\([^)]*\)"
MUSIC_PATTERN = r"[♪♫♬]+"
#: ``JOHN:``, ``DR. SMITH:`` — two or more capitals (Latin-1 included) then a colon at the cue start.
LATIN_SPEAKER_PATTERN = r"^[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9 .'-]*[A-ZÀ-ÖØ-Þ]:\s*"
#: ``- Hi. - Hello.``: a dash that opens a speaker turn — at the cue start or after a sentence
#: terminator and a space — followed by whitespace. A mid-sentence spaced dash (``sagte sie – wirklich``,
#: ``I was — well — tired``) is punctuation the card sentence keeps (NOTE 013).
DIALOGUE_DASH_PATTERN = r"(?:^|(?<=[.!?…]\s))[-–—]\s+"
#: The Nordic variant: Swedish, Norwegian and Danish subtitles write the second speaker's dash unspaced
#: (``-Kom hit.``), where the rule above needs a space and spaCy glues ``-Kom`` into one PUNCT token, losing the
#: word. Requiring a letter after an unspaced dash keeps a negative number intact (``-5 grader ute.``).
NORDIC_DIALOGUE_DASH_PATTERN = r"(?:^|(?<=[.!?…]\s))[-–—](?:\s+|(?=[^\W\d_]))"
LATIN_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LATIN_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)
