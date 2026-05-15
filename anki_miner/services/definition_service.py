"""Walk a configured list of DictionaryProvider implementations until one hits."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback

if TYPE_CHECKING:
    from anki_miner.interfaces import DictionaryProvider

logger = logging.getLogger(__name__)


class DefinitionService:
    """Look up definitions through an ordered provider chain.

    The chain is constructed externally (typically by DictionaryRegistry) and
    passed in. The service only walks it.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        providers: list[DictionaryProvider],
    ):
        self.config = config
        self._providers = providers
        self._loaded = False

    def ensure_loaded(self) -> bool:
        """Call load() on every provider exactly once. Returns True if at
        least one provider became available."""
        if self._loaded:
            return any(p.is_available() for p in self._providers)
        self._loaded = True
        for provider in self._providers:
            try:
                provider.load()
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Failed to load provider '%s': %s", provider.name, e)
        return any(p.is_available() for p in self._providers)

    def get_definition(self, word: str) -> str | None:
        self.ensure_loaded()
        for provider in self._providers:
            if provider.is_available():
                result = provider.lookup(word)
                if result:
                    return result
        return None

    def get_definitions_batch(
        self,
        words: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[str | None]:
        if progress_callback:
            progress_callback.on_start(len(words), "Fetching definitions")

        results: list[str | None] = []
        for i, word in enumerate(words, 1):
            definition = self.get_definition(word)
            results.append(definition)
            if progress_callback:
                if definition:
                    progress_callback.on_progress(i, f"Definition found: {word}")
                else:
                    progress_callback.on_progress(i, f"No definition: {word}")

        if progress_callback:
            progress_callback.on_complete()
        return results
