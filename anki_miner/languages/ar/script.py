"""Arabic script support: word ranges, the P3 normaliser, the key fold, the gate, sentence rules, SDH regex.

``ar_normalize`` is what the parser applies to every line: tashkeel and digits stay (a vocalised line keeps
its marks in the card sentence), tatweel and every format character but ZWNJ/ZWJ go (hermitdave's ignored
list carries 143 k RLE and 78 k RLM tokens), presentation forms decompose. ``ar_fold`` is the
dictionary-key and analysis-key fold: Yomitan's ``removeArabicScriptDiacritics`` set on top. Hamza
seats, alef maqsura and ta marbuta are never folded (minimal pairs), and the lookup ladder tries them
miss-only (``morphology.ArabicLookupStrategy``).
"""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages._spaced.script import BRACKETS_PATTERN, MUSIC_PATTERN, PARENS_PATTERN
from anki_miner.languages.profile import ScriptFilterOption, SentenceRules

#: Letters and in-word marks (spec C.1 tokenizer ranges): tatweel, tashkeel and the Quranic marks
#: included; the digit blocks U+0660-0669 / U+06F0-06F9 and the punctuation U+060C, U+061B, U+061F,
#: U+06D4 fall outside.
AR_WORD_RANGES: tuple[tuple[int, int], ...] = (
    (0x0610, 0x061A),
    (0x0620, 0x065F),
    (0x066E, 0x06D3),
    (0x06D5, 0x06EF),
    (0x06FA, 0x06FC),
    (0x06FF, 0x06FF),
    (0x0750, 0x077F),
    (0x0870, 0x089F),
    (0x08A0, 0x08FF),
)
TATWEEL = "\N{ARABIC TATWEEL}"
_JOINERS = frozenset("\N{ZERO WIDTH NON-JOINER}\N{ZERO WIDTH JOINER}")


def _char_class(ranges: tuple[tuple[int, int], ...]) -> str:
    return "[" + "".join(chr(low) if low == high else f"{chr(low)}-{chr(high)}" for low, high in ranges) + "]"


#: Yomitan's removeArabicScriptDiacritics set.
_DIACRITICS_RE = re.compile(_char_class(((0x0610, 0x061A), (0x064B, 0x065F), (0x0670, 0x0670))))
#: Arabic Presentation Forms-A and -B.
_PRESENTATION_RE = re.compile(_char_class(((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))))


def is_arabic_word_char(char: str) -> bool:
    """True for a letter or in-word mark of the Arabic blocks (the tokenizer's run class)."""
    code = ord(char)
    return any(low <= code <= high for low, high in AR_WORD_RANGES)


def is_arabic_letter(char: str) -> bool:
    """True for an alphabetic character of the Arabic blocks (the script gate)."""
    return char.isalpha() and is_arabic_word_char(char)


def _presentation_nfkc(text: str) -> str:
    return _PRESENTATION_RE.sub(lambda match: unicodedata.normalize("NFKC", match.group()), text)


def _strip_format(text: str, keep: frozenset[str] = frozenset()) -> str:
    return "".join(char for char in text if char in keep or unicodedata.category(char) != "Cf")


def ar_normalize(text: str) -> str:
    """P3: NFC, presentation forms NFKC'd, tatweel and every Cf but ZWNJ/ZWJ removed."""
    text = _presentation_nfkc(unicodedata.normalize("NFC", text)).replace(TATWEEL, "")
    return _strip_format(text, keep=_JOINERS)


def ar_fold(text: str) -> str:
    """The key fold: NFC, presentation forms NFKC'd, tashkeel, tatweel and every Cf removed. Idempotent."""
    text = _presentation_nfkc(unicodedata.normalize("NFC", text))
    return _strip_format(_DIACRITICS_RE.sub("", text).replace(TATWEEL, ""))


class ArabicScript:
    """ScriptSupport: no toggles; the gate is "has an Arabic letter".

    Persian, Urdu and Pashto decks pass it too (S15): the first-switch deck checklist and the scoped
    ``excluded_decks`` carry that load, as for every shared-script language.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        return False

    def contains_target_script(self, text: str) -> bool:
        return any(is_arabic_letter(char) for char in text)


class ArabicDictKeys:
    """DictKeyFolding: ``ar_fold`` term keys, NFC readings, Rule-A-only homograph mask (the ko/zh shape)."""

    def fold_term(self, s: str) -> str:
        return ar_fold(s)

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        exact = [term == word for term, _ in rows]
        if not any(exact):
            return [True] * len(rows)
        contents = {content for (_, content), hit in zip(rows, exact, strict=True) if hit}
        return [hit or content in contents for (_, content), hit in zip(rows, exact, strict=True)]


AR_SENTENCE_RULES = SentenceRules(
    terminators=frozenset(".!?‼⁉⁇⁈\N{ARABIC QUESTION MARK}\N{ARABIC FULL STOP}"),
    ellipses=frozenset("…‥"),
    openers=frozenset('«([{“"'),
    closers=frozenset('»)]}”"'),
    space_aware=True,
)

#: A dialogue dash at the cue start, or after a Latin or Arabic terminator and a space.
AR_DIALOGUE_DASH_PATTERN = r"(?:^|(?<=[.!?…\N{ARABIC QUESTION MARK}]\s))[-–—]\s+"
#: The SDH default: the Latin bracket/paren/music patterns (script-neutral) plus the Arabic dash rule.
#: No speaker-label pattern: Arabic has no capitals to tell a ``name:`` label from speech.
AR_SUBTITLE_REGEX = "|".join((BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, AR_DIALOGUE_DASH_PATTERN))
