"""Italian SubtitleParser factory: the shared spaced factory plus the attested-lemma post-pass."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.it.morphology import AttestedLemmaPass

    kwargs.setdefault("token_post_pass", AttestedLemmaPass())
    return create_spaced_parser(config, **kwargs)
