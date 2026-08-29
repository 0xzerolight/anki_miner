"""Profile registry. Import ``get_profile`` and ``available_languages`` from
HERE, never from the package ``__init__`` — that module is Stage 0's
AVAILABLE_LANGUAGES surface and stays untouched (an eager re-export would drag
services.resource_catalog into every ``import anki_miner.languages``).

``_CACHE`` is process-wide and never evicted in production; a test that
registers a builder of its own resets it with ``_CACHE.clear()``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from anki_miner.languages.profile import LanguageProfile

logger = logging.getLogger(__name__)

_BUILDERS: dict[str, Callable[[], LanguageProfile]] = {}
_CACHE: dict[str, LanguageProfile] = {}
_LOCK = threading.Lock()

#: Codes already reported by :func:`config_language`. Roughly ninety sites read
#: the mining language, several of them per-word, so the warning is emitted once
#: per code rather than once per read.
_DEGRADE_WARNED: set[str] = set()


def _register(code: str, builder: Callable[[], LanguageProfile]) -> None:
    _BUILDERS[code] = builder


def _ja_builder() -> LanguageProfile:
    from anki_miner.languages.ja import build_profile

    return build_profile()


_register("ja", _ja_builder)


def _zh_builder() -> LanguageProfile:
    # The import lives in the builder, not at module scope: a ja session must
    # never pay for (or fail on) the zh engine's optional dependency set.
    from anki_miner.languages.zh import build_profile

    return build_profile()


_register("zh", _zh_builder)


def _ko_builder() -> LanguageProfile:
    # Same reason as zh: the import stays in the builder so a ja session never
    # pays for (or fails on) the ko engine's optional dependency set.
    from anki_miner.languages.ko import build_profile

    return build_profile()


_register("ko", _ko_builder)


def config_language(config: Any) -> str:
    """The mining-language code carried by *config*, ``"ja"`` when unusable.

    Two degrade paths, both landing on Japanese:

    * *config* has no string ``language`` at all. Four pre-existing test files
      build their config as a bare ``MagicMock`` (test_alass_engine.py:61,
      test_audio_condenser.py:508, test_retime_reference.py:64,
      test_subtitle_retimer.py:80) and may not be edited; the pre-Stage-1
      behaviour at every site reading this was "Japanese".
    * The code is whitelisted by ``config.config._LANGUAGE_CODES`` but has no
      registered profile — every whitelisted code has one today, so this is the
      path a code declared ahead of its profile takes. A
      hand-edited ``gui_config.json`` would otherwise raise out of every
      ``get_profile(config_language(config))`` site, including Settings'
      ``load_from_config`` and ``AnkiService.__init__``, with no in-app
      recovery. Membership is checked directly, so no profile is built to find
      out; the moment the real profile registers, the code is honoured.
    """
    language = getattr(config, "language", "ja")
    if not isinstance(language, str):
        return "ja"
    if language not in _BUILDERS:
        if language not in _DEGRADE_WARNED:
            _DEGRADE_WARNED.add(language)
            logger.warning(
                "No language profile is registered for %r; mining as Japanese.",
                language,
            )
        return "ja"
    return language


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
