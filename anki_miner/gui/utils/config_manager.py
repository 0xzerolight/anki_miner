"""GUI configuration persistence manager."""

import json
import logging
import os
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.config.paths import ANKI_MINER_HOME

logger = logging.getLogger(__name__)


class GUIConfigManager:
    """Manager for GUI configuration persistence.

    This class handles saving and loading user configuration to/from a JSON file
    stored in the user's home directory. It handles Path object serialization and
    provides fallback to default configuration if the file doesn't exist or is invalid.
    """

    CONFIG_FILE = ANKI_MINER_HOME / "gui_config.json"

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

        # Atomic write: stage to a sibling .tmp then os.replace. A truncating
        # in-place write (open("w")) leaves invalid JSON if we crash or lose
        # power mid-serialize, which load_config then swallows into factory
        # defaults — wiping every user setting. Staging keeps the previous good
        # file intact until the new one is fully written; os.replace is atomic
        # on the same filesystem. The .tmp is unlinked if serialization raises
        # so a partial temp doesn't accumulate.
        tmp_path = cls.CONFIG_FILE.with_suffix(cls.CONFIG_FILE.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, cls.CONFIG_FILE)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

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
            default = create_default_config()
            # Pre-v2.5: theme was stored in QSettings. If a user upgrades from
            # such a build and has never saved any other GUI config, the file
            # won't exist yet — read QSettings and seed the default so they
            # don't lose their theme preference on first launch.
            from dataclasses import replace

            qs_theme = cls._read_qsettings_theme()
            if qs_theme is not None and qs_theme != default.theme:
                return replace(default, theme=qs_theme)
            return default

        try:
            with cls.CONFIG_FILE.open("r", encoding="utf-8") as f:
                config_dict = json.load(f)

            # Convert string paths back to Path objects
            config_dict = cls._strings_to_paths(config_dict)

            # Migrate old field names
            config_dict = cls._migrate_field_names(config_dict)

            # Migrate pre-preset card-styling boolean → preset id
            config_dict = cls._migrate_card_style_preset(config_dict)

            # Migrate stale allowed_pos defaults (pre-v2.3.2 missing 代名詞)
            config_dict = cls._migrate_allowed_pos(config_dict)

            # Migrate legacy dictionary fields → dictionary_chain
            config_dict = cls._migrate_dictionary_chain(config_dict)

            # Migrate theme key out of QSettings (only when the key is absent
            # from the loaded dict — i.e. first launch after v2.5 upgrade).
            config_dict = cls._migrate_theme_from_qsettings(config_dict)

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
        except OSError as e:
            # An unreadable file (permissions, transient I/O error) must not
            # crash startup — fall back to defaults like the invalid-JSON path.
            logger.warning(f"Could not read config file, using defaults: {e}")
            return create_default_config()

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
        """Rebuild ChainEntry instances when an existing dictionary_chain is
        loaded as list[dict] from JSON. Missing chains fall through to the
        dataclass defaults (jmdict-english + jisho).

        Also strips the obsolete ``use_offline_dict`` key, which was the
        pre-chain on/off toggle for the JMdict provider.
        """
        from anki_miner.config import ChainEntry

        # use_offline_dict is no longer a config field; drop it so the
        # AnkiMinerConfig constructor doesn't choke on unknown kwargs.
        data.pop("use_offline_dict", None)

        raw_chain = data.get("dictionary_chain")
        if raw_chain is None:
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
    def _read_qsettings_theme() -> str | None:
        """Read a legacy theme value from QSettings, returning None if absent.

        Pre-v2.5 the active theme was persisted as a QSettings key
        ``("AnkiMiner", "GUI", "theme")``. The new home is gui_config.json.
        This helper exists only to migrate older installs on upgrade.

        Imports PyQt6 lazily so the module is safely importable in non-GUI
        contexts (e.g. CLI invocations or tests that don't pull in Qt).
        """
        try:
            from PyQt6.QtCore import QSettings
        except Exception:  # pragma: no cover — Qt not installed in this env
            return None

        settings = QSettings("AnkiMiner", "GUI")
        if not settings.contains("theme"):
            return None
        value = settings.value("theme")
        if isinstance(value, str) and value:
            return value
        return None

    @classmethod
    def _migrate_theme_from_qsettings(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Inject the legacy QSettings theme value when missing from the dict.

        No-op when:
          * ``data`` already contains a ``theme`` key (user is on v2.5+), or
          * QSettings has no ``theme`` value (fresh install or never customised).
        """
        if "theme" in data:
            return data
        legacy = cls._read_qsettings_theme()
        if legacy is None:
            return data
        data["theme"] = legacy
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
    def _migrate_card_style_preset(data: dict[str, Any]) -> dict[str, Any]:
        """Map the pre-preset card-styling boolean to a preset id.

        Before card-style presets there was a single boolean
        ``use_default_card_stylesheet``. The new ``card_style_preset`` field
        carries a preset id instead, so an old config's ``True`` maps to
        ``"default"`` (the bundled stylesheet) and ``False`` maps to ``"none"``
        (custom CSS only). The legacy key is left in place — the valid-keys
        filter in ``load_config`` drops it. A config that already has
        ``card_style_preset`` (or neither key) is returned unchanged.
        """
        if "card_style_preset" in data:
            return data
        if "use_default_card_stylesheet" in data:
            data["card_style_preset"] = "default" if data["use_default_card_stylesheet"] else "none"
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
            "dicts_root",
            "pitch_accent_path",
            "frequency_list_path",
            "known_words_db_path",
            "blacklist_path",
            "whitelist_path",
            "stats_db_path",
            "history_db_path",
            "themes_root",
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
