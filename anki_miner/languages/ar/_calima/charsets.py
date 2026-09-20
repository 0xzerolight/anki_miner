"""Arabic character sets and de-diacritisation (ported, see ``__init__``).

Upstream ``camel_tools.utils.charsets`` builds ``UNICODE_PUNCT_SYMBOL_CHARSET``
by walking every code point at import time; :func:`is_punct_or_symbol` asks
``unicodedata`` the same question per character instead, so the analyzer's
punctuation tests are unchanged and importing this module costs nothing.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "AR_CHARSET",
    "AR_DIAC_CHARSET",
    "AR_LETTERS_CHARSET",
    "dediac_ar",
    "is_punct_or_symbol",
]

AR_LETTERS_CHARSET = frozenset(
    "\u0621\u0622\u0623\u0624\u0625\u0626\u0627"
    "\u0628\u0629\u062a\u062b\u062c\u062d\u062e"
    "\u062f\u0630\u0631\u0632\u0633\u0634\u0635"
    "\u0636\u0637\u0638\u0639\u063a\u0640\u0641"
    "\u0642\u0643\u0644\u0645\u0646\u0647\u0648"
    "\u0649\u064a\u0671\u067e\u0686\u06a4\u06af"
)
AR_DIAC_CHARSET = frozenset("\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652\u0670")
AR_CHARSET = AR_LETTERS_CHARSET | AR_DIAC_CHARSET

_DIAC_RE_AR = re.compile("[" + re.escape("".join(sorted(AR_DIAC_CHARSET))) + "]")


def dediac_ar(text: str) -> str:
    """Remove the Arabic diacritics (tashkeel and dagger alif) from *text*."""
    return _DIAC_RE_AR.sub("", text)


def is_punct_or_symbol(char: str) -> bool:
    """Whether *char* is Unicode punctuation (P*) or a symbol (S*)."""
    return unicodedata.category(char)[0] in "PS"
