"""Factory for creating service instances used in episode processing."""

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.orchestration.folder_processor import FolderProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.word_filter import WordFilterService


def create_services(config: AnkiMinerConfig) -> tuple:
    """Create all services needed for episode processing.

    Args:
        config: Mining configuration

    Returns:
        Tuple of (subtitle_parser, word_filter, media_extractor,
                  definition_service, anki_service)
    """
    subtitle_parser = SubtitleParserService(config)
    word_filter = WordFilterService(config)
    media_extractor = MediaExtractorService(config)
    definition_service = DefinitionService(config)
    anki_service = AnkiService(config)

    return (subtitle_parser, word_filter, media_extractor, definition_service, anki_service)


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
    subtitle_parser, word_filter, media_extractor, definition_service, anki_service = (
        create_services(config)
    )

    return EpisodeProcessor(
        config=config,
        subtitle_parser=subtitle_parser,
        word_filter=word_filter,
        media_extractor=media_extractor,
        definition_service=definition_service,
        anki_service=anki_service,
        presenter=presenter,
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
