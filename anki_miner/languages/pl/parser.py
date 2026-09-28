"""Polish SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``)."""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass

    kwargs.setdefault("token_post_pass", FormOfLemmaPass())
    return create_spaced_parser(config, **kwargs)
