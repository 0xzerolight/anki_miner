"""Process-wide shared fugashi tagger with lazy, double-checked-locked construction.

Single-flight assumption (IMPORTANT):
    A MeCab tagger is not safe for concurrent ``.parse()`` calls on one instance.
    Sharing a single tagger across all SubtitleParserService instances is safe ONLY
    because mining is single-flight in this app: one mining worker is active at a
    time, batch processing is sequential, and users mine one tab at a time.  If
    concurrent mining is ever introduced, give each worker its own tagger or guard
    every ``.parse()`` / ``tagger(text)`` call with a lock.

Background warming of this singleton is done by ``gui/workers/prewarm_worker.py``,
which calls ``get_shared_tagger()`` off the GUI thread before the first mine.
"""

import threading

import fugashi

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
