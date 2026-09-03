"""Process-wide {language: tagger} cache. Never evicted.

``"ja"`` delegates to services.tagger.get_shared_tagger() unchanged — the
single-flight LockedTagger contract stays exactly where it is. Later stages add
their branch inside ``_build``, never inside ``get_tagger``, so the cache write
always happens.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

_TAGGERS: dict[str, Any] = {}
_LOCK = threading.Lock()


def _build(language: str) -> Any:
    if language == "ja":
        from anki_miner.services.tagger import get_shared_tagger

        return get_shared_tagger()
    # Generic resolution: any language whose package ships
    # ``<lang>/tokenizer.py::build_tagger()`` registers itself with no
    # per-language code here. An unresolvable module (or a tokenizer whose own
    # third-party import is missing) is reported as an unregistered language —
    # the ValueError callers already handle — with the ImportError chained.
    import importlib

    try:
        module = importlib.import_module(f"anki_miner.languages.{language}.tokenizer")
        # Inside the try on purpose: every tokenizer module imports its engine
        # lazily inside build_tagger (zh does ``import jieba.posseg`` there), so
        # an install without the language's extra surfaces the missing engine
        # HERE, not at import_module. Outside, it escaped as a bare
        # ModuleNotFoundError past every ``except ValueError`` this contract
        # tells callers to write.
        return module.build_tagger()
    except ImportError as exc:
        # Two very different installs land here: a language that ships no
        # tokenizer at all, and one whose engine is present but unimportable (a
        # missing shared library, a half-extracted pack). The flat sentence
        # stays - every caller handles this ValueError - but the import failure
        # now travels inside it, and is recorded even where a caller swallows
        # the exception to fall back.
        detail = f"{type(exc).__name__}: {exc}"
        log_summary(
            logger,
            "Language module probe failed",
            level=logging.WARNING,
            module=f"anki_miner.languages.{language}.tokenizer",
            exc=detail,
        )
        raise ValueError(f"No tokenizer registered for language: {language!r} ({detail})") from exc


def get_tagger(language: str = "ja") -> Any:
    """Return the cached tokenizer for ``language`` (double-checked lock)."""
    cached = _TAGGERS.get(language)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _TAGGERS.get(language)
        if cached is None:
            cached = _build(language)
            _TAGGERS[language] = cached
        return cached
