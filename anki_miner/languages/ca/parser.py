"""Catalan SubtitleParser factory: the shared spaced factory plus the feminine-noun front (``ca/morphology.py``).

The general form-of repair (``_spaced/form_of.py``) is not wired: it never second-guesses a lemma the
dictionary files as a headword, and ``fill`` is one.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.ca.morphology import FeminineNounPass

    kwargs.setdefault("token_post_pass", FeminineNounPass())
    return create_spaced_parser(config, **kwargs)
