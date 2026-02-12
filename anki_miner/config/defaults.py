"""Default configuration values for Anki Miner."""

from .config import AnkiMinerConfig


def create_default_config(**overrides) -> AnkiMinerConfig:
    """Create a default configuration with optional overrides.

    Args:
        **overrides: Keyword arguments to override default values

    Returns:
        AnkiMinerConfig with defaults and overrides applied

    Example:
        config = create_default_config(
            subtitle_offset=-2.5,
            max_parallel_workers=4
        )
    """
    return AnkiMinerConfig(**overrides)
