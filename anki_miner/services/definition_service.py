"""Walk a configured list of DictionaryProvider implementations until one hits."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import ProgressCallback
from anki_miner.services.subtitle_parser import _differs_by_okurigana_only
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.interfaces import DictionaryProvider
    from anki_miner.services.dictionary.registry import DictionaryRegistry

logger = logging.getLogger(__name__)


def _word_unique_batches(
    pairs: list[tuple[str, str | None]],
) -> Iterator[list[tuple[str, str | None]]]:
    """Split pairs so each provider batch contains each word at most once."""
    pending = pairs
    while pending:
        batch: list[tuple[str, str | None]] = []
        deferred: list[tuple[str, str | None]] = []
        seen_words: set[str] = set()
        for pair in pending:
            word, _reading = pair
            if word in seen_words:
                deferred.append(pair)
                continue
            seen_words.add(word)
            batch.append(pair)
        yield batch
        pending = deferred


def collect_dictionary_css_entries(config: AnkiMinerConfig) -> list[tuple[str, str, str]]:
    """Collect ``(dict_id, display_name, scoped_css)`` for every enabled
    dictionary that ships a ``styles.css``.

    Builds the configured provider chain from disk, loads each provider, and
    gathers the per-dictionary scoped CSS (``IndexedDictProvider.dictionary_css``)
    in chain order. Both stable ``dict_id`` and ``display_name`` are retained:
    new envelopes match by ID, while pre-ID envelopes still match by title.
    Entries with no usable CSS are skipped (online providers, dicts without
    ``styles.css``), and the list is ORDERED with duplicates preserved:
    ``display_name`` is not guaranteed unique across providers, so this is
    deliberately not a dict.

    The result feeds each styled field's self-contained trailing ``<style>``
    block via ``card_style_block.attach_card_style_block`` at the
    ``EpisodeProcessor._phase5_create`` seam — this collection runs once per
    episode; blocks are assembled per card/field (Issue #93).

    Does light per-dictionary SQLite I/O (registry scan + ``read_meta`` +
    ``open_readonly`` via each provider's ``load()``), so it runs off the GUI
    thread (inside the card-creation worker). Returns ``[]`` when no enabled
    dictionary ships styles. Never raises: a provider that fails to load is
    skipped, mirroring the never-raises provider boundary elsewhere here. Each
    provider opened here is closed before returning so no ``index.sqlite`` handle
    leaks.
    """
    # Local import avoids any import-time coupling to the registry module.
    from anki_miner.services.dictionary.registry import DictionaryRegistry

    registry = DictionaryRegistry(config.dicts_root)
    registry.load()
    entries: list[tuple[str, str, str]] = []
    for provider in registry.build_provider_chain(config):
        css = ""
        try:
            provider.load()
            css = getattr(provider, "dictionary_css", "")
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Failed to collect CSS from provider '%s': %s", provider.name, e)
        finally:
            closer = getattr(provider, "close", None)
            if callable(closer):
                with contextlib.suppress(Exception):
                    closer()
        if css and css.strip():
            dict_id = getattr(provider, "dict_id", None)
            if isinstance(dict_id, str):
                entries.append((dict_id, provider.name, css.strip()))
    return entries


def collect_dictionary_css(config: AnkiMinerConfig) -> str:
    """Concatenate every enabled indexed dictionary's scoped ``styles.css``.

    Thin join over ``collect_dictionary_css_entries`` (byte-equivalent to the
    pre-entries implementation; pinned by test). Used where an unfiltered
    whole-config blob is still the right input (e.g. the restyler's
    envelope-stamping gate).
    """
    return "\n\n".join(css for _, _, css in collect_dictionary_css_entries(config))


class DefinitionService:
    """Look up definitions through an ordered provider chain.

    The chain is constructed externally (typically by DictionaryRegistry) and
    passed in. The service only walks it.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        providers: list[DictionaryProvider],
        *,
        registry: DictionaryRegistry | None = None,
    ):
        self.config = config
        self._providers = providers
        self._registry = registry
        self._loaded = False
        # Per-run cache for _provider_attest_quality, keyed on (id(provider),
        # include_readings). See clear_run_cache() for scope/lifetime. Using
        # id(provider) is safe because self._providers strong-holds all provider
        # objects for this instance's lifetime. Plain dict is fine unlocked
        # because a DefinitionService (like the rest of this class —
        # ensure_loaded/_loaded above included) is only ever touched from the
        # one worker thread processing a run.
        self._attest_cache: dict[tuple[int, bool], dict[str, dict[str, frozenset[str]]]] = {}

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

    def css_entries(self) -> list[tuple[str, str, str]]:
        """(dict_id, display_name, scoped_css) from the ALREADY-LOADED chain.

        Same filters and order as the scan-based ``collect_dictionary_css_entries``
        (non-str ``dict_id`` skipped, empty/blank CSS skipped, ``.strip()``
        applied, chain order) but reads straight off ``self._providers`` —
        no ``DictionaryRegistry`` construction, no per-dict SQLite open/close.
        This is the ``EpisodeProcessor._phase5_create`` seam (PB1): by Phase 5
        the processor already holds a fully loaded chain, so rescanning
        ``dicts_root`` and reopening every dict's ``index.sqlite`` per episode
        is pure waste.

        Known asymmetry vs. the scan-based collector: a provider whose
        ``load()`` failed at run start contributes no CSS here (its CSS
        attribute stays empty), whereas the scan-based collector calls
        ``load()`` fresh each time and would retry. Callers with no
        already-loaded chain (``card_restyler``, ``card_backfiller`` — once
        per run) should keep using ``collect_dictionary_css_entries``.
        """
        self.ensure_loaded()
        entries: list[tuple[str, str, str]] = []
        for provider in self._providers:
            css = getattr(provider, "dictionary_css", "")
            if not css or not css.strip():
                continue
            dict_id = getattr(provider, "dict_id", None)
            if isinstance(dict_id, str):
                entries.append((dict_id, provider.name, css.strip()))
        return entries

    def has_usable_offline_provider(self) -> bool:
        """Whether the loaded chain has an available, non-empty offline index.

        Provider availability alone is insufficient: Jisho is always available,
        and a schema-current index with zero declared entries opens normally. The
        registry snapshot that built this chain is therefore authoritative. No
        disk scan or metadata read occurs here.
        """
        if self._registry is None:
            return False
        self.ensure_loaded()
        for provider in self._available_offline_providers():
            dict_id = getattr(provider, "dict_id", None)
            if not isinstance(dict_id, str):
                continue
            meta = self._registry.get(dict_id)
            if meta is not None and meta.schema_ok and meta.entry_count > 0:
                return True
        return False

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

    @staticmethod
    def _fallback_candidates(word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        """Ordered ``(candidate_text, conditions)`` list for a lookup miss.

        Ported from Yomitan's lookup fan-out (``Translator._getAlgorithmDeinflections``
        + preprocessor variants, ext/js/language/translator.js, upstream e2ed450):
        a miss on the exact key is retried against spelling/kana variants and
        rule-driven deinflection hypotheses. Here:

        * A non-identical ``orth_base`` alternate is emitted with
          ``conditions=0`` only when it differs by trailing okurigana over the
          same kanji stem. UniDic lemmas that change kanji can name a different
          homograph and are skipped. Katakana/hiragana folds of ``word`` remain
          unconditional pure variants.
        * Deinflection hypotheses come from the already-loaded Japanese
          deinflector; each carries the terminal ``conditions`` bitmask used for
          the entry's rules-column POS check. They are pre-filtered by the
          ``cType`` condition mask (``ctype`` unknown ⇒ mask 0 ⇒ no filter, the
          user-input case) and ordered fewest-steps-first (Yomitan ranks by
          shortest inflection chain).

        The exact ``word`` is never re-emitted (already probed) and duplicates
        collapse to their first, highest-priority occurrence.
        """
        from anki_miner.services.deinflection import (
            conditions_match_mask,
            get_japanese_deinflector,
        )
        from anki_miner.utils.text_utils import hiragana_to_katakana, katakana_to_hiragana

        deinflector = get_japanese_deinflector()
        mask = deinflector.mask_for_ctype(ctype)
        candidates: list[tuple[str, int]] = []
        seen: set[str] = {word}  # never re-probe the exact key

        def _add(text: str, conditions: int) -> None:
            if text and text not in seen:
                seen.add(text)
                candidates.append((text, conditions))

        if orth_base and (orth_base == word or _differs_by_okurigana_only(word, orth_base)):
            _add(orth_base, 0)
        _add(katakana_to_hiragana(word), 0)
        _add(hiragana_to_katakana(word), 0)
        for result in sorted(deinflector.transform(word), key=lambda r: len(r.trace)):
            if conditions_match_mask(result.conditions, mask):
                _add(result.text, result.conditions)
        return candidates

    def _fallback_lookup_offline(
        self,
        word: str,
        orth_base: str,
        ctype: str | None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str | None:
        """First rules-validated fallback hit across offline providers, else None.

        Candidates are tried in priority order (variants, then fewest-step
        deinflections); for each, offline providers are walked in chain order and
        the first hit wins (mirrors ``get_definitions_batch`` first-hit-wins).
        Online providers and providers lacking ``lookup_fallback`` are skipped.
        Never raises: a provider that throws degrades to "skip + continue".
        """
        candidates = self._fallback_candidates(word, orth_base, ctype)
        if not candidates:
            return None
        for cand_text, cand_conditions in candidates:
            for provider in self._providers:
                if is_cancelled is not None and is_cancelled():
                    return None
                if provider.is_online or not provider.is_available():
                    continue
                fb = getattr(provider, "lookup_fallback", None)
                if not callable(fb):
                    continue
                try:
                    html: str | None = fb(cand_text, cand_conditions)
                except Exception as e:
                    logger.warning(
                        "Provider '%s' raised during lookup_fallback of '%s'; skipping: %s",
                        provider.name,
                        cand_text,
                        e,
                    )
                    continue
                if html:
                    return html
        return None

    def get_definitions_batch(
        self,
        words: list[tuple[str, str | None]],
        progress_callback: ProgressCallback | None = None,
        fallback_context: dict[str, tuple[str, str | None]] | None = None,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        lemma_context: dict[str, str] | None = None,
    ) -> list[str | None]:
        """Resolve definitions for a list of ``(word, reading | None)`` pairs,
        preserving first-hit-wins. The reading is a per-word ranking BOOST
        threaded to each offline provider's ``lookup_many`` (matching-reading
        senses lead; ``None`` = wildcard). Output stays a ``list[str | None]``
        aligned to the input pairs.

        Fast path: providers exposing the optional ``lookup_many`` batch method
        are queried once per word-unique sub-batch of still-unfilled pairs (one
        IN-clause SQLite query per sub-batch instead of one query per word).
        Words an earlier provider resolves are removed from the remaining set
        BEFORE the next provider is consulted, so chain semantics are
        first-hit-wins across the provider order. Providers without
        ``lookup_many`` (e.g. the online Jisho fallback) are consulted per-word
        for the remaining words.

        Lookup-miss fallback (plan item 5.2): ``fallback_context`` maps a lookup
        word to its ``(orth_base, cType)``. For any word STILL unresolved after
        the whole chain, the deinflection/variant fallback is retried against
        offline providers (miss-only, so the hot path pays nothing). A
        non-identical ``orth_base`` is intrinsically guarded to the same-kanji,
        okurigana-only case; callers may pass an empty alternate while retaining
        kana/deinflection fallback. Absent (``None``) ⇒ no fallback, preserving
        pre-5.2 behavior for callers that don't supply context.

        ``lemma_context`` maps a lookup word to its token's UniDic lemma,
        forwarded to each offline provider's ``lookup_many`` for the Rule A′
        homograph scope: a kana front (ゆう, lemma 言う) that resolves only via
        the folded-reading scan keeps its own lexeme's rows instead of the
        highest-scored same-reading homograph (有/夕/結う). Absent/empty ⇒
        providers are called with the legacy shape, so older stubs keep working.
        """
        if progress_callback:
            progress_callback.on_start(
                len(words),
                QCoreApplication.translate("DefinitionService", "Fetching definitions"),
            )

        self.ensure_loaded()

        cancelled = False

        def cancellation_requested() -> bool:
            nonlocal cancelled
            if is_cancelled is not None and is_cancelled():
                cancelled = True
            return cancelled

        # Reading is part of lookup identity. Exact duplicate pairs collapse,
        # but the same spelling with two readings must resolve twice.
        resolved: dict[tuple[str, str | None], str] = {}
        remaining = list(dict.fromkeys(words))

        # NOTE: the two ``except Exception`` clauses below are deliberately broad,
        # not an oversight. This is the never-raises provider boundary: a provider
        # (offline index, online Jisho, a user-imported dict) that raises an
        # UNANTICIPATED exception type must degrade to "miss + continue to the next
        # provider", never abort the whole mine. Narrowing to specific types would
        # let a single buggy/edge-case provider crash a run. Words it failed to
        # resolve fall through to the next provider, and any earlier hits are kept.
        for provider in self._providers:
            if not remaining or cancellation_requested():
                break
            if not provider.is_available():
                continue
            batch_fn = getattr(provider, "lookup_many", None)
            if callable(batch_fn):
                still_remaining: list[tuple[str, str | None]] = []
                batches = list(_word_unique_batches(remaining))
                for batch_index, batch in enumerate(batches):
                    if cancellation_requested():
                        break
                    # Legacy call shape when no lemma applies to this batch, so
                    # providers/stubs predating the ``lemmas`` kwarg keep working.
                    batch_lemmas = (
                        {w: lemma_context[w] for w, _ in batch if w in lemma_context} if lemma_context else {}
                    )
                    try:
                        hits = batch_fn(batch, lemmas=batch_lemmas) if batch_lemmas else batch_fn(batch)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup_many; skipping: %s",
                            provider.name,
                            e,
                        )
                        still_remaining.extend(batch)
                        for uncalled in batches[batch_index + 1 :]:
                            still_remaining.extend(uncalled)
                        break
                    for pair in batch:
                        word, _reading = pair
                        result = hits.get(word)
                        if result:
                            resolved[pair] = result
                        else:
                            still_remaining.append(pair)
                if cancelled:
                    break
                remaining = still_remaining
            else:
                # Per-word fallback for providers lacking the batch method (the
                # reading boost applies only to the offline batch path).
                still_remaining = []
                for pair in remaining:
                    if cancellation_requested():
                        break
                    word, _reading = pair
                    try:
                        result = provider.lookup(word)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            word,
                            e,
                        )
                        still_remaining.append(pair)
                        continue
                    if result:
                        resolved[pair] = result
                    else:
                        still_remaining.append(pair)
                if cancelled:
                    break
                remaining = still_remaining

        # Miss-only fallback: for words the whole chain left unresolved, retry
        # deinflection/variant candidates against offline providers. Gated on
        # fallback_context so the hot path (words that hit) pays nothing.
        if fallback_context:
            for pair in remaining:
                if cancellation_requested():
                    break
                word, _reading = pair
                ctx = fallback_context.get(word)
                if ctx is None:
                    continue
                orth_base, ctype = ctx
                html = self._fallback_lookup_offline(
                    word,
                    orth_base,
                    ctype,
                    is_cancelled,
                )
                if html:
                    resolved[pair] = html

        results: list[str | None] = []
        for i, pair in enumerate(words, 1):
            word, _reading = pair
            definition = resolved.get(pair)
            results.append(definition)
            if progress_callback and not cancelled:
                if definition:
                    progress_callback.on_progress(
                        i,
                        tr_format(
                            QCoreApplication.translate("DefinitionService", "Definition found: %1"),
                            word,
                        ),
                    )
                else:
                    progress_callback.on_progress(
                        i,
                        tr_format(
                            QCoreApplication.translate("DefinitionService", "No definition: %1"),
                            word,
                        ),
                    )

        if progress_callback and not cancellation_requested():
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
                    # Existence probe: no reading boost needed, so wildcard pairs.
                    # scope_homographs=False keeps the unfiltered term-OR-reading
                    # semantics — this is the gate AND the kana-recovery attest path
                    # (service_factory wires kana_attest_lookup to THIS method), so a
                    # kana-front word attested only via a kana-term reading row must
                    # survive; the render-path Rule A/B scope would drop it.
                    hits = batch_fn([(w, None) for w in remaining], scope_homographs=False)
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

    def offline_terms_exist(self, terms: list[str]) -> set[str]:
        """Union of exact-headword existence across available OFFLINE providers.

        Compound-matching probe: "does any enabled offline dictionary attest
        this string as a headword". Walks the chain like
        ``has_offline_definitions`` (offline-only, never raises), removing
        found terms before consulting the next provider — union-with-early-exit,
        equivalent to a full union for existence but cheaper.

        Per-word fallback is intentionally omitted (unlike the batch walk in
        ``has_offline_definitions``): every offline provider that can attest
        headwords implements ``has_terms``; the ``lookup`` fallback there exists
        for providers lacking ``lookup_many``, which does not apply here. A
        provider without ``has_terms`` simply attests nothing.
        """
        self.ensure_loaded()

        remaining = list(dict.fromkeys(terms))
        found: set[str] = set()

        for provider in self._providers:
            if not remaining:
                break
            if provider.is_online or not provider.is_available():
                continue
            has_terms_fn = getattr(provider, "has_terms", None)
            if not callable(has_terms_fn):
                continue
            try:
                hits = has_terms_fn(remaining)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during has_terms; skipping: %s",
                    provider.name,
                    e,
                )
                continue
            found.update(hits)
            remaining = [t for t in remaining if t not in hits]

        return found

    def offline_deinflection_terms_exist(self, candidates: list[tuple[str, int]]) -> set[str]:
        """Rules-compatible deinflection headwords across offline providers.

        ``candidates`` preserves each deinflection hypothesis's terminal
        condition mask. Entry rules come from the same ``attest_quality`` data
        and use the same flag/match helpers as ``IndexedDictProvider``'s fallback
        path. This probe is deliberately stricter for non-zero deinflections:
        ``rules=''`` means a non-inflecting entry and does not wildcard-match.
        The general definition fallback keeps its legacy ruleless-dictionary
        compatibility separately. Online/legacy providers are skipped; provider
        failures degrade to misses and never abort subtitle parsing.
        """
        from anki_miner.services.deinflection import condition_flags_from_rules, conditions_match

        self.ensure_loaded()

        conditions_by_term: dict[str, set[int]] = {}
        for term, conditions in dict.fromkeys(candidates):
            conditions_by_term.setdefault(term, set()).add(conditions)

        found: set[str] = set()
        terms = list(conditions_by_term)
        for provider in self._available_offline_providers():
            quality = self._provider_attest_quality(provider, terms, include_readings=False)
            for term, conditions_set in conditions_by_term.items():
                if term in found:
                    continue
                term_rules = quality.get(term, {}).get("term_rules", frozenset())
                if any(
                    conditions_match(conditions, condition_flags_from_rules(rules))
                    for rules in term_rules
                    for conditions in conditions_set
                ):
                    found.add(term)

        return found

    def offline_term_readings(self, terms: list[str]) -> dict[str, list[str]]:
        """Attested readings per headword across available OFFLINE providers.

        Reading-attestation probe for merged compounds
        (``morphology.attest_merged_readings``): "which readings does an
        enabled offline dictionary attest for this exact headword". Walks the
        chain exactly like :meth:`offline_terms_exist` — offline-only,
        ``ensure_loaded`` first, per-provider try/except so a provider failure
        can never raise (or reach the network) from inside subtitle parsing —
        with first-provider-wins semantics per term: once a chain member
        attests a term's readings, later providers are not consulted for it
        (chain order is the user's priority order).
        """
        self.ensure_loaded()

        remaining = list(dict.fromkeys(terms))
        found: dict[str, list[str]] = {}

        for provider in self._providers:
            if not remaining:
                break
            if provider.is_online or not provider.is_available():
                continue
            terms_readings_fn = getattr(provider, "terms_readings", None)
            if not callable(terms_readings_fn):
                continue
            try:
                hits = terms_readings_fn(remaining)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during terms_readings; skipping: %s",
                    provider.name,
                    e,
                )
                continue
            found.update(hits)
            remaining = [t for t in remaining if t not in hits]

        return found

    def offline_term_identities(
        self,
        pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], set[tuple[str, int, str]]]:
        """Exact dictionary identities for offline ``(term, reading)`` pairs.

        Identity is ``(dictionary_id, sequence, normalized_reading)``. Every
        available indexed provider is queried: short-circuiting a term after its
        first hit could hide the lower-priority dictionary that attests both
        orthographic aliases. Providers without the optional exact probe, online
        providers, and failures contribute nothing.
        """
        self.ensure_loaded()

        found: dict[tuple[str, str], set[tuple[str, int, str]]] = {}
        for provider in self._available_offline_providers():
            dict_id = getattr(provider, "dict_id", None)
            exact_sequences_fn = getattr(provider, "exact_term_sequences", None)
            if not isinstance(dict_id, str) or not callable(exact_sequences_fn):
                continue
            try:
                hits = exact_sequences_fn(pairs)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during exact_term_sequences; skipping: %s",
                    provider.name,
                    e,
                )
                continue
            for (term, reading), sequences in hits.items():
                identities = found.setdefault((term, reading), set())
                identities.update((dict_id, sequence, reading) for sequence in sequences)

        return found

    def _available_offline_providers(self) -> list[DictionaryProvider]:
        """Available, offline providers in chain order (commonness/quality probes)."""
        return [p for p in self._providers if not p.is_online and p.is_available()]

    @staticmethod
    def _provider_commonness_aware(provider: DictionaryProvider) -> bool:
        """Whether ``provider`` exposes a truthy ``commonness_aware`` property.

        Optional surface (like ``lookup_many`` / ``has_terms``): a provider
        lacking it — online Jisho, legacy dicts — is not aware. Never raises: a
        property that throws degrades to False."""
        try:
            return bool(getattr(provider, "commonness_aware", False))
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Provider '%s' raised reading commonness_aware; treating as unaware: %s", provider.name, e)
            return False

    def clear_run_cache(self) -> None:
        """Drop the per-run ``_provider_attest_quality`` cache.

        Called by ``EpisodeProcessor._run_pipeline`` at the end of every
        episode/reading item. A provider's dictionary content cannot change
        mid-run, so nothing here is invalidated by clearing — this exists
        purely to bound memory: ``SharedLookupServices`` keeps one
        ``DefinitionService`` alive across an entire multi-item batch, so
        without this the cache would accumulate every distinct surface ever
        probed across the whole batch instead of just the current item's
        vocabulary. That per-item ceiling is also why no size cap is needed
        (contrast the clear-on-cap memos in ``SubtitleParserService``, which
        bound a single corpus-wide instance): this cache clears out from under
        itself before the next item has a chance to grow it back.
        """
        self._attest_cache.clear()

    def _provider_attest_quality(
        self, provider: DictionaryProvider, words: list[str], include_readings: bool
    ) -> dict[str, dict[str, frozenset[str]]]:
        """Cached, never-raises wrapper over ``provider.attest_quality``.

        Cached per ``(provider, include_readings)`` for the run (see
        ``clear_run_cache``): ``offline_deinflection_terms_exist``,
        ``offline_term_commonness`` and ``offline_kana_attest_quality`` all
        read the same per-word rule sets off the same providers, so a word
        already probed by one is free to the others. Only words NOT already
        cached for this ``(provider, include_readings)`` pair trigger a
        provider call, and that call covers just the missing words — a later
        call that introduces new words does an incremental batch for the
        delta, never a re-probe of words already known.

        Providers without the optional method (or that raise) contribute
        nothing (empty entries for every requested word — and that miss is
        itself cached, since a provider that can't answer now won't answer
        later within the same run), so a single buggy provider can never
        abort the probe."""
        cached = self._attest_cache.setdefault((id(provider), include_readings), {})
        missing = [w for w in dict.fromkeys(words) if w not in cached]
        if missing:
            fn = getattr(provider, "attest_quality", None)
            fresh: dict[str, dict[str, frozenset[str]]] = {}
            if callable(fn):
                try:
                    fresh = fn(missing, include_readings)
                except Exception as e:
                    logger.warning("Provider '%s' raised during attest_quality; skipping: %s", provider.name, e)
            # A provider that raises is cached as an empty verdict for the rest
            # of the episode (sticky): fresh remains {} so .get(w, empty) fills
            # with empty entries. A future provider that raises transiently should
            # know this — one raise poisons the cache for the full run.
            empty = {"term_rules": frozenset[str](), "common_rules": frozenset[str]()}
            for w in missing:
                cached[w] = fresh.get(w, empty)
        return {w: cached[w] for w in words if w in cached}

    def offline_term_commonness(self, terms: list[str]) -> dict[str, bool] | None:
        """Whether each term is a COMMON headword in a commonness-aware offline dict.

        Foundation probe for the dict-commonness units (U10 infra). Returns
        ``None`` iff NO available offline provider is commonness-aware — the
        monolingual-only case, where the caller has no commonness signal and must
        stay byte-identical to pre-U10 behavior. Otherwise returns a dict keyed by
        the deduped terms: ``True`` iff some commonness-AWARE provider attests the
        term with a term-exact common row (``attest_quality`` ``common_rules``
        non-empty on the term-only, ``include_readings=False`` probe). Unaware
        providers are ignored; never raises (provider boundary)."""
        self.ensure_loaded()
        aware = [p for p in self._available_offline_providers() if self._provider_commonness_aware(p)]
        if not aware:
            return None
        deduped = list(dict.fromkeys(terms))
        result: dict[str, bool] = dict.fromkeys(deduped, False)
        for provider in aware:
            quality = self._provider_attest_quality(provider, deduped, include_readings=False)
            for term in deduped:
                if result[term]:
                    continue
                wq = quality.get(term)
                if wq and wq["common_rules"]:
                    result[term] = True
        return result

    def offline_kana_attest_quality(self, words: list[str]) -> dict[str, dict[str, frozenset[str]]] | None:
        """Term/common rule sets per word across offline dicts (kana-recovery quality).

        Foundation probe for the kana-recovery quality gate (U10 infra), run with
        the reading arm ON (``include_readings=True``). Returns ``None`` iff NO
        available offline provider is commonness-aware. Otherwise, per deduped
        word:

        * ``term_rules`` — union of ``attest_quality`` ``term_rules`` over ALL
          available offline providers (aware or not: term attestation does not
          need commonness tags).
        * ``common_rules`` — union of ``common_rules`` over commonness-AWARE
          providers only (an unaware dict's ``common_rules`` is empty regardless).

        Never raises (provider boundary)."""
        self.ensure_loaded()
        offline = self._available_offline_providers()
        aware = {id(p): self._provider_commonness_aware(p) for p in offline}
        if not any(aware.values()):
            return None
        deduped = list(dict.fromkeys(words))
        term_acc: dict[str, set[str]] = {w: set() for w in deduped}
        common_acc: dict[str, set[str]] = {w: set() for w in deduped}
        for provider in offline:
            quality = self._provider_attest_quality(provider, deduped, include_readings=True)
            provider_aware = aware[id(provider)]
            for word in deduped:
                wq = quality.get(word)
                if not wq:
                    continue
                term_acc[word].update(wq["term_rules"])
                if provider_aware:
                    common_acc[word].update(wq["common_rules"])
        return {w: {"term_rules": frozenset(term_acc[w]), "common_rules": frozenset(common_acc[w])} for w in deduped}

    def get_glossaries_batch(
        self,
        words: list[tuple[str, str | None]],
        progress_callback: ProgressCallback | None = None,
        *,
        is_cancelled: Callable[[], bool] | None = None,
        lemma_context: dict[str, str] | None = None,
    ) -> list[str | None]:
        """Collect glossary HTML for ``(word, reading | None)`` pairs, preserving
        input order. The reading is a per-word ranking BOOST threaded to each
        offline provider's ``lookup_many``.

        Fast path (OVH-050): offline providers that expose ``lookup_many`` are
        queried once per word-unique sub-batch (one IN-clause SQLite query per
        sub-batch instead of N per-word queries). Walk semantics:
        * Every available *offline* provider is queried in chain order; each
          provider's returned HTML is concatenated verbatim (each provider wraps
          its hit in ``<div class="yomitan-glossary">…</div>``, so the result is
          a sequence of those wrappers — compatible with the Senren toggle).
        * *Online* providers (e.g. Jisho) are consulted per-word only when no
          offline provider returned a hit for that word — they act as a fallback.
        Providers lacking ``lookup_many`` (e.g. legacy offline or online Jisho)
        are consulted per-word, matching the old behaviour.

        ``lemma_context`` mirrors ``get_definitions_batch``: word → token lemma,
        forwarded to batch-capable offline providers for the Rule A′ kana-front
        homograph scope; absent/empty keeps the legacy call shape.
        """
        if progress_callback:
            progress_callback.on_start(
                len(words),
                QCoreApplication.translate("DefinitionService", "Fetching glossary entries"),
            )

        self.ensure_loaded()

        cancelled = False

        def cancellation_requested() -> bool:
            nonlocal cancelled
            if is_cancelled is not None and is_cancelled():
                cancelled = True
            return cancelled

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

        # Exact duplicate pairs collapse; distinct readings stay separate.
        unique_pairs = list(dict.fromkeys(words))

        # Pair-keyed accumulator: each reading keeps its provider-ranked HTML.
        offline_hits: dict[tuple[str, str | None], list[str]] = {pair: [] for pair in unique_pairs}

        for provider in offline_providers:
            if cancellation_requested():
                break
            batch_fn = getattr(provider, "lookup_many", None)
            if callable(batch_fn):
                for batch in _word_unique_batches(unique_pairs):
                    if cancellation_requested():
                        break
                    # Same legacy-shape guard as get_definitions_batch.
                    batch_lemmas = (
                        {w: lemma_context[w] for w, _ in batch if w in lemma_context} if lemma_context else {}
                    )
                    try:
                        provider_results = batch_fn(batch, lemmas=batch_lemmas) if batch_lemmas else batch_fn(batch)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup_many; skipping: %s",
                            provider.name,
                            e,
                        )
                        break
                    for pair in batch:
                        word, _reading = pair
                        html = provider_results.get(word)
                        if html:
                            offline_hits[pair].append(html)
            else:
                for pair in unique_pairs:
                    if cancellation_requested():
                        break
                    word, _reading = pair
                    try:
                        html = provider.lookup(word)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            word,
                            e,
                        )
                        continue
                    if html:
                        offline_hits[pair].append(html)

        # Words with no offline hits fall back to online providers (per-word).
        online_results: dict[tuple[str, str | None], str | None] = {}
        for pair in unique_pairs:
            if cancellation_requested():
                break
            word, _reading = pair
            if not offline_hits[pair]:
                for provider in online_providers:
                    if cancellation_requested():
                        break
                    try:
                        html = provider.lookup(word)
                    except Exception as e:
                        logger.warning(
                            "Provider '%s' raised during lookup of '%s'; skipping: %s",
                            provider.name,
                            word,
                            e,
                        )
                        continue
                    if html:
                        online_results[pair] = html
                        break
                else:
                    online_results[pair] = None

        results: list[str | None] = []
        for i, pair in enumerate(words, 1):
            word, _reading = pair
            if offline_hits[pair]:
                glossary: str | None = "".join(offline_hits[pair])
            else:
                glossary = online_results.get(pair)
            results.append(glossary)
            if progress_callback and not cancelled:
                if glossary:
                    progress_callback.on_progress(
                        i,
                        tr_format(
                            QCoreApplication.translate("DefinitionService", "Glossary found: %1"),
                            word,
                        ),
                    )
                else:
                    progress_callback.on_progress(
                        i,
                        tr_format(
                            QCoreApplication.translate("DefinitionService", "No glossary: %1"),
                            word,
                        ),
                    )

        if progress_callback and not cancellation_requested():
            progress_callback.on_complete()
        return results

    def lookup_all_offline(self, word: str, lemma: str | None = None) -> list[tuple[str, str]]:
        """Aggregate results from all available OFFLINE providers.

        Returns a list of (provider_name, html) tuples for every offline
        provider that returns a hit, in chain order. Online providers (e.g.
        Jisho) are excluded to avoid blocking network I/O during interactive
        in-app dictionary lookup.

        Lookup-miss fallback (plan item 5.2) runs UNCONDITIONALLY here (not
        miss-only): after the exact-``word`` hit, each provider is also probed
        with the deinflection/variant candidates, so a pasted inflected form
        (食べさせられた → 食べる) or an orthography variant surfaces its base
        entry — reproducing Yomitan's core lookup UX in the in-app dialog. This
        is user input, so no orth_base/cType is available; the deinflector plus
        kana folds carry it. Each provider's fallback hits are appended after its
        exact hit, deduped by rendered HTML so a variant re-rendering the exact
        entry is not shown twice.

        Args:
            word: Japanese word (raw user input or a lemma form).
            lemma: the token's UniDic lemma, for the curator side pane's Rule
                A′ homograph scope (mirrors ``get_definitions_batch``'s
                ``lemma_context``, one word at a time) — the card and the pane
                beside it must agree on which lexeme a kana front (ゆう, lemma
                言う) names, not just the reading. When non-empty, the exact
                ``word`` hit is routed through a provider's optional
                ``lookup_many`` (via the same getattr probe used elsewhere) so
                the storage-side ``_homograph_keep_mask`` can prefer the
                lemma-exact rows; a provider without ``lookup_many`` (e.g. the
                online Jisho fallback, already excluded here, or a legacy
                offline stub) keeps the arity-1 ``lookup(word)`` path.
                ``None``/empty skips the probe entirely, so this is
                byte-identical to pre-A′ behavior for every existing caller.

        Returns:
            List of (provider_name, html) tuples in provider chain order. Empty
            list if no offline provider returns any (exact or fallback) hit.
        """
        self.ensure_loaded()
        candidates = self._fallback_candidates(word, "", None)
        out: list[tuple[str, str]] = []
        for p in self._providers:
            if p.is_online or not p.is_available():
                continue
            seen_html: set[str] = set()
            batch_fn = getattr(p, "lookup_many", None) if lemma else None
            try:
                if callable(batch_fn):
                    html = batch_fn([(word, None)], lemmas={word: lemma}).get(word)
                else:
                    html = p.lookup(word)
            except Exception as e:
                logger.warning(
                    "Provider '%s' raised during lookup of '%s'; skipping: %s",
                    p.name,
                    word,
                    e,
                )
                html = None
            if html:
                out.append((p.name, html))
                seen_html.add(html)
            fb = getattr(p, "lookup_fallback", None)
            if not callable(fb):
                continue
            for cand_text, cand_conditions in candidates:
                try:
                    fhtml = fb(cand_text, cand_conditions)
                except Exception as e:
                    logger.warning(
                        "Provider '%s' raised during lookup_fallback of '%s'; skipping: %s",
                        p.name,
                        cand_text,
                        e,
                    )
                    continue
                if fhtml and fhtml not in seen_html:
                    out.append((p.name, fhtml))
                    seen_html.add(fhtml)
        return out
