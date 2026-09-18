"""Polish SubtitleParser factory: the shared spaced factory, no post-pass."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
