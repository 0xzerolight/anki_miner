"""The Persian verb paradigms, as string composition instead of hazm's class.

``Conjugation.get_all`` run with sentinel stems in place of the real ones emits
97 single-token forms, 79 of them distinct, and every one is a prefix, a stem
and a suffix concatenated (plan probe P-3, ``SP/probe3.py``). Expanding those 79
templates over all 693 ``verbs.dat`` rows reproduces hazm's own single-token
output for 689 of them -- the four misses are malformed rows, which the lexicon
skips -- at 5.7 MB and 0.04 s against hazm's 113 MB and 0.56 s.

The bare imperative is deliberately absent. Adding ``be`` + present stem and
``na`` + present stem moves the table's collisions with ``words.dat`` from 92 to
337 and turns common words into rare verbs: bale, boland, bare, baste, besham
and bezar all become infinitives of verbs nobody mines (measured over
fa_50k.txt, plan probe P-9). Imperative recognition is on the owed list.
"""

from __future__ import annotations

#: The sentinels the templates are written over.
PAST = "{past}"
PRESENT = "{present}"

ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
_ALEF = "\N{ARABIC LETTER ALEF}"
_BEH = "\N{ARABIC LETTER BEH}"
_DAL = "\N{ARABIC LETTER DAL}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"
#: mi-, the imperfective prefix; na-, the negative one.
_MI = _MEEM + _YEH + ZWNJ
_NA = _NOON

#: na-, mi-, nami- and the bare stem, in hazm's own order.
_PAST_PREFIXES = ("", _NA, _MI, _NA + _MI)
#: -am, -i, (none), -im, -id, -and.
_PAST_SUFFIXES = (_MEEM, _YEH, "", _YEH + _MEEM, _YEH + _DAL, _NOON + _DAL)
#: The past participle's own suffixes: -e, -e'am, -e'i, -e'im, -e'id, -e'and.
_PARTICIPLE_SUFFIXES = (
    _HEH + ZWNJ + _ALEF + _MEEM,
    _HEH + ZWNJ + _ALEF + _YEH,
    _HEH,
    _HEH + ZWNJ + _ALEF + _YEH + _MEEM,
    _HEH + ZWNJ + _ALEF + _YEH + _DAL,
    _HEH + ZWNJ + _ALEF + _NOON + _DAL,
)
#: The present half adds the subjunctive be- to the same four prefixes.
_PRESENT_PREFIXES = ("", _NA, _BEH, _MI, _NA + _MI)
#: -am, -i, -ad, -im, -id, -and. Note the third is -ad, not the bare stem: the
#: imperative it would produce is exactly what Decision 2 leaves out.
_PRESENT_SUFFIXES = (_MEEM, _YEH, _DAL, _YEH + _MEEM, _YEH + _DAL, _NOON + _DAL)

#: The infinitive: past stem plus -an.
INFINITIVE_PATTERN = PAST + _NOON

#: The 30 present-half templates, which the informal table also expands.
PRESENT_PATTERNS: tuple[str, ...] = tuple(
    prefix + PRESENT + suffix for prefix in _PRESENT_PREFIXES for suffix in _PRESENT_SUFFIXES
)

#: All 79 single-token templates: infinitive, simple past, past participle,
#: present.
PATTERNS: tuple[str, ...] = (
    (INFINITIVE_PATTERN,)
    + tuple(prefix + PAST + suffix for prefix in _PAST_PREFIXES for suffix in _PAST_SUFFIXES)
    + tuple(prefix + PAST + suffix for prefix in _PAST_PREFIXES for suffix in _PARTICIPLE_SUFFIXES)
    + PRESENT_PATTERNS
)


def apply(pattern: str, past: str = "", present: str = "") -> str:
    """Fill one template."""
    return pattern.replace(PAST, past).replace(PRESENT, present)


def expand(past: str, present: str) -> list[str]:
    """Every single-token form of one verb, infinitive included."""
    return [apply(pattern, past, present) for pattern in PATTERNS]


def split_stems(verb_line: str) -> tuple[str, str]:
    """``past#present`` into its two stems (either may be empty or malformed)."""
    past, _, present = verb_line.partition("#")
    return past, present


def infinitive(verb_line: str) -> str:
    """The infinitive of a ``past#present`` line: past stem plus -an."""
    past, _present = split_stems(verb_line)
    return past + _NOON
