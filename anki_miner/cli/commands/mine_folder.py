"""CLI command for mining vocabulary from a folder of episodes."""

from pathlib import Path

from anki_miner.config import create_default_config
from anki_miner.exceptions import AnkiMinerException
from anki_miner.orchestration import EpisodeProcessor, FolderProcessor
from anki_miner.presenters import ConsolePresenter, ConsoleProgressCallback
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    ValidationService,
    WordFilterService,
)


def mine_folder_command(args) -> int:
    """Execute the mine-folder subcommand.

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

    presenter.show_info("Anki Miner - Folder Processing Tool")
    presenter.show_info("=" * 50)

    # Convert path
    folder = Path(args.folder)

    # Validate folder exists
    if not folder.exists():
        presenter.show_error(f"Folder not found: {folder}")
        return 1

    if not folder.is_dir():
        presenter.show_error(f"Path is not a directory: {folder}")
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
        except Exception as e:
            presenter.show_warning(f"Could not load offline dictionary: {e}")

    # Load pitch accent data if enabled
    pitch_accent_service = None
    if config.use_pitch_accent:
        try:
            from anki_miner.services.pitch_accent_service import PitchAccentService

            pitch_accent_service = PitchAccentService(config.pitch_accent_path)
            presenter.show_info("Loading pitch accent data...")
            if pitch_accent_service.load():
                presenter.show_success("Pitch accent data loaded")
        except Exception as e:
            presenter.show_warning(f"Could not load pitch accent data: {e}")
            pitch_accent_service = None

    # Load frequency data if enabled
    frequency_service = None
    if config.use_frequency_data:
        try:
            from anki_miner.services.frequency_service import FrequencyService

            frequency_service = FrequencyService(config.frequency_list_path)
            presenter.show_info("Loading frequency data...")
            if frequency_service.load():
                presenter.show_success("Frequency data loaded")
        except Exception as e:
            presenter.show_warning(f"Could not load frequency data: {e}")
            frequency_service = None

    # Create processors
    episode_processor = EpisodeProcessor(
        config=config,
        subtitle_parser=subtitle_parser,
        word_filter=word_filter,
        media_extractor=media_extractor,
        definition_service=definition_service,
        anki_service=anki_service,
        presenter=presenter,
        pitch_accent_service=pitch_accent_service,
        frequency_service=frequency_service,
    )

    folder_processor = FolderProcessor(
        episode_processor=episode_processor,
        presenter=presenter,
    )

    # Process folder
    try:
        results = folder_processor.process_folder(
            folder=folder,
            preview_mode=args.preview,
            progress_callback=progress,
        )

        # Calculate totals
        total_cards = sum(r.cards_created for r in results)
        total_errors = sum(len(r.errors) for r in results)

        if total_errors > 0:
            return 1

        return 0 if total_cards > 0 or args.preview else 1

    except AnkiMinerException as e:
        presenter.show_error(f"Error: {e}")
        return 1
    except Exception as e:
        presenter.show_error(f"Unexpected error: {e}")
        return 1
