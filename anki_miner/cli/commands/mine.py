"""CLI command for mining vocabulary from a single episode."""

from pathlib import Path

from anki_miner.config import create_default_config
from anki_miner.exceptions import AnkiMinerException
from anki_miner.orchestration import EpisodeProcessor
from anki_miner.presenters import ConsolePresenter, ConsoleProgressCallback
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    ValidationService,
    WordFilterService,
)


def mine_command(args) -> int:
    """Execute the mine subcommand.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 = success, 1 = failure)
    """
    # Create config
    config = create_default_config(subtitle_offset=args.offset)

    # Create presenter and progress callback
    presenter = ConsolePresenter()
    progress = ConsoleProgressCallback()

    presenter.show_info("Anki Miner - Vocabulary Mining Tool")
    presenter.show_info("=" * 50)

    # Convert paths
    video_file = Path(args.video)
    subtitle_file = Path(args.subtitle)

    # Validate files exist
    if not video_file.exists():
        presenter.show_error(f"Video file not found: {video_file}")
        return 1

    if not subtitle_file.exists():
        presenter.show_error(f"Subtitle file not found: {subtitle_file}")
        return 1

    # Create services
    subtitle_parser = SubtitleParserService(config)
    word_filter = WordFilterService(config)
    media_extractor = MediaExtractorService(config)
    definition_service = DefinitionService(config)
    anki_service = AnkiService(config)

    # Validate setup
    presenter.show_info("\nValidating setup...")
    validator = ValidationService(config)
    validation_result = validator.validate_setup()
    presenter.show_validation_result(validation_result)

    if not validation_result.all_passed:
        presenter.show_error("\nValidation failed. Please fix the issues above.")
        return 1

    # Load offline dictionary if enabled
    if config.use_offline_dict:
        try:
            presenter.show_info("\nLoading offline dictionary...")
            if definition_service.load_offline_dictionary():
                presenter.show_success("Offline dictionary loaded")
            else:
                presenter.show_info("Using Jisho API for definitions")
        except Exception as e:
            presenter.show_warning(f"Could not load offline dictionary: {e}")
            presenter.show_info("Falling back to Jisho API")

    # Create processor
    processor = EpisodeProcessor(
        config=config,
        subtitle_parser=subtitle_parser,
        word_filter=word_filter,
        media_extractor=media_extractor,
        definition_service=definition_service,
        anki_service=anki_service,
        presenter=presenter,
    )

    # Process episode
    try:
        presenter.show_info("\nProcessing episode...")
        result = processor.process_episode(
            video_file=video_file,
            subtitle_file=subtitle_file,
            preview_mode=args.preview,
            progress_callback=progress,
        )

        presenter.show_processing_result(result)

        if result.errors:
            return 1

        if args.preview:
            return 0

        return 0 if result.cards_created > 0 else 1

    except AnkiMinerException as e:
        presenter.show_error(f"Error: {e}")
        return 1
    except Exception as e:
        presenter.show_error(f"Unexpected error: {e}")
        return 1
