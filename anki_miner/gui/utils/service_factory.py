"""Factory for creating service instances used in episode processing."""

import contextlib
import logging
from dataclasses import dataclass, field

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.interfaces.sentence_audio import SentenceAudioFetcher
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.audio_packs.fetcher import LocalAudioPackFetcher
from anki_miner.services.audio_packs.registry import AudioPackRegistry
from anki_miner.services.custom_audio_fetcher import CustomAudioFetcher, custom_audio_slug
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.expression_audio_fetcher import ChainedExpressionAudioFetcher, JPod101AudioFetcher
from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.google_translate_audio_fetcher import GoogleTranslateAudioFetcher
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.pitch_accent_service import PitchAccentService
from anki_miner.services.sentence_tts_fetcher import (
    ChainedSentenceAudioFetcher,
    GoogleSentenceTtsFetcher,
    PapagoSentenceTtsFetcher,
)
from anki_miner.services.stats_service import StatsService
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService
from anki_miner.services.wordset_service import WordsetService
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


def _tr(text: str) -> str:
    """Translate a user-facing service-load message under the ServiceFactory context."""
    return QCoreApplication.translate("ServiceFactory", text)


@dataclass
class ServiceLoadResult:
    """Result of loading optional services, including any warnings."""

    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Services:
    """Bundle of services required to construct an :class:`EpisodeProcessor`.

    Attribute names mirror the historical tuple-position names so callers
    that previously unpacked ``create_services(...)`` can switch to
    attribute access without renaming locals.
    """

    subtitle_parser: SubtitleParserService
    word_filter: WordFilterService
    media_extractor: MediaExtractorService
    definition_service: DefinitionService
    anki_service: AnkiService
    pitch_accent_service: PitchAccentService | None
    frequency_service: MultiFrequencyService | None
    known_word_db: KnownWordDB | None
    word_list_service: WordListService | None
    wordset_service: WordsetService | None
    youtube_fetcher: YouTubeFetcherService
    expression_audio_fetcher: ExpressionAudioFetcher
    # Sentence-TTS chain for reading sources (manga/novels). Non-Optional like
    # expression_audio_fetcher: a disabled feature yields an empty chain.
    sentence_audio_fetcher: SentenceAudioFetcher
    # Loaded dictionary registry (same handle that built the provider chain),
    # injected into the EpisodeProcessor so its per-slot DictMeta.schema_ok
    # backs the 4.0 staleness gate — NOT the built chain, which drops stale
    # slots and would make the gate never fire.
    dictionary_registry: DictionaryRegistry
    load_result: ServiceLoadResult


def _load_dict_registry(
    config: AnkiMinerConfig,
    load_result: ServiceLoadResult | None = None,
) -> DictionaryRegistry:
    """Construct + scan a :class:`DictionaryRegistry` for ``config.dicts_root``.

    Shared by :func:`build_definition_service` and :func:`create_services` so the
    disk scan happens once per service build and the same handle drives both the
    provider chain and the 4.0 staleness gate (``DictMeta.schema_ok``).
    """
    registry = DictionaryRegistry(config.dicts_root)
    try:
        registry.load()
    except OSError as e:
        # OSError here means the registry guard inside load() didn't catch it
        # (shouldn't happen after OVH-048 fix, but belt-and-suspenders).
        msg = f"Could not scan dictionaries folder: {e}"
        logger.warning(msg)
        if load_result is not None:
            load_result.warnings.append(tr_format(_tr("Couldn't scan dictionaries folder: %1"), e))
    return registry


def build_definition_service(
    config: AnkiMinerConfig,
    load_result: ServiceLoadResult | None = None,
    *,
    registry: DictionaryRegistry | None = None,
) -> DefinitionService:
    """Build the dictionary provider chain and its :class:`DefinitionService`.

    Constructs the registry, loads it, assembles the provider chain, and wraps
    it in a DefinitionService. When ``config.dictionary_chain`` has any enabled
    indexed entry, the chain is eagerly loaded (``ensure_loaded``) — this is the
    one path that touches sqlite, so it stays gated on having an indexed entry
    to keep a Jisho-only config I/O-free.

    Args:
        config: Mining configuration.
        load_result: Optional sink for human-readable load info/warnings
            (used by :func:`create_services`). ``None`` skips that reporting;
            the eager-load failure is then re-raised for the caller to handle.
        registry: Optional pre-loaded registry to reuse (``create_services``
            passes the one it also hands to the processor for the staleness
            gate). ``None`` builds + scans its own (the PrewarmWorker path).

    Returns:
        The constructed DefinitionService (loaded iff an indexed entry is on).
    """
    if registry is None:
        registry = _load_dict_registry(config, load_result)
    providers = registry.build_provider_chain(config)
    definition_service = DefinitionService(config, providers=providers)

    if any(e.kind == "indexed" and e.enabled for e in config.dictionary_chain):
        try:
            definition_service.ensure_loaded()
        except Exception as e:
            if load_result is None:
                raise
            logger.warning("Could not load dictionary chain: %s", e)
            load_result.warnings.append(tr_format(_tr("Couldn't load dictionary chain: %1"), e))
        else:
            if load_result is not None:
                available = [p.name for p in providers if p.is_available()]
                failed = [p.name for p in providers if not p.is_available()]
                if available:
                    load_result.info.append(tr_format(_tr("Dictionary chain loaded: %1"), ", ".join(available)))
                if failed:
                    load_result.warnings.append(
                        tr_format(_tr("Skipping unavailable provider(s): %1"), ", ".join(failed))
                    )
                if not available and not failed:
                    load_result.warnings.append(_tr("No offline dictionary index; using Jisho only"))

    return definition_service


def _build_pitch_service(
    config: AnkiMinerConfig,
    load_result: ServiceLoadResult,
) -> PitchAccentService | None:
    """Build + load the optional pitch accent service (full CSV parse).

    Extracted from :func:`create_services` so :func:`create_shared_lookup_services`
    constructs the identical service — single source of truth for the load,
    the entry-count info line, and the failure-to-warning downgrade.
    """
    if not config.pitch_active:
        return None
    try:
        pitch_accent_service = PitchAccentService(config.pitch_accent_path)
        pitch_accent_service.load()
        count = pitch_accent_service.entry_count
        if count > 0:
            load_result.info.append(tr_format(_tr("Pitch accent data loaded: %1 entries"), f"{count:,}"))
            return pitch_accent_service
        load_result.warnings.append(
            _tr("Pitch accent file has no valid entries (expected CSV/TSV: reading, kanji, pattern)")
        )
        return None
    except Exception as e:
        logger.warning(f"Could not load pitch accent data: {e}")
        load_result.warnings.append(tr_format(_tr("Couldn't load pitch accent data: %1"), e))
        return None


def _build_frequency_service(
    config: AnkiMinerConfig,
    load_result: ServiceLoadResult,
) -> MultiFrequencyService | None:
    """Build + load the optional multi-source frequency service.

    Extracted from :func:`create_services` for the same single-source-of-truth
    reason as :func:`_build_pitch_service`.
    """
    if not config.frequency_active:
        return None
    try:
        registry = FrequencySourceRegistry(config.freqs_root)
        registry.load()
        providers = [p for p in registry.build_sources(config) if p.load()]
        if not providers:
            # Nothing enabled / on-disk: no providers loaded. Not an error —
            # an enabled chain entry can still point at a missing on-disk index.
            return None
        frequency_service = MultiFrequencyService(providers)
        # Sum entry counts from the registry meta for the enabled chain
        # entries that actually produced a loaded provider. The provider
        # exposes .name (display) and .source_id, not the count — counts
        # live on FreqSourceMeta — so resolve each via registry.get().
        total_entries = sum(meta.entry_count for p in providers if (meta := registry.get(p.source_id)) is not None)
        load_result.info.append(
            tr_format(
                _tr("Frequency data loaded: %1 source(s), %2 entries"),
                len(providers),
                f"{total_entries:,}",
            )
        )
        return frequency_service
    except Exception as e:
        logger.warning(f"Could not load frequency data: {e}")
        load_result.warnings.append(tr_format(_tr("Couldn't load frequency data: %1"), e))
        return None


@dataclass(frozen=True)
class SharedLookupServices:
    """Offset-independent lookup services shared across one multi-item run.

    Owned by the worker that built it: an :class:`EpisodeProcessor`
    constructed over this bundle gets ``owns_lookup_services=False`` and must
    NOT close these services between items — the worker closes the bundle in a
    ``finally`` at end of run, preserving the Issue #30 (Windows file-lock)
    teardown guarantee at run granularity instead of item granularity.

    Bundle over loose injection kwargs: one param threads through both factory
    layers, the coordinated :meth:`close` is what the worker's ``finally``
    needs, and ``load_result`` carries the once-per-run load messages.
    """

    dictionary_registry: DictionaryRegistry
    definition_service: DefinitionService
    pitch_accent_service: PitchAccentService | None
    frequency_service: MultiFrequencyService | None
    load_result: ServiceLoadResult

    def close(self) -> None:
        """Release the bundle's sqlite handles. Idempotent, never raises.

        Closes the definition service's per-dict handles and every frequency
        provider's per-source handle (``MultiFrequencyService.close`` is itself
        idempotent/never-raises). The in-memory pitch service has no handles.
        """
        with contextlib.suppress(Exception):
            self.definition_service.close()
        if self.frequency_service is not None:
            # MultiFrequencyService.close is itself documented never-raises;
            # the suppress keeps this bundle's contract independent of that.
            with contextlib.suppress(Exception):
                self.frequency_service.close()


def create_shared_lookup_services(config: AnkiMinerConfig) -> SharedLookupServices:
    """Build the offset-independent lookup stack once for a multi-item run.

    Same construction as :func:`create_services` (shared private builders), so
    a bundle-backed processor behaves byte-identically to a per-item build:
    registry scan + eager dict load, pitch CSV parse, frequency registry load.
    The caller owns the bundle's lifetime — close it in a ``finally``.
    """
    load_result = ServiceLoadResult()
    dictionary_registry = _load_dict_registry(config, load_result)
    definition_service = build_definition_service(config, load_result, registry=dictionary_registry)
    return SharedLookupServices(
        dictionary_registry=dictionary_registry,
        definition_service=definition_service,
        pitch_accent_service=_build_pitch_service(config, load_result),
        frequency_service=_build_frequency_service(config, load_result),
        load_result=load_result,
    )


def _build_expression_audio_fetcher(
    config: AnkiMinerConfig,
    load_result: ServiceLoadResult | None = None,
) -> ExpressionAudioFetcher:
    """Build the expression audio fetcher chain from ``config.expression_audio_chain``.

    Constructs a :class:`~anki_miner.services.expression_audio_fetcher.ChainedExpressionAudioFetcher`
    whose members follow config order.  ``kind="jpod101"`` entries become
    :class:`~anki_miner.services.expression_audio_fetcher.JPod101AudioFetcher`;
    ``kind="googletts"`` entries become
    :class:`~anki_miner.services.google_translate_audio_fetcher.GoogleTranslateAudioFetcher`;
    ``kind="pack"`` entries are resolved against :class:`AudioPackRegistry`;
    ``kind="custom"``/``"custom_json"`` entries become
    :class:`~anki_miner.services.custom_audio_fetcher.CustomAudioFetcher` (cached
    under a per-URL ``audio_cache/custom_<slug>/`` dir).  These online fetchers
    open only a cheap ``requests.Session`` at build time (no disk scan), so no
    I/O gating is needed — and none is in the default chain, so a default config
    never constructs them.

    I/O neutrality: ``AudioPackRegistry`` is only constructed + loaded when the
    expression_audio Anki field is mapped (``config.anki_fields["expression_audio"]``
    non-empty) AND at least one enabled ``kind="pack"`` entry is present —
    mirrors the dictionary eager-load gating so a default (unmapped field) or
    jpod101-only config causes no disk access.  With the field unmapped the
    fetcher is never consulted (Phase 3 two-part gate), so pack entries are
    skipped silently; jpod101 entries are still constructed (I/O-free) to keep
    the chain shape uniform and ``Services.expression_audio_fetcher`` non-Optional.

    Args:
        config: Mining configuration.
        load_result: Optional sink for human-readable warnings (e.g. missing
            pack_id). ``None`` suppresses those messages; logger always fires.

    Returns:
        A :class:`ChainedExpressionAudioFetcher` wrapping the resolved list.
        The list may be empty (all entries disabled) — the chain returns None.
    """
    audio_cache_root = ANKI_MINER_HOME / "audio_cache"
    jpod_cache = audio_cache_root / "jpod101"
    googletts_cache = audio_cache_root / "googletts"
    pack_cache = audio_cache_root / "local_packs"

    # Build registry only when needed — avoids disk scan for default config
    # and when the expression-audio field is unmapped (fetcher never consulted).
    field_mapped = bool(config.anki_fields.get("expression_audio"))
    has_pack_entries = field_mapped and any(e.kind == "pack" and e.enabled for e in config.expression_audio_chain)
    pack_fetchers_by_id: dict[str, LocalAudioPackFetcher] = {}
    if has_pack_entries:
        registry = AudioPackRegistry(config.audio_packs_root)
        registry.load()
        for pack_fetcher in registry.build_fetcher_chain(config, pack_cache):
            pack_fetchers_by_id[pack_fetcher.pack_id] = pack_fetcher

    fetchers: list[ExpressionAudioFetcher] = []
    for entry in config.expression_audio_chain:
        if not entry.enabled:
            continue
        if entry.kind == "jpod101":
            fetchers.append(
                JPod101AudioFetcher(
                    cache_dir=jpod_cache,
                    delay=config.expression_audio_delay,
                )
            )
        elif entry.kind == "googletts":
            fetchers.append(
                GoogleTranslateAudioFetcher(
                    cache_dir=googletts_cache,
                    delay=config.expression_audio_delay,
                )
            )
        elif entry.kind in ("custom", "custom_json"):
            if not entry.url:
                msg = f"Skipping {entry.kind} audio chain entry with no URL"
                logger.warning(msg)
                if load_result is not None:
                    load_result.warnings.append(tr_format(_tr("Skipping %1 audio entry with no URL"), entry.kind))
                continue
            slug = custom_audio_slug(entry.url)
            fetchers.append(
                CustomAudioFetcher(
                    url_template=entry.url,
                    kind=entry.kind,
                    cache_dir=audio_cache_root / f"custom_{slug}",
                    file_prefix=f"custom_{slug}",
                    delay=config.expression_audio_delay,
                )
            )
        elif entry.kind == "pack":
            if not field_mapped:
                # Field unmapped → fetcher never consulted (Phase 3 two-part gate);
                # skip silently so a disabled feature surfaces no pack noise.
                continue
            if entry.pack_id is None:
                # warning already logged by registry.build_fetcher_chain
                if load_result is not None:
                    load_result.warnings.append(_tr("Skipping audio pack entry with no pack ID"))
                continue
            resolved_pack = pack_fetchers_by_id.get(entry.pack_id)
            if resolved_pack is None:
                # Registry skipped it (unknown/missing); warning already logged
                # there — add to load_result for UI surfacing.
                if load_result is not None:
                    load_result.warnings.append(tr_format(_tr("Audio pack '%1' unavailable; skipped"), entry.pack_id))
                continue
            fetchers.append(resolved_pack)  # duplicate pack_ids pass through (same object queried twice)

    return ChainedExpressionAudioFetcher(fetchers)


def _build_sentence_audio_fetcher(config: AnkiMinerConfig) -> SentenceAudioFetcher:
    """Build the sentence-TTS chain for reading sources.

    Fixed provider order (Google first, Papago fallback); the two config bools
    only select membership. I/O neutrality: with the master flag off the chain
    is returned empty immediately — no ``requests.Session`` (Papago) is ever
    constructed for a disabled feature, so a default config is byte-for-byte
    pre-feature. Constructors touch no disk or network (Session only); the
    cache dir is created lazily on first fetch.
    """
    if not config.reading_tts_enabled:
        return ChainedSentenceAudioFetcher([])

    cache_dir = ANKI_MINER_HOME / "audio_cache" / "sentence_tts"
    fetchers: list[SentenceAudioFetcher] = []
    if config.reading_tts_google_enabled:
        fetchers.append(GoogleSentenceTtsFetcher(cache_dir=cache_dir, delay=config.expression_audio_delay))
    if config.reading_tts_papago_enabled:
        fetchers.append(PapagoSentenceTtsFetcher(cache_dir=cache_dir, delay=config.expression_audio_delay))
    return ChainedSentenceAudioFetcher(fetchers)


def create_services(
    config: AnkiMinerConfig,
    subtitle_parser: SubtitleParserService | None = None,
    anki_service: AnkiService | None = None,
    shared_lookup: SharedLookupServices | None = None,
) -> Services:
    """Create all services needed for episode processing.

    Args:
        config: Mining configuration
        subtitle_parser: Optional pre-built parser to reuse instead of
            constructing a fresh one. The Deck Builder injects its Phase-1
            parser here so Phase-2 mining hits the already-filled per-file
            tokenization cache. The caller owns ensuring the parser's
            parse-relevant config matches ``config`` (offset / bold target /
            allowed POS / excluded subtypes / regex-filter fields); the parser
            reads only those, so reuse is byte-identical for a matching config.
        anki_service: Optional pre-built :class:`AnkiService` to reuse.
            When provided the existing instance (and its populated vocab cache)
            is reused rather than constructing a fresh one. The batch queue
            worker passes a single shared instance so the cache survives across
            all items in the run. Default ``None`` preserves single-episode and
            deck-builder behaviour (a fresh instance per call).
        shared_lookup: Optional pre-built :class:`SharedLookupServices` bundle.
            When provided, the dictionary registry scan, eager dictionary load,
            pitch CSV parse, and frequency registry load are all SKIPPED and
            the bundle's instances are used — the batch queue worker builds one
            bundle per run so N queue items pay one lookup-stack build instead
            of N. The bundle's owner (the worker) closes it; processors built
            over it must not (``owns_lookup_services=False``). The returned
            ``load_result`` then excludes the bundle's load messages — the
            owner surfaces those once per run.

    Returns:
        A frozen :class:`Services` bundle holding every constructed
        service plus a :class:`ServiceLoadResult` describing any
        warnings or info messages produced during optional-service
        initialization.
    """
    load_result = ServiceLoadResult()

    if shared_lookup is not None:
        dictionary_registry = shared_lookup.dictionary_registry
        definition_service = shared_lookup.definition_service
    else:
        # Scan the dictionary registry ONCE, then reuse the same handle for both the
        # provider chain and the EpisodeProcessor's staleness gate (4.0). Built
        # BEFORE the parser because the parser's compound matcher borrows the
        # DefinitionService's offline_terms_exist.
        dictionary_registry = _load_dict_registry(config, load_result)
        definition_service = build_definition_service(config, load_result, registry=dictionary_registry)

    if subtitle_parser is None:
        # Headword-existence probe: injected iff an indexed offline dict is
        # enabled (compound matching, services/compound_matcher.py, is always on)
        # — it borrows the DefinitionService's offline_terms_exist seam, so a
        # Jisho-only config stays I/O-free and behaves exactly as before.
        #
        # Deck Builder parity note: the Deck Builder's base processor flows
        # through THIS fresh-parser branch (it never pre-builds a parser), so
        # preview (count_lemmas) and build share the same probe via the parser's
        # line cache. If a future change pre-builds that parser elsewhere, it
        # must wire term_lookup the same way or preview and build diverge.
        has_indexed_dict = any(e.kind == "indexed" and e.enabled for e in config.dictionary_chain)
        term_lookup = definition_service.offline_terms_exist if has_indexed_dict else None
        # Attested-readings probe (merged-compound reading fix, audit F2):
        # gated ONLY on an indexed dict being present — the morphology merges it
        # corrects (noun-suffix/prefix/nominalizer) run regardless.
        reading_lookup = definition_service.offline_term_readings if has_indexed_dict else None
        # Reading-capable existence probe for pure-hiragana kana recovery (WS2):
        # term-OR-reading (has_offline_definitions), NOT the term-only
        # offline_terms_exist above — きれい is attested only as 綺麗's reading.
        # None ⇒ no offline dict ⇒ no recovery (safe degrade, pre-WS2 behavior).
        kana_attest_lookup = definition_service.has_offline_definitions if has_indexed_dict else None
        # Commonness probe for the verb-front resolver (U11): narrows the
        # deinflection override pool to headwords a commonness-aware offline dict
        # tags common. Gated the same way as the sibling probes; the probe itself
        # returns None when no chain member is commonness-aware (degrade).
        term_common_lookup = definition_service.offline_term_commonness if has_indexed_dict else None
        subtitle_parser = SubtitleParserService(
            config,
            term_lookup=term_lookup,
            reading_lookup=reading_lookup,
            kana_attest_lookup=kana_attest_lookup,
            term_common_lookup=term_common_lookup,
        )
    # Share the parser's tagger with the word filter so i+1 swap can
    # rebuild bolded sentence fields without spinning up a second tagger
    # (fugashi.Tagger initialization is non-trivial).
    word_filter = WordFilterService(config, tagger=subtitle_parser.tagger)
    media_extractor = MediaExtractorService(config)
    if anki_service is None:
        anki_service = AnkiService(config)
    youtube_fetcher = YouTubeFetcherService(config=config)
    expression_audio_fetcher = _build_expression_audio_fetcher(config, load_result)
    sentence_audio_fetcher = _build_sentence_audio_fetcher(config)

    # Optional services (reused from the bundle on the shared path).
    if shared_lookup is not None:
        pitch_accent_service = shared_lookup.pitch_accent_service
        frequency_service = shared_lookup.frequency_service
    else:
        pitch_accent_service = _build_pitch_service(config, load_result)
        frequency_service = _build_frequency_service(config, load_result)

    # Always construct the DB: the constructor is I/O-free and the user-curated
    # ignore list (source='user', Issue #42) must be applied on every run even
    # when use_known_words_db is off. Only eagerly initialize the file for the
    # sync cache; the curator/Manage dialog initialize lazily on first write so
    # users who never touch the feature get no empty file.
    known_word_db: KnownWordDB | None = None
    try:
        known_word_db = KnownWordDB(config.known_words_db_path)
        if config.use_known_words_db:
            known_word_db.initialize()
    except Exception as e:
        logger.warning(f"Could not initialize known word database: {e}")
        load_result.warnings.append(tr_format(_tr("Couldn't initialize known word database: %1"), e))
        known_word_db = None

    word_list_service = None
    if config.use_blacklist or config.use_whitelist:
        try:
            word_list_service = WordListService(
                blacklist_path=config.blacklist_path if config.use_blacklist else None,
                whitelist_path=config.whitelist_path if config.use_whitelist else None,
            )
            word_list_service.load()
        except Exception as e:
            logger.warning(f"Could not load word lists: {e}")
            load_result.warnings.append(tr_format(_tr("Couldn't load word lists: %1"), e))
            word_list_service = None

    wordset_service = None
    if config.excluded_wordsets:
        try:
            wordset_service = WordsetService(enabled_ids=config.excluded_wordsets)
            wordset_service.load()
            if wordset_service.is_available():
                load_result.info.append(
                    tr_format(_tr("Name wordsets loaded: %1 set(s) enabled"), len(config.excluded_wordsets))
                )
            else:
                wordset_service = None
        except Exception as e:
            logger.warning(f"Could not load name wordsets: {e}")
            load_result.warnings.append(tr_format(_tr("Couldn't load name wordsets: %1"), e))
            wordset_service = None

    return Services(
        subtitle_parser=subtitle_parser,
        word_filter=word_filter,
        media_extractor=media_extractor,
        definition_service=definition_service,
        anki_service=anki_service,
        pitch_accent_service=pitch_accent_service,
        frequency_service=frequency_service,
        known_word_db=known_word_db,
        word_list_service=word_list_service,
        wordset_service=wordset_service,
        youtube_fetcher=youtube_fetcher,
        expression_audio_fetcher=expression_audio_fetcher,
        sentence_audio_fetcher=sentence_audio_fetcher,
        dictionary_registry=dictionary_registry,
        load_result=load_result,
    )


def create_episode_processor(
    config: AnkiMinerConfig,
    presenter: PresenterProtocol,
    stats_service: StatsService | None = None,
    subtitle_parser: SubtitleParserService | None = None,
    anki_service: AnkiService | None = None,
    shared_lookup: SharedLookupServices | None = None,
) -> EpisodeProcessor:
    """Create an EpisodeProcessor with all required services.

    Args:
        config: Mining configuration
        presenter: Output presenter for messages
        stats_service: Optional statistics recording service
        subtitle_parser: Optional pre-built parser to reuse (see
            :func:`create_services`); the Deck Builder passes its Phase-1 parser
            here to reuse the filled tokenization cache in Phase 2.
        anki_service: Optional pre-built :class:`AnkiService` to reuse across
            multiple calls (see :func:`create_services`). The batch queue worker
            passes a single shared instance to preserve the populated vocab
            cache across all queue items. Default ``None`` builds a fresh one.
        shared_lookup: Optional shared lookup bundle (see
            :func:`create_services`). When provided the processor is built with
            ``owns_lookup_services=False`` so its ``close()`` leaves the
            bundle's sqlite handles for the owning worker's ``finally``.

    Returns:
        Configured EpisodeProcessor instance
    """
    services = create_services(
        config, subtitle_parser=subtitle_parser, anki_service=anki_service, shared_lookup=shared_lookup
    )

    # Surface service load feedback to the user
    for msg in services.load_result.info:
        presenter.show_info(msg)
    for msg in services.load_result.warnings:
        presenter.show_warning(msg)

    return EpisodeProcessor(
        config=config,
        subtitle_parser=services.subtitle_parser,
        word_filter=services.word_filter,
        media_extractor=services.media_extractor,
        definition_service=services.definition_service,
        anki_service=services.anki_service,
        presenter=presenter,
        pitch_accent_service=services.pitch_accent_service,
        frequency_service=services.frequency_service,
        known_word_db=services.known_word_db,
        word_list_service=services.word_list_service,
        wordset_service=services.wordset_service,
        stats_service=stats_service,
        youtube_fetcher=services.youtube_fetcher,
        expression_audio_fetcher=services.expression_audio_fetcher,
        sentence_audio_fetcher=services.sentence_audio_fetcher,
        dictionary_registry=services.dictionary_registry,
        owns_lookup_services=shared_lookup is None,
    )


def create_youtube_fetcher(config: AnkiMinerConfig) -> YouTubeFetcherService:
    """Create a standalone YouTubeFetcherService for the YouTube tab.

    Args:
        config: Mining configuration

    Returns:
        Configured YouTubeFetcherService instance
    """
    return YouTubeFetcherService(config=config)
