"""yue text normalisation and dictionary-key folding.

Two different folds, on purpose. :func:`normalize_yue` is the profile's
``normalize`` -- what the STORED sentence goes through -- and is NFC and nothing
else: the cleaned line is what the card shows, so widths and enclosed forms stay
as the subtitle wrote them. :func:`fold_term_yue` is the dictionary KEY fold and
may be lossy, because it is applied symmetrically at import and at query
(``services/dictionary/storage.py::_folders``, whose comment block states the
symmetry rule: an index written with one folding and queried with another
silently returns zero rows).

The key fold is NFC, then the radical fold, then NFKC restricted to the
Halfwidth and Fullwidth Forms Latin block U+FF01-FF5E. The restriction is not
about compatibility ideographs -- U+F9D1 folds under plain NFC already -- but
because whole-string NFKC expands enclosed and squared CJK (㈱ -> (株),
㌀ -> アパート) and rewrites ｟ -> ⦅, which the profile's sentence rules list as
an opener. HK subtitles mix fullwidth Latin and digits into Han runs, and that
is the only width fold a key needs.
"""

from __future__ import annotations

import unicodedata

from anki_miner.utils.ja_normalize import normalize_radicals

#: Halfwidth and Fullwidth Forms, Latin sub-block: the only characters the key
#: fold passes through NFKC.
_FULLWIDTH_LATIN = range(0xFF01, 0xFF5F)


def normalize_yue(text: str) -> str:
    """NFC-normalise ``text``. Single normalisation rule for the yue engine."""
    return unicodedata.normalize("NFC", text)


def fold_term_yue(text: str) -> str:
    """The dictionary term key: NFC, radical fold, fullwidth-Latin NFKC."""
    folded = normalize_radicals(normalize_yue(text))
    return "".join(unicodedata.normalize("NFKC", char) if ord(char) in _FULLWIDTH_LATIN else char for char in folded)
