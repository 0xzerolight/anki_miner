"""Best-effort background warming of MeCab + the dictionary chain.

On the FIRST Mine click the app builds services on the GUI thread, which
constructs ``fugashi.Tagger()`` (loads unidic-lite, ~0.5-2s) and opens every
installed dictionary's SQLite index. That freezes the first Mine for seconds.

This worker warms the relevant process/OS caches in a background thread right
after the main window first paints, so the first real Mine is materially
faster. It is *pure best-effort*:

- It constructs a THROWAWAY ``fugashi.Tagger()`` and a THROWAWAY
  ``DictionaryRegistry`` solely to force unidic-lite load + lattice init and
  the sqlite page cache / meta sidecars into memory, then discards everything.
- It NEVER shares the warmed Tagger or any sqlite connection with the mining
  worker. ``fugashi.Tagger`` is not thread-safe and cross-thread connection
  reuse during a live mine is needless risk. We warm OS/process caches only.
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

import fugashi
from PyQt6.QtCore import QThread

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry

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

        Nothing constructed here is retained or shared — the local Tagger,
        registry, providers and DefinitionService go out of scope (and their
        sqlite handles close) when this method returns.
        """
        try:
            # Force unidic-lite load + lattice init into the process by running
            # the throwaway tagger once on a tiny string.
            tagger = fugashi.Tagger()
            tagger("ウォームアップ")
            del tagger

            # Warm the sqlite page cache / meta sidecars for the configured
            # dictionary chain, then discard everything (no shared connections).
            registry = DictionaryRegistry(self._config.dicts_root)
            registry.load()
            providers = registry.build_provider_chain(self._config)
            definition_service = DefinitionService(self._config, providers=providers)
            definition_service.ensure_loaded()
            del definition_service
            del providers
            del registry
        except Exception:  # noqa: BLE001 - best-effort; never crash the app
            logger.debug("Prewarm failed (best-effort, ignored)", exc_info=True)
