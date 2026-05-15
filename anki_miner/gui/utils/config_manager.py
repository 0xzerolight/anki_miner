"""GUI configuration persistence manager."""

import json
import logging
from dataclasses import asdict, fields
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

            # Migrate old field names
            config_dict = cls._migrate_field_names(config_dict)

            # Migrate stale allowed_pos defaults (pre-v2.3.2 missing 代名詞)
            config_dict = cls._migrate_allowed_pos(config_dict)

            # Migrate legacy dictionary fields → dictionary_chain
            config_dict = cls._migrate_dictionary_chain(config_dict)

            # Drop keys not in the current dataclass (e.g., removed fields from old
            # versions). Without this filter, AnkiMinerConfig(**config_dict) raises
            # TypeError and the except below would silently reset the entire user
            # config to defaults.
            valid_keys = {f.name for f in fields(AnkiMinerConfig)}
            dropped = set(config_dict) - valid_keys
            if dropped:
                logger.debug("Dropping unknown config keys: %s", sorted(dropped))
            config_dict = {k: v for k, v in config_dict.items() if k in valid_keys}

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

    # Pre-v2.3.2 default for allowed_pos (lacked 代名詞). Used to detect untouched
    # legacy configs we can safely migrate to the current default.
    _LEGACY_ALLOWED_POS: frozenset[str] = frozenset({"名詞", "動詞", "形容詞", "副詞", "形状詞"})

    @classmethod
    def _migrate_allowed_pos(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Replace stale pre-v2.3.2 allowed_pos default with the current default.

        Only fires when the saved list matches the legacy default exactly (set
        comparison so JSON ordering doesn't matter) AND 代名詞 is absent. User-
        edited lists are left untouched. The migration is in-memory only; the
        new value will persist the next time the user saves their config.
        """
        saved = data.get("allowed_pos")
        if not isinstance(saved, list):
            return data

        saved_set = set(saved)
        if saved_set == cls._LEGACY_ALLOWED_POS and "代名詞" not in saved_set:
            logger.info("Migrating allowed_pos: adding 代名詞 to enable pronoun mining")
            data["allowed_pos"] = list(create_default_config().allowed_pos)

        return data

    @staticmethod
    def _migrate_dictionary_chain(data: dict[str, Any]) -> dict[str, Any]:
        """Synthesize dictionary_chain when an older config lacks it.

        Legacy state mapped to chain entries:
          use_offline_dict=True  → jmdict-english enabled
          use_offline_dict=False → jmdict-english disabled (kept for re-enable)
        Jisho is always enabled in synthesized chains; users disable via UI.
        Existing dictionary_chain (loaded as list[dict]) is rebuilt into the
        ChainEntry dataclasses.
        """
        from anki_miner.config import ChainEntry

        raw_chain = data.get("dictionary_chain")
        if raw_chain is None:
            use_offline = bool(data.get("use_offline_dict", True))
            data["dictionary_chain"] = (
                ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=use_offline),
                ChainEntry(kind="jisho", dict_id=None, enabled=True),
            )
            return data

        # Rebuild ChainEntry instances from JSON dicts
        chain: list[ChainEntry] = []
        for item in raw_chain:
            if isinstance(item, dict):
                kind = item.get("kind")
                if kind in ("indexed", "jisho"):
                    chain.append(
                        ChainEntry(
                            kind=kind,
                            dict_id=item.get("dict_id"),
                            enabled=bool(item.get("enabled", True)),
                        )
                    )
            elif isinstance(item, ChainEntry):
                chain.append(item)
        data["dictionary_chain"] = tuple(chain)
        return data

    @staticmethod
    def _migrate_field_names(data: dict[str, Any]) -> dict[str, Any]:
        """Migrate old anki_fields keys to current names.

        Handles:
        - pitch_accent → pitch_position (value copied) + pitch_category (empty)
        - frequency_rank → frequency (value copied)
        """
        fields = data.get("anki_fields")
        if not isinstance(fields, dict):
            return data

        if "pitch_accent" in fields:
            fields["pitch_position"] = fields.pop("pitch_accent")
            fields.setdefault("pitch_category", "")

        if "frequency_rank" in fields:
            fields["frequency"] = fields.pop("frequency_rank")

        return data

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
            "pitch_accent_path",
            "frequency_list_path",
            "known_words_db_path",
            "blacklist_path",
            "whitelist_path",
            "stats_db_path",
            "history_db_path",
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
