"""Per-language mining support.

Stage 0 declares only the code vocabulary. Profile types, the registry and the
ja/ko/zh profiles arrive in later stages and are imported from
``anki_miner.languages.registry`` — never re-exported here. ``profile.py``
imports ``anki_miner.services.resource_catalog`` at module level, so an eager
re-export would drag the service layer into every ``import anki_miner.languages``.
Nothing in this module may import Qt, a tokenizer, or anki_miner.services —
config-time and packaging cost stays nil.
"""

from __future__ import annotations

#: Every mining language the app knows about. ``AnkiMinerConfig`` duplicates
#: this tuple as ``anki_miner.config.config._LANGUAGE_CODES`` because config
#: must not import this package (same rule as ``excluded_wordsets`` vs
#: ``WORDSET_IDS``); ``tests/unit/test_config_language.py`` pins them identical.
AVAILABLE_LANGUAGES: tuple[str, ...] = ("ja", "ko", "zh")

__all__ = ["AVAILABLE_LANGUAGES"]
