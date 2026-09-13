"""Spanish SubtitleParser factory: the shared spaced factory, no post-pass (the verb repair runs in the tagger)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
