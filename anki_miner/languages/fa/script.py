"""Persian character tables, normalisation, folding and the script gate (spec C.2).

Every Persian character in this file is a ``\\N{NAME}`` escape and every pattern
is built from named letter constants: the authoring tools decode a backslash-u
escape into the literal character, which would put invisible right-to-left text
into a source file (LEAD-BRIEF section 3).

The spacing rules are hazm 0.12.1's own tables, transcribed rather than
paraphrased -- ``constants.py`` ``EXTRA_SPACE_PATTERNS`` /
``AFFIX_SPACING_PATTERNS`` / ``PUNCTUATION_SPACING_PATTERNS`` and
``normalizer.py``'s ``seperate_mi`` -- and they run in hazm's own order
(``correct_spacing`` = extra space, affix, punctuation; then the repeated-letter
collapse; then ``seperate_mi``). hazm's ``PERSIAN_STYLE_PATTERNS``,
``UNICODE_REPLACEMENTS`` and the number tables are deliberately NOT ported: they
rewrite the user's text (ASCII quotes to guillemets, Latin digits to Persian
ones), which a mining pipeline must never do to a subtitle line.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from anki_miner.languages._spaced.script import BRACKETS_PATTERN, MUSIC_PATTERN, PARENS_PATTERN
from anki_miner.languages.profile import ScriptFilterOption, SentenceRules

ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
ZWJ = "\N{ZERO WIDTH JOINER}"
TATWEEL = "\N{ARABIC TATWEEL}"
_JOINERS = frozenset((ZWNJ, ZWJ))

#: Arabic-script letters and in-word marks (the tokenizer's run class).
FA_WORD_RANGES: tuple[tuple[int, int], ...] = (
    (0x0620, 0x063F),
    (0x0641, 0x064A),
    (0x066E, 0x066F),
    (0x0671, 0x06D3),
    (0x06D5, 0x06D5),
    (0x06E5, 0x06E6),
    (0x06EE, 0x06EF),
    (0x06FA, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
)

#: Letter unification shared by ``fa_normalize`` and ``fa_fold`` (hazm's table, Persian subset).
LETTER_MAP = {
    "\N{ARABIC LETTER YEH}": "\N{ARABIC LETTER FARSI YEH}",
    "\N{ARABIC LETTER ALEF MAKSURA}": "\N{ARABIC LETTER FARSI YEH}",
    "\N{ARABIC LETTER YEH BARREE}": "\N{ARABIC LETTER FARSI YEH}",
    "\N{ARABIC LETTER KAF}": "\N{ARABIC LETTER KEHEH}",
    "\N{ARABIC LETTER SWASH KAF}": "\N{ARABIC LETTER KEHEH}",
    "\N{ARABIC LETTER TEH MARBUTA}": "\N{ARABIC LETTER HEH}",
    "\N{ARABIC LETTER AE}": "\N{ARABIC LETTER HEH}",
    "\N{ARABIC LETTER HEH DOACHASHMEE}": "\N{ARABIC LETTER HEH}",
    "\N{ARABIC LETTER HEH GOAL}": "\N{ARABIC LETTER HEH}",
    "\N{ARABIC LETTER HEH GOAL WITH HAMZA ABOVE}": "\N{ARABIC LETTER HEH}",
    "\N{ARABIC LETTER TEH MARBUTA GOAL}": "\N{ARABIC LETTER HEH}",
    "\N{ARABIC LETTER ALEF WASLA}": "\N{ARABIC LETTER ALEF}",
    "\N{ARABIC LETTER KEHEH WITH THREE DOTS ABOVE}": "\N{ARABIC LETTER KEHEH}",
}
_LETTER_TABLE = {ord(src): dst for src, dst in LETTER_MAP.items()}

#: Marks dropped by BOTH folds: harakat, superscript alef, Quranic annotation, tatweel.
_MARKS = (
    (0x064B, 0x0652),
    (0x0656, 0x065F),
    (0x0670, 0x0670),
    (0x06D6, 0x06ED),
    (0x0640, 0x0640),
)
#: Dropped by ``fa_fold`` only: madda, hamza above/below (so heh+hamza folds onto plain heh).
_KEY_MARKS = ((0x0653, 0x0655),)


def _char_class(ranges: tuple[tuple[int, int], ...]) -> str:
    return "[" + "".join(chr(lo) if lo == hi else f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]"


_MARKS_RE = re.compile(_char_class(_MARKS))
_KEY_MARKS_RE = re.compile(_char_class(_MARKS + _KEY_MARKS))
_PRESENTATION_RE = re.compile(_char_class(((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))))
HEH_WITH_YEH = "\N{ARABIC LETTER HEH WITH YEH ABOVE}"

# --- the letters the ported patterns are written from ---------------------
_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_TEH = "\N{ARABIC LETTER TEH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_SHEEN = "\N{ARABIC LETTER SHEEN}"
_GAF = "\N{ARABIC LETTER GAF}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"
#: mi (MEEM + FARSI YEH), the imperfective prefix ``seperate_mi`` and the affix rule are about.
_MI = _MEEM + _YEH

# hazm constants.py:7-8. PUNC_AFTER's first three characters are the escaped dot
# and the colon, which its own patterns slice apart (``PUNC_AFTER[:3]``).
_PUNC_AFTER_HEAD = r"\.:"
_PUNC_AFTER_TAIL = (
    "!"
    "\N{ARABIC COMMA}"
    "\N{ARABIC SEMICOLON}"
    "\N{ARABIC QUESTION MARK}"
    "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    r"\]\)\}"
)
_PUNC_AFTER = _PUNC_AFTER_HEAD + _PUNC_AFTER_TAIL
_PUNC_BEFORE = "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}" + r"\[\(\{"
#: hazm spells the digit-spacing rules with a literal 32-letter alphabet; the
#: range class already in this module is the same set plus the Arabic-only
#: letters, which unification has folded away by the time these run.
_FA_LETTER_CLASS = _char_class(FA_WORD_RANGES)

# hazm constants.py:10-20 (EXTRA_SPACE_PATTERNS), verbatim.
_EXTRA_SPACE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^ +| +$", ""),
    (r" {2,}", " "),
    (r"\n{3,}", "\n\n"),
    (ZWNJ + "{2,}", ZWNJ),
    (ZWNJ + "{1,} ", " "),
    (" " + ZWNJ + "{1,}", " "),
    (r"\b" + ZWNJ + r"*\B", ""),
    (r"\B" + ZWNJ + r"*\b", ""),
    ("[" + TATWEEL + r"\r]", ""),
)

# hazm constants.py:36-54 (AFFIX_SPACING_PATTERNS), verbatim.
_AFFIX_SPACING_PATTERNS: tuple[tuple[str, str], ...] = (
    (f"([^ ]{_HEH}) {_YEH} ", f"\\1{ZWNJ}{_YEH} "),
    (f"(^| )({_NOON}?{_MI}) ", f"\\1\\2{ZWNJ}"),
    (
        f"(?<=[^\\n\\d {_PUNC_AFTER}{_PUNC_BEFORE}]{{2}}) "
        f"({_TEH}{_REH}({_YEH}{_NOON}?)?|{_GAF}{_REH}{_YEH}?|{_HEH}{_ALEF}{_YEH}?)"
        f"(?=[ \\n{_PUNC_AFTER}{_PUNC_BEFORE}]|$)",
        f"{ZWNJ}\\1",
    ),
    (
        f"([^ ]{_HEH}) ({_ALEF}({_MEEM}|{_YEH}{_MEEM}|{_SHEEN}|{_NOON}{_DAL}|"
        f"{_YEH}|{_YEH}{_DAL}|{_TEH}))(?=[ \\n{_PUNC_AFTER}]|$)",
        f"\\1{ZWNJ}\\2",
    ),
    (f"({_HEH})({_HEH}{_ALEF})", f"\\1{ZWNJ}\\2"),
)

# hazm constants.py:22-34 (PUNCTUATION_SPACING_PATTERNS), verbatim.
_PUNCTUATION_SPACING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r'" ([^\n"]+) "', r'"\1"'),
    (f" ([{_PUNC_AFTER}])", r"\1"),
    (f"([{_PUNC_BEFORE}]) ", r"\1"),
    (
        f"([{_PUNC_AFTER_HEAD}])([^ {_PUNC_AFTER}"
        r"\d\N{EXTENDED ARABIC-INDIC DIGIT ZERO}-\N{EXTENDED ARABIC-INDIC DIGIT NINE}])",
        r"\1 \2",
    ),
    (f"([{_PUNC_AFTER_TAIL}])([^ {_PUNC_AFTER}])", r"\1 \2"),
    (f"([^ {_PUNC_BEFORE}])([{_PUNC_BEFORE}])", r"\1 \2"),
    (f"(\\d)({_FA_LETTER_CLASS})", r"\1 \2"),
    (f"({_FA_LETTER_CLASS})(\\d)", r"\1 \2"),
)

_COMPILED_EXTRA_SPACE = tuple((re.compile(p), r) for p, r in _EXTRA_SPACE_PATTERNS)
_COMPILED_AFFIX = tuple((re.compile(p), r) for p, r in _AFFIX_SPACING_PATTERNS)
_COMPILED_PUNCTUATION = tuple((re.compile(p), r) for p, r in _PUNCTUATION_SPACING_PATTERNS)

#: Three or more of the same letter collapse to one. hazm's own version asks the
#: 193,350-row word list whether one or two is right; that answer needs the data
#: pack, and normalisation has to work on a fresh install, so this is the
#: pack-free rule: a Persian word never writes a letter three times.
_REPEATED_RE = re.compile(r"(" + _FA_LETTER_CLASS + r")\1{2,}")

#: hazm normalizer.py:336. The prefix is split only when the ZWNJ spelling is a
#: known verb form, which is what leaves miz ("table") alone.
_MI_PREFIX_RE = re.compile(r"\b" + _NOON + "?" + _MI + _FA_LETTER_CLASS + "+")
_MI_HEAD_RE = re.compile("^(" + _NOON + "?" + _MI + ")")

#: Set by ``languages.fa._hazm.conjugation`` once the verb table is built, so
#: ``seperate_mi`` can ask "is this a known verb form?" without this module
#: importing the engine. ``None`` means "never split", which is what a fresh
#: install (no data pack) gets, and what every other language does today.
FA_SEPARATE_MI_HOOK: Callable[[str], bool] | None = None


def is_fa_word_char(char: str) -> bool:
    """True for an Arabic-script letter or in-word mark."""
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in FA_WORD_RANGES)


def is_fa_letter(char: str) -> bool:
    """True for an Arabic-script letter (marks and digits excluded)."""
    return char.isalpha() and is_fa_word_char(char)


def _presentation_nfkc(text: str) -> str:
    return _PRESENTATION_RE.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text)


def _strip_format(text: str, keep: frozenset[str] = frozenset()) -> str:
    return "".join(ch for ch in text if ch in keep or unicodedata.category(ch) != "Cf")


def unify_letters(text: str) -> str:
    """NFC, presentation forms decomposed, Arabic letters mapped onto their Persian shapes."""
    return _presentation_nfkc(unicodedata.normalize("NFC", text)).translate(_LETTER_TABLE)


def fa_fold(text: str) -> str:
    """Index/comparison key: letters unified, every mark and format char dropped, heh+hamza -> heh."""
    folded = _KEY_MARKS_RE.sub("", unify_letters(text)).replace(HEH_WITH_YEH, "\N{ARABIC LETTER HEH}")
    return _strip_format(folded)


def _apply(patterns: tuple[tuple[re.Pattern[str], str], ...], text: str) -> str:
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def _seperate_mi(text: str) -> str:
    hook = FA_SEPARATE_MI_HOOK
    if hook is None:
        return text

    def replace_match(match: re.Match[str]) -> str:
        joined = match.group(0)
        split = _MI_HEAD_RE.sub("\\1" + ZWNJ, joined)
        return split if hook(split) else joined

    return _MI_PREFIX_RE.sub(replace_match, text)


def fa_normalize(text: str) -> str:
    """The stored spelling of a Persian line: one line in, one line out.

    Surfaces are slices of the normalised line, so this is idempotent by
    construction -- running it over its own output changes nothing.
    """
    text = unify_letters(text)
    text = _MARKS_RE.sub("", text)
    text = _strip_format(text, keep=_JOINERS)
    text = _apply(_COMPILED_EXTRA_SPACE, text)
    text = _apply(_COMPILED_AFFIX, text)
    text = _apply(_COMPILED_PUNCTUATION, text)
    text = _REPEATED_RE.sub(r"\1", text)
    return _seperate_mi(text)


class PersianDictKeys:
    """DictKeyFolding for Persian (spec C.2, S3), Rule-A-only mask.

    ``fold_term`` is ``fa_fold``: ONE function, reused as the profile's
    ``dedup_fold``, so a card typed without the ZWNJ and a mined form carrying it
    are one key at import, at query and at dedup. ``fold_reading`` casefolds,
    because Persian readings are Latin romanisations. The mask body is Korean's
    (``ko/script.py:126-132``): term-exact rows win, plus the same-content
    carve-out that preserves the dedup-before-cap tag union.
    """

    def fold_term(self, s: str) -> str:
        """Fold a lookup/storage term key."""
        return fa_fold(s)

    def fold_reading(self, s: str | None) -> str | None:
        """Fold a reading key (Persian readings are Latin romanisations)."""
        return None if s is None else unicodedata.normalize("NFC", s).casefold()

    def homograph_keep_mask(self, word: str, rows: list[tuple[str, str]], lemma: str | None = None) -> list[bool]:
        """Rule-A-only keep mask aligned to *rows* ((term, content) pairs)."""
        term_exact = [term == word for term, _ in rows]
        if not any(term_exact):
            return [True] * len(rows)
        exact_contents = {content for (_, content), ex in zip(rows, term_exact, strict=True) if ex}
        return [ex or content in exact_contents for (_, content), ex in zip(rows, term_exact, strict=True)]


class PersianScript:
    """ScriptSupport for Persian: Arabic-script letters, never digits or punctuation.

    ``filter_options() == ()`` (spec C.2): Persian has no script-exclusion
    toggles, so ``matches`` is never reached. It answers False for every option
    id rather than raising, keeping the protocol's total shape.
    """

    def filter_options(self) -> tuple[ScriptFilterOption, ...]:
        """No script-exclusion toggles for Persian."""
        return ()

    def matches(self, option_id: str, form: str) -> bool:
        """Unreachable: there are no options to match."""
        return False

    def contains_target_script(self, text: str) -> bool:
        """True when *text* contains an Arabic-script letter."""
        return any(is_fa_letter(char) for char in text)


FA_SENTENCE_RULES = SentenceRules(
    terminators=frozenset(
        ".!?"
        "\N{ARABIC QUESTION MARK}"
        "\N{DOUBLE EXCLAMATION MARK}"
        "\N{EXCLAMATION QUESTION MARK}"
        "\N{QUESTION EXCLAMATION MARK}"
    ),
    ellipses=frozenset("\N{HORIZONTAL ELLIPSIS}\N{TWO DOT LEADER}"),
    openers=frozenset('\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}([{\N{LEFT DOUBLE QUOTATION MARK}"'),
    closers=frozenset('\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK})]}\N{RIGHT DOUBLE QUOTATION MARK}"'),
    space_aware=True,
    # Empty, like ja/ko/zh: hazm's abbreviations.dat lives in the PACK, and a
    # rule that only fires once the user has downloaded a pack would split
    # sentences differently before and after the download.
    abbreviations=frozenset(),
)

#: A dialogue dash at the cue start, or after a Latin or Persian terminator and a space (the ar rule).
FA_DIALOGUE_DASH_PATTERN = r"(?:^|(?<=[.!?…\N{ARABIC QUESTION MARK}]\s))[-–—]\s+"
#: The SDH default: the script-neutral bracket/paren/music patterns plus the Persian dash rule.
#: No speaker-label pattern: the Arabic script has no capitals to tell a ``name:`` label from speech.
FA_SUBTITLE_REGEX = "|".join((BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, FA_DIALOGUE_DASH_PATTERN))
