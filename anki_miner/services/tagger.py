"""Process-wide shared fugashi tagger with lazy construction and background pre-warm.

Single-flight assumption (IMPORTANT):
    A MeCab tagger is not safe for concurrent ``.parse()`` calls on one instance.
    Sharing a single tagger across all SubtitleParserService instances is safe ONLY
    because mining is single-flight in this app: one mining worker is active at a
    time, batch processing is sequential, and users mine one tab at a time.  If
    concurrent mining is ever introduced, give each worker its own tagger or guard
    every ``.parse()`` / ``tagger(text)`` call with a lock.
"""

import logging
import threading

import fugashi

logger = logging.getLogger(__name__)

_tagger: fugashi.Tagger | None = None
_lock = threading.Lock()


def get_shared_tagger() -> fugashi.Tagger:
    """Return the process-wide shared fugashi.Tagger, building it once (double-checked lock)."""
    global _tagger
    if _tagger is None:
        with _lock:
            if _tagger is None:
                _tagger = fugashi.Tagger()
    return _tagger


def _prewarm_worker() -> None:
    try:
        get_shared_tagger()
    except Exception:  # noqa: BLE001 - background prewarm must never crash the app
        logger.warning("Tagger pre-warm failed; it will be built on first use.", exc_info=True)


def prewarm_tagger() -> threading.Thread:
    """Start building the shared tagger on a daemon thread; return the thread (fire-and-forget)."""
    t = threading.Thread(target=_prewarm_worker, name="tagger-prewarm", daemon=True)
    t.start()
    return t
