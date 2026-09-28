"""German SubtitleParser factory: the shared spaced factory, the form-of front repair, then separable-verb reattachment.

The repair (``_spaced/form_of.py``) runs first so a join sees the repaired verb: ``siehst … aus`` is
``siehst`` to the tagger, ``sehen`` after the repair and ``aussehen`` after the join (§4.3 item 2).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.de.tokenizer import DE_TITLE_CASE_POS

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(FormOfLemmaPass(title_case_pos=DE_TITLE_CASE_POS), SeparableVerbPass()),
    )
    return create_spaced_parser(config, **kwargs)
