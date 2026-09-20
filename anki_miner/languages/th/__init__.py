"""Thai language engine (spec C.3).

Every ``pythainlp`` import is function-local or behind ``_engine``, so importing
this package — and building the th LanguageProfile — never needs the
``anki-miner[th]`` extra installed. Availability is reported by
``languages.th.availability``.
"""

from __future__ import annotations
