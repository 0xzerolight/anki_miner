"""hazm's suffix stemmer (``stemmer.py``), ported.

Naive on purpose, exactly as upstream: the longest matching suffix comes off,
a one-character suffix only when three characters would be left. It is a
retrieval aid, not a lemmatiser -- it takes mardom ("people") to mard ("man")
and in ("this") to a bare alef -- which is why the tokenizer mines the lemma the
lookup ladder chose and never a second ``stem()`` pass over it.
"""

from __future__ import annotations

ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
_ALEF = "\N{ARABIC LETTER ALEF}"
_TEH = "\N{ARABIC LETTER TEH}"
_DAL = "\N{ARABIC LETTER DAL}"
_REH = "\N{ARABIC LETTER REH}"
_SHEEN = "\N{ARABIC LETTER SHEEN}"
_GAF = "\N{ARABIC LETTER GAF}"
_MEEM = "\N{ARABIC LETTER MEEM}"
_NOON = "\N{ARABIC LETTER NOON}"
_HEH = "\N{ARABIC LETTER HEH}"
_YEH = "\N{ARABIC LETTER FARSI YEH}"
_HAMZA_ABOVE = "\N{ARABIC HAMZA ABOVE}"
_HEH_WITH_YEH = "\N{ARABIC LETTER HEH WITH YEH ABOVE}"

#: hazm ``constants.py:84-89`` plus the three it adds in ``Stemmer.__init__``
#: (the ezafe hamza, a ZWNJ-alef and a bare ZWNJ), longest first.
SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        {
            _YEH,
            _ALEF + _YEH,
            _HEH + _ALEF,
            _HEH + _ALEF + _YEH,
            _HEH + _ALEF + _YEH + _YEH,
            _TEH + _REH,
            _TEH + _REH + _YEH,
            _TEH + _REH + _YEH + _NOON,
            _GAF + _REH,
            _GAF + _REH + _YEH,
            _ALEF + _MEEM,
            _ALEF + _TEH,
            _ALEF + _SHEEN,
            _YEH + _MEEM,
            _YEH + _DAL,
            _NOON + _DAL,
            _MEEM + _ALEF + _NOON,
            _TEH + _ALEF + _NOON,
            _SHEEN + _ALEF + _NOON,
            _HEH + _ALEF + _YEH + _MEEM + _ALEF + _NOON,
            _HEH + _ALEF + _YEH + _TEH + _ALEF + _NOON,
            _HEH + _ALEF + _YEH + _SHEEN + _ALEF + _NOON,
            _ALEF + _NOON,
            _YEH + _NOON,
            _MEEM,
            _TEH,
            _SHEEN,
            _HAMZA_ABOVE,
            ZWNJ + _ALEF,
            ZWNJ,
        },
        key=lambda suffix: (-len(suffix), suffix),
    )
)

#: Keeping fewer than this many characters is not a stem (hazm's own floor, and
#: it applies to one-character suffixes only).
MIN_STEM = 3


def stem(word: str) -> str:
    """Strip the longest matching suffix, hazm's rules unchanged."""
    for suffix in SUFFIXES:
        if word.endswith(suffix):
            if len(suffix) == 1 and len(word) - len(suffix) < MIN_STEM:
                continue
            word = word[: -len(suffix)]
            break

    if word.endswith(_HEH_WITH_YEH):
        word = word[:-1] + _HEH
    if word.endswith(ZWNJ):
        word = word[:-1]
    return word
