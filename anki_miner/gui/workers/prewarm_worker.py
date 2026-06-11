"""Best-effort background warming of MeCab + the dictionary chain.

On the FIRST Mine click the app builds services on the GUI thread, which
constructs ``fugashi.Tagger()`` (loads unidic-lite, ~0.5-2s) and opens every
installed dictionary's SQLite index. That freezes the first Mine for seconds.

This worker warms the relevant process/OS caches in a background thread right
after the main window first paints, so the first real Mine is materially
faster. It is *pure best-effort*:

- It builds (or warms, if already built) the SHARED ``get_shared_tagger()``
  singleton so the unidic-lite load + lattice init is paid here, on the
  shared tagger that mining actually reuses — not on a throwaway instance.
  Construction is double-checked-locked, so warming it from this thread while
  the GUI thread may also call ``get_shared_tagger()`` is safe; the
  single-flight invariant only forbids concurrent ``.parse()`` during a live
  mine, which a prewarm-before-first-mine never does.
- For the dictionary chain it constructs a THROWAWAY ``DictionaryRegistry`` to
  force the sqlite page cache / meta sidecars into memory, then discards it —
  cross-thread sqlite connection reuse during a live mine is needless risk.
- A failure inside :meth:`run` (e.g. missing MeCab dict in some env) is
  swallowed and logged, never crashing the app.

If the user clicks Mine before this finishes, behavior is exactly today's cold
path — there is no shared state and no synchronization with the mining path.

Completion is signalled via the built-in ``QThread.finished`` signal, which Qt
emits whenever :meth:`run` returns (every exit path), so callers can drop their
GC reference to the worker safely. We deliberately do not declare a custom
``finished`` ``pyqtSignal`` (that would shadow ``QThread.finished``, which the
codebase relies on for cleanup wiring — see ``dictionary_import_worker``).
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import build_definition_service
from anki_miner.services.tagger import get_shared_tagger

logger = logging.getLogger(__name__)


class PrewarmWorker(QThread):
    """Warm MeCab + dictionary-chain caches off the GUI thread, then discard.

    Emits the built-in ``QThread.finished`` signal on every exit path.
    """

    def __init__(self, config: AnkiMinerConfig, parent: object = None) -> None:
        """Initialize the prewarm worker.

        Args:
            config: The same mining configuration ``main()`` already holds.
                Used to locate the dictionary chain and dicts root.
            parent: Optional parent QObject.
        """
        super().__init__(parent)  # type: ignore[arg-type]
        self._config = config

    def run(self) -> None:
        """Warm caches best-effort. Any failure is logged and swallowed.

        The shared tagger singleton is built/warmed and intentionally retained
        (it is the instance mining reuses). The registry, providers and
        DefinitionService are throwaway — they go out of scope (and their
        sqlite handles close) when this method returns.
        """
        try:
            # Build the SHARED tagger singleton (the one mining reuses); this
            # loads unidic-lite, the dominant first-use cost. Do NOT discard it.
            # We deliberately do NOT run a warm `.parse()` here: a MeCab tagger
            # is not safe for concurrent `.parse()` on one instance, and a parse
            # on this background thread could race a mining worker's parse on the
            # same singleton if the user clicks Mine during prewarm (see the
            # single-flight note in services/tagger.py).
            get_shared_tagger()

            # Warm the sqlite page cache / meta sidecars for the configured
            # dictionary chain, then discard everything (no shared connections).
            # Shares the factory's gated eager-load: build_definition_service
            # only touches sqlite when an indexed entry is enabled, so a
            # Jisho-only chain warms nothing here, same as the real mine path.
            definition_service = build_definition_service(self._config)
            del definition_service
        except Exception:  # noqa: BLE001 - best-effort; never crash the app
            logger.debug("Prewarm failed (best-effort, ignored)", exc_info=True)
