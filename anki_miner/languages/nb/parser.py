"""Norwegian Bokmål SubtitleParser factory: the spaced factory plus the particle-verb join, written apart."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.nb.morphology import norwegian_particle_candidates

    kwargs.setdefault("token_post_pass", SeparableVerbPass(candidates=norwegian_particle_candidates))
    return create_spaced_parser(config, **kwargs)
