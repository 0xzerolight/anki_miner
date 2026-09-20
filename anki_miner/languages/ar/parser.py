"""Arabic SubtitleParser factory: the shared ``_spaced`` factory (script gate, mined form, reading, ar_normalize)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
