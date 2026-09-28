"""Turkish SubtitleParser factory: the spaced factory plus the dictionary's form-of front repair.

zeyrek's pick can be a lexeme wty-tr-en files only as an inflected form (``bitti``, ``günü``, ``eder``), and that
card's whole Definition was the ``non-lemma`` pointer (``bitmek``). ``FormOfLemmaPass`` (R36 ``form_lookup``) fronts
the one headword those rows name, lemma rows before the surface's: the pick is zeyrek's analysis of the whole
word, the surface only its spelling. Two headwords (``adamı``: ``ada``, ``adam``) keep the pick.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass
    from anki_miner.languages.tr.morphology import tr_row_targets

    kwargs.setdefault("token_post_pass", FormOfLemmaPass(surface_first=False, row_targets=tr_row_targets))
    return create_spaced_parser(config, **kwargs)
