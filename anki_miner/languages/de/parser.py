"""German SubtitleParser factory: the shared spaced factory plus separable-verb reattachment (§4.3 item 2)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.morphology import SeparableVerbPass

    kwargs.setdefault("token_post_pass", SeparableVerbPass())
    return create_spaced_parser(config, **kwargs)
