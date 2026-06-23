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

    def get_definitions_batch(
        self,
        words: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[str | None]:
        """Resolve definitions for a list of words, preserving first-hit-wins.

        Fast path: providers exposing the optional ``lookup_many`` batch method
        are queried ONCE for the still-unfilled words (one IN-clause SQLite
        query per dictionary instead of one query per word). Words an earlier
        provider resolves are removed from the remaining set BEFORE the next
        provider is consulted, so chain semantics are first-hit-wins across the
        provider order. Providers without ``lookup_many`` (e.g. the online Jisho
        fallback) are consulted per-word for the remaining words.
        """
        if progress_callback:
            progress_callback.on_start(len(words), "Fetching definitions")

        self.ensure_loaded()

        # Resolve over the chain into a per-word map keyed by the FIRST seen
        # occurrence — duplicates collapse to one lookup, mirroring the chain.
        resolved: dict[str, str | None] = {}
        remaining = list(dict.fromkeys(words))  # de-dup, preserve order

        # NOTE: the two ``except Exception`` clauses below are deliberately broad,
        # not an oversight. This is the never-raises provider boundary: a provider
        # (offline index, online Jisho, a user-imported dict) that raises an
        # UNANTICIPATED exception type must degrade to "miss + continue to the next
        # provider", never abort the whole mine. Narrowing to specific types would
        # let a single buggy/edge-case provider crash a run. Words it failed to
        # resolve fall through to the next provider, and any earlier hits are kept.
        for provider in self._providers:
            if not remaining:
                break
            if not provider.is_available():
                continue
            batch_fn = getattr(provider, "lookup_many", None)
            if callable(batch_fn):
                try:
                    hits = batch_fn(remaining)
                except Exception as e:
                    logger.warning(
                        "Provider '%s' raised during lookup_many; skipping: %s",
                        provider.name,
                        e,
                    )
                    continue
                still_remaining: list[str] = []
                for word in remaining:
                    result = hits.get(word)
                    if result:
                        resolved[word] = result
                    else:
                        still_remaining.append(word)
                remaining = still_remaining
            else:
                # Per-word fallback for providers lacking the batch method.
                still_remaining = []
                for word in remaining:
                    try:
                        result = provider.lookup(word)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            word,
                            e,
                        )
                        still_remaining.append(word)
                        continue
                    if result:
                        resolved[word] = result
                    else:
                        still_remaining.append(word)
                remaining = still_remaining

        results: list[str | None] = []
        for i, word in enumerate(words, 1):
            definition = resolved.get(word)
            results.append(definition)
            if progress_callback:
                if definition:
                    progress_callback.on_progress(i, f"Definition found: {word}")
                else:
                    progress_callback.on_progress(i, f"No definition: {word}")

        if progress_callback:
            progress_callback.on_complete()
        return results

    def has_offline_definitions(self, words: list[str]) -> dict[str, bool]:
        """Report which words have a definition in any OFFLINE provider.

        Offline-only existence probe used to drop no-definition words BEFORE
        the curation dialog (the curator must not surface words that can never
        become cards). Mirrors the fast-path structure of get_definitions_batch
        but excludes online providers (e.g. Jisho) so the check never blocks on
        network I/O — matching the offline-only contract of lookup_all_offline.

        A word is True iff some offline provider returns a truthy hit. The same
        never-raises provider boundary applies: a provider raising an
        unanticipated exception degrades to "miss + continue", never aborting.

        Known, intentional asymmetry vs. Phase 5: the actual card-build step uses
        get_definitions_batch over the FULL chain (online providers included). When
        a user enables Jisho, a word whose only definition is from Jisho is dropped
        by this probe before the curation dialog — accepted on purpose so the
        pre-curator filter never blocks on network I/O. Do not add online providers
        here to "close" the gap.

        Returns a dict keyed by the deduped input words; every input word is
        present exactly once.
        """
        self.ensure_loaded()

        deduped = list(dict.fromkeys(words))
        found: dict[str, bool] = dict.fromkeys(deduped, False)
        remaining = list(deduped)

        for provider in self._providers:
            if not remaining:
                break
            if provider.is_online or not provider.is_available():
                continue
            batch_fn = getattr(provider, "lookup_many", None)
            if callable(batch_fn):
                try:
                    hits = batch_fn(remaining)
                except Exception as e:
                    logger.warning(
                        "Provider '%s' raised during lookup_many; skipping: %s",
                        provider.name,
                        e,
                    )
                    continue
                still_remaining: list[str] = []
                for word in remaining:
                    if hits.get(word):
                        found[word] = True
                    else:
                        still_remaining.append(word)
                remaining = still_remaining
            else:
                still_remaining = []
                for word in remaining:
                    try:
                        result = provider.lookup(word)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            word,
                            e,
                        )
                        still_remaining.append(word)
                        continue
                    if result:
                        found[word] = True
                    else:
                        still_remaining.append(word)
                remaining = still_remaining

        return found

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
            try:
                result = provider.lookup(word)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during lookup of '%s'; skipping: %s",
                    provider.name,
                    word,
                    e,
                )
                continue
            if result:
                offline_hits.append(result)

        if offline_hits:
            return "".join(offline_hits)

        online_hits: list[str] = []
        for provider in online_providers:
            try:
                result = provider.lookup(word)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during lookup of '%s'; skipping: %s",
                    provider.name,
                    word,
                    e,
                )
                continue
            if result:
                online_hits.append(result)
        return "".join(online_hits) if online_hits else None

    def get_glossaries_batch(
        self,
        words: list[str],
        progress_callback: ProgressCallback | None = None,
    ) -> list[str | None]:
        """Batch variant of get_glossary; preserves input order.

        Fast path (OVH-050): offline providers that expose ``lookup_many`` are
        queried ONCE for all words (one IN-clause SQLite query per dictionary
        instead of N per-word queries). Walk semantics mirror ``get_glossary``:
        * Every available *offline* provider is queried; hits are concatenated.
        * *Online* providers are consulted per-word only when no offline provider
          returned a hit for that word — they act as a fallback.
        Providers lacking ``lookup_many`` (e.g. legacy offline or online Jisho)
        are consulted per-word, matching the old behaviour.
        """
        if progress_callback:
            progress_callback.on_start(len(words), "Fetching glossary entries")

        self.ensure_loaded()

        # Collect all available offline providers (batch-capable or per-word).
        offline_providers: list[DictionaryProvider] = []
        online_providers: list[DictionaryProvider] = []
        for provider in self._providers:
            if not provider.is_available():
                continue
            if provider.is_online:
                online_providers.append(provider)
            else:
                offline_providers.append(provider)

        # Per-word accumulator: word → list[str] of offline HTML hits.
        offline_hits: dict[str, list[str]] = {w: [] for w in words}

        for provider in offline_providers:
            batch_fn = getattr(provider, "lookup_many", None)
            if callable(batch_fn):
                try:
                    provider_results = batch_fn(words)
                except Exception as e:
                    logger.warning(
                        "Provider '%s' raised during lookup_many; skipping: %s",
                        provider.name,
                        e,
                    )
                    continue
                for w in words:
                    html = provider_results.get(w)
                    if html:
                        offline_hits[w].append(html)
            else:
                for w in words:
                    try:
                        html = provider.lookup(w)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            w,
                            e,
                        )
                        continue
                    if html:
                        offline_hits[w].append(html)

        # Words with no offline hits fall back to online providers (per-word).
        online_results: dict[str, str | None] = {}
        for w in words:
            if not offline_hits[w]:
                for provider in online_providers:
                    try:
                        html = provider.lookup(w)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            w,
                            e,
                        )
                        continue
                    if html:
                        online_results[w] = html
                        break
                else:
                    online_results[w] = None

        results: list[str | None] = []
        for i, word in enumerate(words, 1):
            if offline_hits[word]:
                glossary: str | None = "".join(offline_hits[word])
            else:
                glossary = online_results.get(word)
            results.append(glossary)
            if progress_callback:
                if glossary:
                    progress_callback.on_progress(i, f"Glossary found: {word}")
                else:
                    progress_callback.on_progress(i, f"No glossary: {word}")

        if progress_callback:
            progress_callback.on_complete()
        return results

    def lookup_all_offline(self, word: str) -> list[tuple[str, str]]:
        """Aggregate results from all available OFFLINE providers.

        Returns a list of (provider_name, html) tuples for every offline
        provider that returns a hit, in chain order. Online providers (e.g.
        Jisho) are excluded to avoid blocking network I/O during interactive
        in-app dictionary lookup.

        Args:
            word: Japanese word (typically lemma form).

        Returns:
            List of (provider_name, html) tuples, one per offline provider
            that returned a hit, in provider chain order. Empty list if no
            offline provider returns a hit.
        """
        self.ensure_loaded()
        out: list[tuple[str, str]] = []
        for p in self._providers:
            if p.is_online or not p.is_available():
                continue
            try:
                html = p.lookup(word)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during lookup of '%s'; skipping: %s",
                    p.name,
                    word,
                    e,
                )
                continue
            if html:
                out.append((p.name, html))
        return out
