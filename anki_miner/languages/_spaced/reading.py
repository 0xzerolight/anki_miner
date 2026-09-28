"""The parser-side ``ReadingSupport`` for a profile that has no reading of its own.

``SubtitleParserService`` reads ``reading_support=None`` as "run the Japanese
derivation": reading off the token's kana, ruby assembly, and the attested-
reading review that records multi-reading headwords. For a language whose
``LanguageProfile.reading`` is ``None`` that pass fills ExpressionReading with a
dictionary's reading column (ro ``lucra`` -> ``lucră``), shows the Japanese
"more than one reading" warning, and gives ko a stem fragment as its Reading.
Its factories inject this instead, so the parser takes the profile-owned branch
and every reading field stays ``""``. ``profile.reading`` itself stays ``None``:
the profile has no reading; only the parser needs to be told so.
"""

from __future__ import annotations

from typing import Any


class NoReading:
    """``ReadingSupport`` whose word reading is always empty."""

    def word_reading(self, token: Any) -> str:
        return ""


NO_READING = NoReading()
