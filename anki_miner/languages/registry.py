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
