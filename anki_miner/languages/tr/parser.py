"""Turkish SubtitleParser factory: the spaced factory plus two passes over wty-tr-en's rows (R36 ``form_lookup``).

``HeadwordPosPass`` labels a pick with the class its lemma's headword rows give, among that lemma's own readings:
zeyrek ranks ``çocuk``'s Adj reading over its Noun one. It reads the tokenizer's lemma, so it runs first.

zeyrek's pick can also be a lexeme wty-tr-en files only as an inflected form (``bitti``, ``günü``, ``eder``), and that
card's whole Definition was the ``non-lemma`` pointer (``bitmek``). ``FormOfLemmaPass`` fronts the one headword those
rows name, lemma rows before the surface's: the pick is zeyrek's analysis of the whole word, the surface only its
spelling. Two headwords (``adamı``: ``ada``, ``adam``) keep the pick.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages.tr.morphology import HeadwordPosPass, tr_row_targets

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(HeadwordPosPass(), FormOfLemmaPass(surface_first=False, row_targets=tr_row_targets)),
    )
    return create_spaced_parser(config, **kwargs)
