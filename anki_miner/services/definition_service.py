"""Service for fetching word definitions using pluggable providers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback
from anki_miner.services.providers.jisho_provider import JishoProvider
from anki_miner.services.providers.jmdict_provider import JMdictProvider

if TYPE_CHECKING:
    from anki_miner.interfaces import DictionaryProvider

logger = logging.getLogger(__name__)


class DefinitionService:
    """Fetch word definitions using a chain of dictionary providers."""

    def __init__(
        self,
        config: AnkiMinerConfig,
        providers: list[DictionaryProvider] | None = None,
    ):
        """Initialize the definition service.

        Args:
            config: Configuration for definition lookup.
            providers: Ordered list of dictionary providers. If None,
                       default providers are built from config.
        """
        self.config = config
        self._custom_providers = providers is not None

        if providers is not None:
            self._providers: list[DictionaryProvider] = providers
        else:
            self._providers = self._build_default_providers()

        # Backwards-compatibility: track JMdict state for load_offline_dictionary()
        self._jmdict: dict[str, list[str]] | None = None

    def _build_default_providers(self) -> list[DictionaryProvider]:
        """Build the default provider chain from config settings."""
        providers: list[DictionaryProvider] = []

        if self.config.use_offline_dict:
            providers.append(JMdictProvider(self.config.jmdict_path))

        # Jisho is always available as potential fallback
        providers.append(JishoProvider(self.config.jisho_api_url, self.config.jisho_delay))

        return providers

    def load_providers(self) -> dict[str, bool]:
        """Load all providers that require initialization.

        Returns:
            Dict mapping provider name to load success status.
        """
        results = {}
        for provider in self._providers:
            try:
                success = provider.load()
                results[provider.name] = success
            except Exception as e:
                logger.warning(f"Failed to load provider '{provider.name}': {e}")
                results[provider.name] = False
        return results

    def load_offline_dictionary(self) -> bool:
        """Load offline dictionary (backwards-compatible wrapper).

        Returns:
            True if the offline dictionary loaded successfully.
        """
        if not self.config.use_offline_dict:
            return False

        self.load_providers()

        # Maintain backwards compat: update _jmdict reference
        for provider in self._providers:
            if isinstance(provider, JMdictProvider) and provider.is_available():
                self._jmdict = provider._dictionary
                return True

        return False

    def get_definition(self, word: str) -> str | None:
        """Get definition for a word.

        When using custom providers: tries each provider in order, returns first hit.
        When using default providers: preserves original fallback behavior where
        JMdict (when loaded) does not fall through to Jisho for missing words.

        Args:
            word: Japanese word to look up.

        Returns:
            HTML-formatted definition string, or None if not found.
        """
        if self._custom_providers:
            # Pluggable mode: simple chain - try each in order
            for provider in self._providers:
                if provider.is_available():
                    result = provider.lookup(word)
                    if result:
                        return result
            return None

        # Default mode: preserve original fallback semantics
        jmdict = None
        jisho = None
        for provider in self._providers:
            if isinstance(provider, JMdictProvider):
                jmdict = provider
            elif isinstance(provider, JishoProvider):
                jisho = provider

        # Try offline dictionary first
        if jmdict and jmdict.is_available():
            result = jmdict.lookup(word)
            if result:
                return result

        # Fallback to Jisho API only if offline dict is disabled or failed to load
        if (not self.config.use_offline_dict or not (jmdict and jmdict.is_available())) and jisho:
            return jisho.lookup(word)

        return None

    def get_definitions_batch(
        self,
        words: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[str | None]:
        """Get definitions for multiple words.

        Args:
            words: List of words to look up.
            progress_callback: Optional callback for progress reporting.

        Returns:
            List of definitions matching input order.
        """
        if progress_callback:
            progress_callback.on_start(len(words), "Fetching definitions")

        definitions = []
        for i, word in enumerate(words, 1):
            definition = self.get_definition(word)
            definitions.append(definition)

            if progress_callback:
                if definition:
                    progress_callback.on_progress(i, f"Definition found: {word}")
                else:
                    progress_callback.on_progress(i, f"No definition: {word}")

        if progress_callback:
            progress_callback.on_complete()

        return definitions
