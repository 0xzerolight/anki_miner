"""Finnish SubtitleParser factory: the shared spaced factory plus the form-of front repair (``_spaced/form_of.py``).

The repair reads the surface, then ``finnish_suffix_candidates`` (the surface without the clitics and possessive
suffix the model's morph marks), then the model's lemma; ``fi/morphology.py`` has the measurements.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages._spaced.form_of import FormOfLemmaPass
    from anki_miner.languages.fi.morphology import finnish_suffix_candidates

    kwargs.setdefault("token_post_pass", FormOfLemmaPass(extra_candidates=finnish_suffix_candidates))
    return create_spaced_parser(config, **kwargs)
