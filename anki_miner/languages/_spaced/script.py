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
