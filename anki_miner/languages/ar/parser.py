"""Arabic SubtitleParser factory: the shared ``_spaced`` factory (script gate, mined form, reading, ar_normalize).

The one Arabic seam it closes is the ``token_post_pass``: ``ArabicFormOfPass`` fronts a word wty-ar-en
files only as a form of one lemma (``\u0645\u0627\u0636\u064a`` -> ``\u0645\u0627\u0636``). It reads R36's ``form_lookup``, which
``service_factory`` wires whenever an indexed dictionary is enabled; with none it is a no-op.
"""

from __future__ import annotations

from typing import Any


def create_parser(config: Any, **kwargs: Any) -> Any:
    from anki_miner.languages._spaced import create_spaced_parser
    from anki_miner.languages.ar.morphology import ArabicFormOfPass
    from anki_miner.languages.registry import get_profile

    kwargs.setdefault("token_post_pass", ArabicFormOfPass())
    # Bilingual cues put an English translation line under the native one, and the
    # flattened cue becomes the card's Sentence (ZH-046, KO-06): the script gate drops it.
    kwargs.setdefault("has_target_script", get_profile(config.language).script.contains_target_script)
    return create_spaced_parser(config, **kwargs)
