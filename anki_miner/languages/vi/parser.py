"""Vietnamese SubtitleParser factory: the shared spaced factory, the name tier and the token gate.

``create_spaced_parser`` fills normalize (``vi_normalize``), the mined-form policy
(``SpacedMinedForm``: the tokenizer's folded surface), no compound matcher (a
spaced join would print the syllables together) and no sentence annotation. Two
seams are set here first (``setdefault`` keeps an injected test double in charge):
the name tier (``VietnameseNamePass``) and the per-token script gate
``is_vietnamese_word`` (plan decision 16) - the profile's
``contains_target_script`` judges whole texts and would drop ASCII-only words.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.vi.morphology import VietnameseNamePass
    from anki_miner.languages.vi.script import is_vietnamese_word

    kwargs.setdefault("token_post_pass", VietnameseNamePass())
    kwargs.setdefault("script_gate", is_vietnamese_word)
    return create_spaced_parser(config, **kwargs)
