"""Vietnamese text normalisation, script gate and subtitle/sentence data (spec C.4)."""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages.vi.tones import to_new_style, to_old_style

#: Spec C.4's class: the letters only Vietnamese spells (a-breve, a-circumflex, d-stroke,
#: e-circumflex, o-circumflex, o-horn, u-horn, both cases), the Latin-1 tone-marked vowels it
#: shares with Romance languages, and all of Latin Extended Additional's Vietnamese block
#: (U+1EA0 .. U+1EF9). The script gate reuses it.
_VI_LETTERS: frozenset[str] = frozenset("ăâđêôơư" "ĂÂĐÊÔƠƯ" "àáãèéìíòóõùúýĩũ" "ÀÁÃÈÉÌÍÒÓÕÙÚÝĨŨ") | frozenset(
    chr(code) for code in range(0x1EA0, 0x1EFA)
)
#: One unaccented Vietnamese syllable: optional onset, one to three vowels (glides included),
#: optional coda. Vietnamese has no f/j/w/z and no coda outside ``c ch m n ng nh p t``.
_VI_SYLLABLE_SHAPE = re.compile(
    r"(?:ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdghklmnprstvx])?[aeiouy]{1,3}(?:ch|ng|nh|[cmnpt])?"
)
#: A letter run: the unit the tone fold is allowed to touch. Digits and punctuation split runs.
_LETTER_RUN = re.compile(r"[^\W\d_]+")


def _ascii_skeleton(word: str) -> str:
    """The word with every Vietnamese diacritic removed: thuy from thuỷ, dep from đẹp."""
    stripped = "".join(char for char in unicodedata.normalize("NFD", word.lower()) if not unicodedata.combining(char))
    return stripped.replace("đ", "d")


def _is_vietnamese_spelling(word: str) -> bool:
    """Every letter is in the Vietnamese alphabet and the word is one well-formed syllable."""
    if any(not char.isascii() and char not in _VI_LETTERS for char in unicodedata.normalize("NFC", word)):
        return False
    return _VI_SYLLABLE_SHAPE.fullmatch(_ascii_skeleton(word)) is not None


def fold_tone_placement(text: str, *, new_style: bool = False) -> str:
    """Move tone marks to their canonical vowel, one letter run at a time.

    The viet_text_tools port cannot tell a Vietnamese tone mark from the tilde of a Spanish
    n-tilde or the acute of a French e-acute, so folding a whole cue rewrites foreign names
    into the stored sentence and onto the card. A run therefore keeps its original spelling
    unless its folded form is one well-formed Vietnamese syllable written only in the
    Vietnamese alphabet. Length is preserved either way (the tagging copy relies on it).
    """
    fold = to_new_style if new_style else to_old_style

    def _fold_run(match: re.Match[str]) -> str:
        word = match.group()
        folded = fold(word)
        if folded == word:
            return word
        return folded if _is_vietnamese_spelling(folded) else word

    return _LETTER_RUN.sub(_fold_run, text)


#: Runs of whitespace other than a newline: clean_subtitle_text strips annotations per
#: physical line after normalize, so line breaks must survive (its own final join flattens
#: them); a reading-tab unit gets no such join, so a double space inside a compound would
#: otherwise stop the engine's single-spaced word from matching the line.
_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
#: The Icelandic eth (U+00D0/U+00F0) typed for Vietnamese D-stroke (U+0110/U+0111): the same
#: glyph in most fonts, a different letter to every key and to the tokenizer.
ETH_REPAIR = str.maketrans({"Ð": "Đ", "ð": "đ"})


def vi_normalize(text: str) -> str:
    """P3 (S5): NFC, eth repair, word-scoped old-style tone placement, spaces collapsed.

    Case is untouched: the stored sentence keeps the subtitle's capitals. The fold is scoped
    per letter run, so a foreign name inside the cue survives verbatim.
    """
    text = unicodedata.normalize("NFC", text).translate(ETH_REPAIR)
    return _HORIZONTAL_WHITESPACE.sub(" ", fold_tone_placement(text))
