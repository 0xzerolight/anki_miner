"""Croatian SubtitleParser factory: the shared spaced factory plus the short-infinitive repair."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.hr.morphology import hr_short_infinitive_pass

    kwargs.setdefault("token_post_pass", hr_short_infinitive_pass)
    return create_spaced_parser(config, **kwargs)
