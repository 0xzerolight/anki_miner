"""Profile registry. Import ``get_profile`` and ``available_languages`` from
HERE, never from the package ``__init__`` — that module is Stage 0's
AVAILABLE_LANGUAGES surface and stays untouched (an eager re-export would drag
services.resource_catalog into every ``import anki_miner.languages``).

``_CACHE`` is process-wide and never evicted in production; a test that
registers a builder of its own resets it with ``_CACHE.clear()``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from anki_miner.languages.profile import LanguageProfile

_BUILDERS: dict[str, Callable[[], LanguageProfile]] = {}
_CACHE: dict[str, LanguageProfile] = {}
_LOCK = threading.Lock()


def _register(code: str, builder: Callable[[], LanguageProfile]) -> None:
    _BUILDERS[code] = builder


def _ja_builder() -> LanguageProfile:
    from anki_miner.languages.ja import build_profile

    return build_profile()


_register("ja", _ja_builder)


def config_language(config: Any) -> str:
    """The mining-language code carried by *config*, ``"ja"`` when it has none.

    Stage 0 validates ``AnkiMinerConfig.language`` against ``_LANGUAGE_CODES``,
    so the fallback is unreachable for a real config. It exists because four
    pre-existing test files build their config as a bare ``MagicMock``
    (test_alass_engine.py:61, test_audio_condenser.py:508,
    test_retime_reference.py:64, test_subtitle_retimer.py:80) and may not be
    edited; the pre-1B behaviour at every site reading this was "Japanese".
    """
    language = getattr(config, "language", "ja")
    return language if isinstance(language, str) else "ja"


def available_languages() -> tuple[str, ...]:
    """Codes with a registered profile, in registration order."""
    return tuple(_BUILDERS)


def get_profile(code: str) -> LanguageProfile:
    """Return the cached profile for ``code``. Unknown codes raise ValueError."""
    cached = _CACHE.get(code)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(code)
        if cached is not None:
            return cached
        builder = _BUILDERS.get(code)
        if builder is None:
            raise ValueError(f"Unknown language code: {code!r}")
        profile = builder()
        _CACHE[code] = profile
        return profile
