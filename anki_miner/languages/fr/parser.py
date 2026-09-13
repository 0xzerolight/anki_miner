"""French SubtitleParser factory: the shared spaced factory plus the verb-lemma repair."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.fr.morphology import FrenchVerbLemmaPass

    kwargs.setdefault("token_post_pass", FrenchVerbLemmaPass())
    return create_spaced_parser(config, **kwargs)
