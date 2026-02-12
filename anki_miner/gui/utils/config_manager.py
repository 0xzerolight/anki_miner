"""GUI configuration persistence manager."""

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from anki_miner.config import AnkiMinerConfig, create_default_config

logger = logging.getLogger(__name__)


class GUIConfigManager:
    """Manager for GUI configuration persistence.

    This class handles saving and loading user configuration to/from a JSON file
    stored in the user's home directory. It handles Path object serialization and
    provides fallback to default configuration if the file doesn't exist or is invalid.
    """

    CONFIG_FILE = Path.home() / ".anki_miner" / "gui_config.json"

    @classmethod
    def save_config(cls, config: AnkiMinerConfig) -> None:
        """Save configuration to JSON file.

        Args:
            config: Configuration to save

        Raises:
            OSError: If unable to create directory or write file
        """
        # Ensure directory exists
        cls.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Convert config to dict
        config_dict = asdict(config)

        # Convert Path objects to strings
        config_dict = cls._paths_to_strings(config_dict)

        # Write to file
        with cls.CONFIG_FILE.open("w", encoding="utf-8") as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)

    @classmethod
    def load_config(cls) -> AnkiMinerConfig:
        """Load configuration from JSON file.

        Returns:
            Loaded configuration, or default configuration if file doesn't exist

        Note:
            If the file exists but is invalid, falls back to default configuration
            and logs a warning.
        """
        if not cls.CONFIG_FILE.exists():
            return create_default_config()

        try:
            with cls.CONFIG_FILE.open("r", encoding="utf-8") as f:
                config_dict = json.load(f)

            # Convert string paths back to Path objects
            config_dict = cls._strings_to_paths(config_dict)

            # Create config from dict
            return AnkiMinerConfig(**config_dict)

        except (json.JSONDecodeError, TypeError, ValueError) as e:
            # If config is invalid, return default
            logger.warning(f"Invalid config file, using defaults: {e}")
            return create_default_config()

    @classmethod
    def config_exists(cls) -> bool:
        """Check if configuration file exists.

        Returns:
            True if config file exists, False otherwise
        """
        return cls.CONFIG_FILE.exists()

    @classmethod
    def delete_config(cls) -> None:
        """Delete the configuration file.

        This forces the application to use default configuration on next load.
        """
        if cls.CONFIG_FILE.exists():
            cls.CONFIG_FILE.unlink()

    @staticmethod
    def _paths_to_strings(data: dict[str, Any]) -> dict[str, Any]:
        """Convert Path objects to strings in a dict.

        Args:
            data: Dictionary potentially containing Path objects

        Returns:
            Dictionary with Path objects converted to strings
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, Path):
                result[key] = str(value)
            elif isinstance(value, dict):
                result[key] = GUIConfigManager._paths_to_strings(value)
            elif isinstance(value, list):
                result[key] = [str(item) if isinstance(item, Path) else item for item in value]
            else:
                result[key] = value
        return result

    @staticmethod
    def _strings_to_paths(data: dict[str, Any]) -> dict[str, Any]:
        """Convert string paths back to Path objects.

        Args:
            data: Dictionary with string paths

        Returns:
            Dictionary with appropriate strings converted to Path objects
        """
        # Keys that should be converted to Path objects
        path_keys = {
            "media_temp_folder",
            "jmdict_path",
        }

        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in path_keys and isinstance(value, str):
                result[key] = Path(value)
            elif isinstance(value, dict):
                result[key] = GUIConfigManager._strings_to_paths(value)
            else:
                result[key] = value
        return result
