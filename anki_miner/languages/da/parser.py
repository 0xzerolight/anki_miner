"""Danish SubtitleParser factory: the spaced factory, the form-of front repair, then the two-word particle-verb join.

The repair (``_spaced/form_of.py``) runs first so a join sees the repaired verb. A particle on an adverbial arc
joins only when the dictionary attests the two-word verb (``da/morphology.py``).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass, OrderedPasses
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.da.morphology import DA_ADVERBIAL_PARTICLE_DEPS, danish_particle_candidates

    join = SeparableVerbPass(candidates=danish_particle_candidates, attested_only_deps=DA_ADVERBIAL_PARTICLE_DEPS)
    kwargs.setdefault("token_post_pass", OrderedPasses(FormOfLemmaPass(), join))
    return create_spaced_parser(config, **kwargs)
