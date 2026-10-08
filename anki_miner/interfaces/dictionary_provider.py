"""Protocol for dictionary lookup providers."""

from typing import Protocol


class DictionaryProvider(Protocol):
    """Interface for a dictionary backend that can look up word definitions.

    Any dictionary source (JMdict, Yomitan dictionaries, etc.)
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

    # NOTE: ``lookup_many`` is an OPTIONAL batch fast-path. It is intentionally
    # NOT part of the required Protocol surface — providers predating it do not
    # implement it. Consumers MUST probe for it at runtime
    # (``callable(getattr(provider, "lookup_many", None))``) and fall back to
    # per-word ``lookup`` when absent. An implementer's contract:
    #
    #     def lookup_many(
    #         self,
    #         pairs: list[tuple[str, str | None]],
    #         scope_homographs: bool = True,
    #         lemmas: dict[str, str] | None = None,
    #         pos: dict[str, str] | None = None,
    #     ) -> dict[str, str | None]:
    #         """Batch variant of ``lookup``. ``pairs`` is a list of
    #         ``(word, reading | None)`` — the reading is a per-word ranking BOOST
    #         (None = wildcard, no boost). With ``scope_homographs=True`` (default)
    #         the result for every word MUST be byte-identical to ``lookup(word)``
    #         with that word's boost applied (render-path homograph scoping ON);
    #         ``False`` keeps the unfiltered term-OR-reading semantics for the
    #         existence/attestation probes. ``lemmas`` (word → token lemma) feeds
    #         the Rule A′ kana-front scope and ``pos`` (word → token part of
    #         speech) the profile's row rank; callers pass each only when
    #         non-empty, so implementations predating the kwargs keep working.
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
    #
    # NOTE: ``lookup_fallback`` is a third OPTIONAL method (lookup-miss fallback,
    # plan item 5.2). Offline indexed providers implement it; consumers probe via
    # ``getattr`` and skip providers without it. Contract:
    #
    #     def lookup_fallback(self, word: str, conditions: int, pos: str | None = None) -> str | None:
    #         """Look up a deinflection/variant candidate, keeping only entries
    #         whose stored ``rules`` (POS) are compatible with the hypothesis
    #         ``conditions`` (0 = spelling/kana variant, passes unconditionally;
    #         empty rules accept unconditionally). Renders identically to
    #         ``lookup``. ``pos`` is the part of speech of the token the
    #         candidate came from, for the profile's form-row splice test;
    #         callers pass it only when known, so implementations predating the
    #         kwarg keep working. Never raises; degrade to None."""
