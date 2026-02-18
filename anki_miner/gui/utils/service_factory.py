"""Factory for creating service instances used in episode processing."""

import logging

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.frequency_service import FrequencyService
from anki_miner.services.known_word_db import KnownWordDB
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.pitch_accent_service import PitchAccentService
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_list_service import WordListService

logger = logging.getLogger(__name__)


def create_services(config: AnkiMinerConfig) -> tuple:
    """Create all services needed for episode processing.

    Args:
        config: Mining configuration

    Returns:
        Tuple of (subtitle_parser, word_filter, media_extractor,
                  definition_service, anki_service,
                  pitch_accent_service, frequency_service)
    """
    subtitle_parser = SubtitleParserService(config)
    word_filter = WordFilterService(config)
    media_extractor = MediaExtractorService(config)
    definition_service = DefinitionService(config)
    anki_service = AnkiService(config)

    # Optional services
    pitch_accent_service = None
    if config.use_pitch_accent:
        try:
            pitch_accent_service = PitchAccentService(config.pitch_accent_path)
            pitch_accent_service.load()
        except Exception as e:
            logger.warning(f"Could not load pitch accent data: {e}")
            pitch_accent_service = None

    frequency_service = None
    if config.use_frequency_data:
        try:
            frequency_service = FrequencyService(config.frequency_list_path)
            frequency_service.load()
        except Exception as e:
            logger.warning(f"Could not load frequency data: {e}")
            frequency_service = None

    known_word_db = None
    if config.use_known_words_db:
        try:
            known_word_db = KnownWordDB(config.known_words_db_path)
            known_word_db.initialize()
        except Exception as e:
            logger.warning(f"Could not initialize known word database: {e}")
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
    )


def create_episode_processor(
    config: AnkiMinerConfig, presenter: PresenterProtocol
) -> EpisodeProcessor:
    """Create an EpisodeProcessor with all required services.

    Args:
        config: Mining configuration
        presenter: Output presenter for messages

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
    ) = create_services(config)

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
    )


def create_folder_processor(
    config: AnkiMinerConfig, presenter: PresenterProtocol
) -> FolderProcessor:
    """Create a FolderProcessor with all required services.

    Args:
        config: Mining configuration
        presenter: Output presenter for messages

    Returns:
        Configured FolderProcessor instance
    """
    episode_processor = create_episode_processor(config, presenter)
    return FolderProcessor(episode_processor=episode_processor, presenter=presenter)
