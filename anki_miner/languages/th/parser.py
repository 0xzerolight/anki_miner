"""th SubtitleParser factory (the profile's ``create_parser`` field).

The service is the shared ``SubtitleParserService`` -- nothing is subclassed.
The tokenizer arrives through ``tagger_provider.get_tagger(config.language)``.
The shared spaced factory fills every seam Thai needs.

``compound_matching=False`` (S7), as the zh and yue factories also pass: the
compound matcher greedily joins adjacent tokens against the
installed dictionary, and with no spaces to stop it a Thai line merges up to
five tokens into one attested-looking string. The spaced factory passes it.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    """Build the Thai SubtitleParser for ``config``."""
    from anki_miner.languages._spaced import create_spaced_parser

    return create_spaced_parser(config, **kwargs)
