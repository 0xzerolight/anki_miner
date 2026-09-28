"""Hungarian SubtitleParser factory: the spaced factory, the preverb join, then the potential-verb front repair.

The join (§4.3 item 2, ``compound:preverb``) runs first, so the repair reads the verb the card will carry: ``teheted
… fel`` joins to ``feltehet``, whose own wty-hu-en form row names ``feltesz``. The repair is the shared form-of pass
(``_spaced/form_of.py``) reading the front's row before the surface's (``tudhatod``'s row names ``tudhat``, itself
no headword) and gated by ``potential_verb_front`` (``hu/morphology.py``).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.hu.morphology import hungarian_preverb_candidates, potential_verb_front

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(
            SeparableVerbPass(candidates=hungarian_preverb_candidates),
            FormOfLemmaPass(surface_first=False, accept=potential_verb_front),
        ),
    )
    return create_spaced_parser(config, **kwargs)
