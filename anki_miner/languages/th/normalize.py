"""Thai text normalisation for the stored sentence and for dictionary keys.

**NFC, never NFKC.** NFKC decomposes U+0E33 THAI CHARACTER SARA AM into
U+0E4D + U+0E32 and every dictionary key that contains it stops matching; NFC is
a no-op on well-formed Thai and the safe choice.

On top of NFC come PyThaiNLP's own repair steps, minus its zero-width removal:
``reorder_vowels`` (doubled SARA E -> SARA AE, stray nikhahit + SARA AA ->
SARA AM, canonical tone-mark order), ``remove_repeat_vowels`` and
``remove_dangling`` (an orphan leading vowel). Repeated CONSONANTS are left
alone: they are an intensifier a reader writes on purpose, not an error.

Zero-width characters are kept. They are the tokenizer's authored word breaks
(``tokenizer._ZERO_WIDTH_RE``), the stored sentence is what the tokenizer is fed,
and Anki renders them invisibly. ``dedup_fold`` strips them, so no duplicate-card
key ever contains one.

``pythainlp`` is imported function-locally, behind ``_engine`` (which sets the
offline and read-only environment variables BEFORE any pythainlp import: a lone
``from pythainlp.util import reorder_vowels`` is enough to create
``$HOME/pythainlp-data``, measured). Its absence degrades to NFC plus the
whitespace collapse -- the same shape ``zh.variants.to_traditional`` uses for
a missing OpenCC. In practice the language is off the selector without the
engine (``availability.th_missing_required_reason``), so the degraded branch is
only ever the known-words and dictionary paths running before a pack install.
"""

from __future__ import annotations

import re
import unicodedata

from anki_miner.languages.th import _engine  # noqa: F401  # env guard; must precede pythainlp

_WHITESPACE_RE = re.compile("[ \t\N{NO-BREAK SPACE}]+")


def _pythainlp_steps(text: str) -> str:
    try:
        from pythainlp.util import remove_dangling, remove_repeat_vowels, reorder_vowels
    except ImportError:
        return text
    return str(remove_dangling(remove_repeat_vowels(reorder_vowels(text))))


def normalize_th(text: str) -> str:
    """The P3 normaliser: NFC, PyThaiNLP's repairs, collapsed spaces."""
    return _WHITESPACE_RE.sub(" ", _pythainlp_steps(unicodedata.normalize("NFC", text))).strip()


def fold_term_th(term: str) -> str:
    """Dictionary TERM key: NFC + ``reorder_vowels``. Idempotent."""
    try:
        from pythainlp.util import reorder_vowels
    except ImportError:
        return unicodedata.normalize("NFC", term)
    return str(reorder_vowels(unicodedata.normalize("NFC", term)))
