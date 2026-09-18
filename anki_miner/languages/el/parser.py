"""Greek SubtitleParser factory: the shared spaced factory, no post-pass.

The it-style attested-lemma pass was measured and left out: on UD Greek GDT test it moves lemma
accuracy from 81.1 % to 81.2 % (plan 2026-09-17-el, "Lemma quality").
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
