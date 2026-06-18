"""Process-wide shared fugashi tagger with lazy, double-checked-locked construction.

The shared tagger is wrapped in a ``LockedTagger`` that serialises every
``tagger(text)`` and ``.parse()`` call with an ``threading.RLock``.  Concurrent
mining sessions (multiple tabs, batch + single, etc.) are therefore safe: each
call acquires the lock, tokenises, then releases.  The single-flight assumption
that previously guarded against MeCab non-reentrancy is **no longer load-bearing**
and has been removed.

The RLock is used (rather than a plain Lock) so that same-thread re-entry
(e.g. nested parse calls on one thread) does not deadlock; cross-thread mutual
exclusion is the primary safety goal.

Background warming via ``gui/workers/prewarm_worker.py`` calls
``get_shared_tagger()`` to pay the unidic-lite / lattice init cost early.
Prewarm intentionally does NOT call ``.parse()`` — it only constructs the tagger.
This is safe: ``get_shared_tagger()`` / wrapper construction is itself guarded by
a separate ``threading.Lock`` (double-checked lock), so concurrent first calls
from multiple threads do not double-build.
"""

import threading
from typing import Any

import fugashi

_tagger_lock = threading.Lock()
_tagger: fugashi.Tagger | None = None
_locked_tagger: "LockedTagger | None" = None


class LockedTagger:
    """Thread-safe wrapper around a ``fugashi.Tagger``.

    Serialises ``__call__`` and ``parse`` via a module-level ``threading.RLock``
    so that concurrent mining sessions cannot call the underlying MeCab tagger
    simultaneously.  All other attribute access is transparently delegated to the
    wrapped tagger via ``__getattr__``.

    The return value of ``__call__`` is the node iterable returned by the
    underlying tagger, so callers that do ``list(tagger(text))`` continue to work
    unchanged.
    """

    # The single process-wide lock shared across ALL LockedTagger instances
    # (there is only ever one, but the class-level placement makes the scope clear).
    _parse_lock: threading.RLock = threading.RLock()

    def __init__(self, inner: fugashi.Tagger) -> None:
        # Store as a name that will NOT be caught by __getattr__.
        object.__setattr__(self, "_inner", inner)

    # ------------------------------------------------------------------
    # Locked interface
    # ------------------------------------------------------------------

    def __call__(self, text: str, *args: Any, **kwargs: Any) -> Any:
        """Acquire the parse lock, tokenise ``text``, return the node iterable."""
        with self._parse_lock:
            return self._inner(text, *args, **kwargs)

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        """Acquire the parse lock and delegate to the wrapped tagger's ``parse``."""
        with self._parse_lock:
            return self._inner.parse(*args, **kwargs)

    # ------------------------------------------------------------------
    # Transparent delegation
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate all other attribute access to the wrapped tagger."""
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __repr__(self) -> str:
        inner = object.__getattribute__(self, "_inner")
        return f"LockedTagger({inner!r})"


def get_shared_tagger() -> LockedTagger:
    """Return the process-wide lock-guarded fugashi tagger.

    Builds the underlying ``fugashi.Tagger`` once (double-checked lock) and
    wraps it in a ``LockedTagger`` that serialises every ``.parse()`` /
    ``__call__``.  The wrapper is also built once and cached.

    Concurrent callers from multiple threads are safe: construction is
    double-checked-locked; post-construction every tokenisation acquires the
    ``LockedTagger._parse_lock`` before touching the underlying MeCab tagger.
    """
    global _tagger, _locked_tagger
    if _locked_tagger is None:
        with _tagger_lock:
            if _locked_tagger is None:
                # Build the raw tagger if not already built.
                if _tagger is None:
                    _tagger = fugashi.Tagger()
                _locked_tagger = LockedTagger(_tagger)
    return _locked_tagger
