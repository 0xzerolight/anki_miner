"""yue SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed.
The tokenizer arrives through ``tagger_provider.get_tagger(config.language)``.
The shared spaced factory fills the seams below; this one adds the ellipsis guard.

``script_gate`` is the one seam this factory closes that zh leaves open: in
sentence context the tagger gives Latin and digit runs ordinary tags (measured:
``Netflix`` NOUN, ``2024`` NOUN, ``8`` NOUN), so POS cannot exclude them and the
Han gate has to. zh does not need this because jieba tags Latin ``eng``/``x``,
outside its allowed set.

``compound_matching=False`` (S7) -- the matcher greedily joins adjacent tokens
against the installed dictionary, and Cantonese has no spaces to stop it. With
CC-CEDICT-Canto carrying 163,280 mostly written-Chinese headwords, leaving it on
turns the top risk of this language (written Chinese under Cantonese dialogue)
into a mining behaviour. th closes it for the same reason.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Cantonese SubtitleParser for ``config``."""
    from anki_miner.languages._spaced import create_spaced_parser

    # Cantonese is as single-character-dense as Mandarin: see zh/parser.py.
    kwargs.setdefault("ellipsis_fragment_guard", False)
    return create_spaced_parser(config, **kwargs)
