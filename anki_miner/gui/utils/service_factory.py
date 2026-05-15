"""Factory for creating service instances used in episode processing."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.registry import DictionaryRegistry
from anki_miner.services.frequency_service import FrequencyService
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.pitch_accent_service import PitchAccentService
from anki_miner.services.stats_service import StatsService
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

logger = logging.getLogger(__name__)


@dataclass
class ServiceLoadResult:
    """Result of loading optional services, including any warnings."""

    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)


def create_services(config: AnkiMinerConfig) -> tuple:
    """Create all services needed for episode processing.

    Args:
        config: Mining configuration

    Returns:
        Tuple of (subtitle_parser, word_filter, media_extractor,
                  definition_service, anki_service,
                  pitch_accent_service, frequency_service,
                  known_word_db, word_list_service, youtube_fetcher,
                  load_result)
    """
    load_result = ServiceLoadResult()

    subtitle_parser = SubtitleParserService(config)
    word_filter = WordFilterService(config)
    media_extractor = MediaExtractorService(config)

    # Build the provider chain via the registry, then hand it to DefinitionService.
    dicts_root = Path.home() / ".anki_miner" / "dicts"
    registry = DictionaryRegistry(dicts_root)
    providers = registry.build_provider_chain(config)
    definition_service = DefinitionService(config, providers=providers)
    anki_service = AnkiService(config)
    youtube_fetcher = YouTubeFetcherService(config=config)

    has_indexed_entry = any(e.kind == "indexed" and e.enabled for e in config.dictionary_chain)
    if has_indexed_entry:
        try:
            if definition_service.ensure_loaded():
                load_result.info.append("Dictionary chain loaded")
            else:
                load_result.warnings.append(
                    "No dictionary provider is available; lookups will return None"
                )
        except Exception as e:
            logger.warning(f"Could not load dictionary providers: {e}")
            load_result.warnings.append(f"Could not load dictionary providers: {e}")

    # Optional services
    pitch_accent_service = None
    if config.use_pitch_accent:
        try:
            pitch_accent_service = PitchAccentService(config.pitch_accent_path)
            pitch_accent_service.load()
            count = pitch_accent_service.entry_count
            if count > 0:
                load_result.info.append(f"Pitch accent data loaded: {count:,} entries")
            else:
                load_result.warnings.append(
                    "Pitch accent file loaded but contained 0 valid entries. "
                    "Expected CSV/TSV format: reading, kanji, pattern (3 columns)."
                )
                pitch_accent_service = None
        except Exception as e:
            logger.warning(f"Could not load pitch accent data: {e}")
            load_result.warnings.append(f"Could not load pitch accent data: {e}")
            pitch_accent_service = None

    frequency_service = None
    if config.use_frequency_data:
        try:
            frequency_service = FrequencyService(config.frequency_list_path)
            frequency_service.load()
            count = frequency_service.entry_count
            if count > 0:
                load_result.info.append(f"Frequency data loaded: {count:,} entries")
            else:
                load_result.warnings.append(
                    "Frequency file loaded but contained 0 valid entries. "
                    "Expected CSV/TSV format: rank, word OR word, rank (2 columns)."
                )
                frequency_service = None
        except Exception as e:
            logger.warning(f"Could not load frequency data: {e}")
            load_result.warnings.append(f"Could not load frequency data: {e}")
            frequency_service = None

    known_word_db = None
    if config.use_known_words_db:
        try:
            known_word_db = KnownWordDB(config.known_words_db_path)
            known_word_db.initialize()
        except Exception as e:
            logger.warning(f"Could not initialize known word database: {e}")
            load_result.warnings.append(f"Could not initialize known word database: {e}")
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
            load_result.warnings.append(f"Could not load word lists: {e}")
            word_list_service = None

    return (
        subtitle_parser,
        word_filter,
        media_extractor,
        definition_service,
        anki_service,
        pitch_accent_service,
        frequency_service,
        known_word_db,
        word_list_service,
        youtube_fetcher,
        load_result,
    )


def create_episode_processor(
    config: AnkiMinerConfig,
    presenter: PresenterProtocol,
    stats_service: StatsService | None = None,
) -> EpisodeProcessor:
    """Create an EpisodeProcessor with all required services.

    Args:
        config: Mining configuration
        presenter: Output presenter for messages
        stats_service: Optional statistics recording service

    Returns:
        Configured EpisodeProcessor instance
    """
    (
        subtitle_parser,
        word_filter,
        media_extractor,
        definition_service,
        anki_service,
        pitch_accent_service,
        frequency_service,
        known_word_db,
        word_list_service,
        youtube_fetcher,
        load_result,
    ) = create_services(config)

    # Surface service load feedback to the user
    for msg in load_result.info:
        presenter.show_info(msg)
    for msg in load_result.warnings:
        presenter.show_warning(msg)

    return EpisodeProcessor(
        config=config,
        subtitle_parser=subtitle_parser,
        word_filter=word_filter,
        media_extractor=media_extractor,
        definition_service=definition_service,
        anki_service=anki_service,
        presenter=presenter,
        pitch_accent_service=pitch_accent_service,
        frequency_service=frequency_service,
        known_word_db=known_word_db,
        word_list_service=word_list_service,
        stats_service=stats_service,
        youtube_fetcher=youtube_fetcher,
    )


def create_youtube_fetcher(config: AnkiMinerConfig) -> YouTubeFetcherService:
    """Create a standalone YouTubeFetcherService for the YouTube tab.

    Args:
        config: Mining configuration

    Returns:
        Configured YouTubeFetcherService instance
    """
    return YouTubeFetcherService(config=config)


def create_folder_processor(
    config: AnkiMinerConfig,
    presenter: PresenterProtocol,
    stats_service: StatsService | None = None,
) -> FolderProcessor:
    """Create a FolderProcessor with all required services.

    Args:
        config: Mining configuration
        presenter: Output presenter for messages
        stats_service: Optional statistics recording service

    Returns:
        Configured FolderProcessor instance
    """
    episode_processor = create_episode_processor(config, presenter, stats_service)
    return FolderProcessor(episode_processor=episode_processor, presenter=presenter)
