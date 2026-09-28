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
_ALEF_MADDA = "\N{ARABIC LETTER ALEF WITH MADDA ABOVE}"
_BEH = "\N{ARABIC LETTER BEH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_ZAIN = "\N{ARABIC LETTER ZAIN}"
_FEH = "\N{ARABIC LETTER FEH}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_WAW = "\N{ARABIC LETTER WAW}"
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

#: Tehrani speech's present person endings, each with the formal one it stands
#: for: 3sg -e for -ad, 3pl -an for -and, 2pl -in for -id (mikone, mikonan,
#: mikonin). The other three persons are spelt as the formal ones are.
_COLLOQUIAL_SUFFIXES = ((_HEH, _DAL), (_NOON, _NOON + _DAL), (_YEH + _NOON, _YEH + _DAL))
#: The 3sg -e follows a consonant only: a stem ending in a or u keeps -ad
#: (mixad, miad), which the informal table already spells.
_VOWEL_FINAL = (_ALEF, _ALEF_MADDA, _WAW)

#: 15 ``(colloquial, formal)`` template pairs over the same five prefixes.
COLLOQUIAL_PRESENT_PATTERNS: tuple[tuple[str, str], ...] = tuple(
    (prefix + PRESENT + colloquial, prefix + PRESENT + formal)
    for prefix in _PRESENT_PREFIXES
    for colloquial, formal in _COLLOQUIAL_SUFFIXES
)

#: The separable preverbs (bar-, dar-, baz-, foru-, var-, va-). verbs.dat keeps
#: one inside the stem (bargasht#bargard), so the plain templates put mi- in
#: front of it; the grammatical spelling is bar-mi-gardam.
PREVERBS = (
    _BEH + _REH,
    _DAL + _REH,
    _BEH + _ALEF + _ZAIN,
    _FEH + _REH + _WAW,
    _WAW + _REH,
    _WAW + _ALEF,
)
#: The mi- and nami- templates of the present and the past progressive: the
#: forms a preverb goes in front of.
IMPERFECTIVE_PATTERNS: tuple[str, ...] = tuple(
    prefix + PAST + suffix for prefix in (_MI, _NA + _MI) for suffix in _PAST_SUFFIXES
) + tuple(prefix + PRESENT + suffix for prefix in (_MI, _NA + _MI) for suffix in _PRESENT_SUFFIXES)

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


def expand_colloquial(informal: str, present: str) -> list[tuple[str, str]]:
    """``(colloquial form, formal spelling)`` for every colloquial-ending present.

    The endings go on the INFORMAL stem (mi-r-e for raftan, not mi-rav-e): a
    formal stem with a colloquial ending is not how anyone speaks, and it is
    what turns the clitic shun ("their") into shav- + -an.
    """
    return [
        (apply(colloquial, present=informal), apply(formal, present=present))
        for colloquial, formal in COLLOQUIAL_PRESENT_PATTERNS
        if not (colloquial.endswith(PRESENT + _HEH) and informal.endswith(_VOWEL_FINAL))
    ]


def split_preverb(past: str, present: str) -> tuple[str, str, str] | None:
    """``(preverb, past, present)`` when both stems open with the same preverb."""
    for preverb in PREVERBS:
        if past.startswith(preverb) and present.startswith(preverb):
            return preverb, past[len(preverb) :], present[len(preverb) :]
    return None


def expand_preverb(preverb: str, past: str, present: str) -> list[str]:
    """The mi-/nami- forms of a preverb verb with the prefix after the preverb."""
    return [preverb + apply(pattern, past, present) for pattern in IMPERFECTIVE_PATTERNS]


def split_stems(verb_line: str) -> tuple[str, str]:
    """``past#present`` into its two stems (either may be empty or malformed)."""
    past, _, present = verb_line.partition("#")
    return past, present


def infinitive(verb_line: str) -> str:
    """The infinitive of a ``past#present`` line: past stem plus -an."""
    past, _present = split_stems(verb_line)
    return past + _NOON
