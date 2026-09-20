"""Turkish SubtitleParser factory: the spaced factory as is (no post-pass; the lookup ladder is the Latin one)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
