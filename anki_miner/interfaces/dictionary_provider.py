"""Protocol for dictionary lookup providers."""

from typing import Protocol


class DictionaryProvider(Protocol):
    """Interface for a dictionary backend that can look up word definitions.

    Any dictionary source (JMdict, Jisho API, custom dictionaries, etc.)
    implements this protocol to participate in the pluggable definition system.
    """

    @property
    def name(self) -> str:
        """Human-readable name for this provider (e.g., 'JMdict Offline')."""
        ...

    @property
    def is_online(self) -> bool:
        """Whether this provider performs network I/O for lookups.

        Online providers (Jisho) are used as a fallback in glossary
        collection: skipped when at least one offline provider returns
        a hit, to avoid per-word network latency.
        """
        ...

    def is_available(self) -> bool:
        """Check if this provider is ready to serve lookups."""
        ...

    def load(self) -> bool:
        """Initialize / load the provider's data.

        Returns:
            True if loading succeeded, False otherwise.

        Raises:
            SetupError: If loading fails and cannot be recovered.
        """
        ...

    def lookup(self, word: str) -> str | None:
        """Look up a single word definition.

        Args:
            word: Japanese word (typically lemma form).

        Returns:
            HTML-formatted definition string, or None if not found.
        """
        ...

    # NOTE: ``lookup_many`` is an OPTIONAL batch fast-path. It is intentionally
    # NOT part of the required Protocol surface — online providers (e.g. Jisho)
    # cannot batch and do not implement it. Consumers MUST probe for it at
    # runtime (``callable(getattr(provider, "lookup_many", None))``) and fall
    # back to per-word ``lookup`` when absent. An implementer's contract:
    #
    #     def lookup_many(
    #         self, pairs: list[tuple[str, str | None]]
    #     ) -> dict[str, str | None]:
    #         """Batch variant of ``lookup``. ``pairs`` is a list of
    #         ``(word, reading | None)`` — the reading is a per-word ranking BOOST
    #         (None = wildcard, no boost). The result for every word MUST be
    #         byte-identical to ``lookup(word)`` with that word's boost applied.
    #         Returns a dict keyed by every requested word; a miss maps to None."""
    #
    # NOTE: ``has_terms`` is a second OPTIONAL method (compound matching). Only
    # offline providers with an exact-headword index implement it; consumers
    # probe via ``getattr`` and treat absence as "attests nothing" (no per-word
    # fallback — see DefinitionService.offline_terms_exist). Contract:
    #
    #     def has_terms(self, terms: list[str]) -> set[str]:
    #         """Return the subset of ``terms`` that exist as exact headwords
    #         (term column, NOT reading). Never raises; degrade to empty set."""
