"""Danish SubtitleParser factory: the spaced factory plus the two-word particle-verb join."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.da.morphology import danish_particle_candidates

    kwargs.setdefault("token_post_pass", SeparableVerbPass(candidates=danish_particle_candidates))
    return create_spaced_parser(config, **kwargs)
