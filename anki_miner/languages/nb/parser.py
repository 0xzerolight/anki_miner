"""Norwegian Bokmål SubtitleParser factory: the spaced factory, the form-of front repair, then the particle-verb join.

The repair (``_spaced/form_of.py``) runs first so a join sees the repaired verb; the join writes
the particle verb apart.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.nb.morphology import norwegian_particle_candidates

    kwargs.setdefault(
        "token_post_pass",
        OrderedPasses(FormOfLemmaPass(), SeparableVerbPass(candidates=norwegian_particle_candidates)),
    )
    return create_spaced_parser(config, **kwargs)
