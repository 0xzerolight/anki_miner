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

    def close(self) -> None:
        """Close every provider that exposes a ``close()`` method.

        Needed so the GUI can release per-dict ``index.sqlite`` handles before
        deleting a dictionary folder — on Windows, an open SQLite connection
        keeps a file lock that blocks ``rmtree`` (Issue #30). The Protocol
        does not require ``close``; probe via ``getattr`` so providers without
        it (e.g. Jisho) are silently skipped. Resets ``_loaded`` so a later
        ``ensure_loaded()`` will re-open the chain cleanly.
        """
        for provider in self._providers:
            closer = getattr(provider, "close", None)
            if not callable(closer):
                continue
            try:
                closer()
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Failed to close provider '%s': %s", provider.name, e)
        self._loaded = False

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

    def get_glossary(self, word: str) -> str | None:
        """Collect hits from all enabled providers and concatenate as one HTML blob.

        Walk semantics:
        * Every available *offline* provider is queried in chain order.
        * *Online* providers (e.g. Jisho) are queried only if no offline
          provider returned a hit — they act as a fallback, matching the
          single-definition chain's existing semantics.
        * Each provider's returned HTML is concatenated verbatim. Each
          provider already wraps its hit in
          ``<div class="yomitan-glossary"><ol><li data-dictionary="…">…</li></ol></div>``
          so the result is a sequence of those wrappers — compatible with
          the Senren dictionary-toggle.

        Returns the concatenated HTML, or None when no provider hit.
        """
        self.ensure_loaded()
        offline_hits: list[str] = []
        online_providers: list[DictionaryProvider] = []
        for provider in self._providers:
            if not provider.is_available():
                continue
            if provider.is_online:
                online_providers.append(provider)
                continue
            result = provider.lookup(word)
            if result:
                offline_hits.append(result)

        if offline_hits:
            return "".join(offline_hits)

        online_hits: list[str] = []
        for provider in online_providers:
            result = provider.lookup(word)
            if result:
                online_hits.append(result)
        return "".join(online_hits) if online_hits else None

    def get_glossaries_batch(
        self,
        words: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[str | None]:
        """Batch variant of get_glossary; preserves input order."""
        if progress_callback:
            progress_callback.on_start(len(words), "Fetching glossary entries")

        results: list[str | None] = []
        for i, word in enumerate(words, 1):
            glossary = self.get_glossary(word)
            results.append(glossary)
            if progress_callback:
                if glossary:
                    progress_callback.on_progress(i, f"Glossary found: {word}")
                else:
                    progress_callback.on_progress(i, f"No glossary: {word}")

        if progress_callback:
            progress_callback.on_complete()
        return results
