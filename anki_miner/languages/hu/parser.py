"""Hungarian SubtitleParser factory: the spaced factory plus the preverb join (§4.3 item 2, ``compound:preverb``)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.morphology import SeparableVerbPass
    from anki_miner.languages.hu.morphology import hungarian_preverb_candidates

    kwargs.setdefault("token_post_pass", SeparableVerbPass(candidates=hungarian_preverb_candidates))
    return create_spaced_parser(config, **kwargs)
