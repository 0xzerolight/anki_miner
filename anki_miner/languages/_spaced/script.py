"""Latin script gate for spaCy languages (and, from Task 4, the Latin subtitle regex defaults)."""

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
LATIN_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, LATIN_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)
