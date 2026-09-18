"""Lithuanian SubtitleParser factory: the shared spaced factory, no post-pass.

The it-style attested-lemma pass was measured and left out: on ALKSNIS dev+test it fires on 426 of
11,987 content tokens for a net +17 correct lemmas (plan 2026-09-17-lt, D-7).
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
