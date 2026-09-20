"""Vietnamese text normalisation, script gate and subtitle/sentence data (spec C.4)."""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
)
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.profile import ScriptFilterOption, SentenceRules
from anki_miner.languages.vi.syllables import COMMON_SYLLABLES
from anki_miner.languages.vi.tones import to_new_style, to_old_style
from anki_miner.utils.ja_normalize import is_cjk_ideograph

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


# --- S15: the two script gates --------------------------------------------------
#: Arabic (+ supplement, presentation forms), Thai, Hangul jamo/compatibility/syllables, kana.
#: CJK ideographs come from the shared is_cjk_ideograph.
_FOREIGN_BLOCKS: tuple[tuple[int, int], ...] = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x0E00, 0x0E7F),
    (0x1100, 0x11FF),
    (0x3040, 0x30FF),
    (0x3130, 0x318F),
    (0xAC00, 0xD7AF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)
_ASCII_WORD = re.compile(r"[a-z]+")
#: Share of a text's ASCII words that must be common syllables for an unaccented line to pass.
#: Two syllables therefore need both (1 >= 0.6 * 2 is false), which is what keeps a two-word
#: English deck front out even when one of its words collides with a Vietnamese syllable.
_SYLLABLE_SHARE = 0.6


def _is_foreign(char: str) -> bool:
    code = ord(char)
    return is_cjk_ideograph(char) or any(low <= code <= high for low, high in _FOREIGN_BLOCKS)


class VietnameseScript:
    """ScriptSupport: no script toggles; the gate is "a Vietnamese letter, or mostly common syllables".

    Any CJK, kana, Hangul, Thai or Arabic character fails the whole text. The syllable
    heuristic is what lets an unaccented line (and so an English deck front made of
    colliding syllables, ``ten``) through: S15's first-switch deck checklist and the scoped
    ``excluded_decks`` carry that load, as for every Latin-script language.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        text = unicodedata.normalize("NFC", text)
        if any(_is_foreign(char) for char in text):
            return False
        if any(char in _VI_LETTERS for char in text):
            return True
        words = _ASCII_WORD.findall(text.lower())
        return bool(words) and sum(word in COMMON_SYLLABLES for word in words) >= _SYLLABLE_SHARE * len(words)


def is_vietnamese_word(text: str) -> bool:
    """The parser's per-token gate: a Vietnamese letter, or every syllable a Vietnamese shape.

    ``contains_target_script`` judges whole texts (deck fronts, decoded files); a token
    inside a Vietnamese cue needs a different question, because many content words are
    plain ASCII (``mua``, ``phim``, ``nhanh``) and only 200 syllables are in the text gate's
    table. The syllable grammar still drops what the engine tags ``N`` inside a cue but
    is not Vietnamese (``Directionless``, ``OK``, ``internet``).

    The two gates therefore disagree by design: measured on 20,000 real cues, 8.8 % of
    distinct mineable fronts and 3.8 % of tokens are words this gate mines and the text
    gate does not read as Vietnamese, so they never reach the known-words DB. Aligning them
    would ingest English decks as Vietnamese (S15), which is the worse failure.
    """
    text = unicodedata.normalize("NFC", text)
    if not text or any(_is_foreign(char) for char in text):
        return False
    if any(char in _VI_LETTERS for char in text):
        return True
    pieces = text.lower().split()
    return bool(pieces) and all(_VI_SYLLABLE_SHAPE.fullmatch(piece) for piece in pieces)


# --- S10: the Vietnamese subtitle-cleanup default ------------------------------
#: Vietnamese capitals outside the shared class A-Z + Latin-1: the upper-case half of Latin
#: Extended Additional (even code points U+1EA0 .. U+1EF8) and the six letter-stroke capitals.
_VI_CAPITALS = "ĂĐĨŨƠƯ" + "".join(chr(code) for code in range(0x1EA0, 0x1EFA, 2))
_CAPITAL = f"A-ZÀ-ÖØ-Þ{_VI_CAPITALS}"
#: A speaker label in Vietnamese capitals - the shared Latin preset leaves it in the cue and
#: the parser mines it.
VI_SPEAKER_PATTERN = rf"^[{_CAPITAL}][{_CAPITAL}0-9 .'-]*[{_CAPITAL}]:\s*"
#: The S10 default for Vietnamese. No inline flags.
VI_SUBTITLE_REGEX = "|".join(
    (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, VI_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
)

# --- S8: reading-tab sentence rules ---------------------------------------------
#: Casefolded keys without the final dot: city/town/district/ward (Tp. Tx. Q. P.) and the
#: academic and professional titles written before a name (TS. ThS. GS. PGS. BS. KS.). Being
#: non-empty also switches on the ASCII ellipsis rule (``...`` does not end a sentence), which
#: spec C.4 asks for. underthesea's own punkt abbreviation list (1,032 noisy entries) is not used.
VI_ABBREVIATIONS: frozenset[str] = frozenset({"tp", "tx", "q", "p", "ts", "ths", "gs", "pgs", "bs", "ks"})
VI_SENTENCE_RULES: SentenceRules = sentence_rules(VI_ABBREVIATIONS)
