"""Factory for creating service instances used in episode processing."""

import logging

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.frequency_service import FrequencyService
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.pitch_accent_service import PitchAccentService
from anki_miner.services.stats_service import StatsService
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.word_filter import WordFilterService

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

    return (
        subtitle_parser,
        word_filter,
        media_extractor,
        definition_service,
        anki_service,
        pitch_accent_service,
        frequency_service,
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
        stats_service=stats_service,
    )


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
