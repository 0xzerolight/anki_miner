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
