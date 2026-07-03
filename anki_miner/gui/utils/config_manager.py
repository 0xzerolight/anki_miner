"""GUI configuration persistence manager."""

import dataclasses
import json
import logging
import os
import shutil
import types
import typing
from dataclasses import fields
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

        Writes atomically (temp + os.replace) and rotates the prior good config
        to ``gui_config.json.bak`` before each overwrite, so the previous
        contents survive one bad write (one-overwrite recovery).

        Args:
            config: Configuration to save

        Raises:
            OSError: If unable to create directory or write file
        """
        # Ensure directory exists
        cls.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Convert config to dict.
        # anki_fields is stored as MappingProxyType (for immutability).
        # dataclasses.asdict uses deepcopy on non-dataclass fields, and
        # MappingProxyType is not picklable/deepcopy-able in CPython —
        # so we use a custom serializer instead of asdict.
        config_dict = cls._config_to_serializable_dict(config)

        # Convert Path objects to strings
        config_dict = cls._paths_to_strings(config_dict)

        # Atomic write: stage to a sibling .tmp then os.replace. A truncating
        # in-place write (open("w")) leaves invalid JSON if we crash or lose
        # power mid-serialize, which load_config then swallows into factory
        # defaults — wiping every user setting. Staging keeps the previous good
        # file intact until the new one is fully written; os.replace is atomic
        # on the same filesystem. The .tmp is unlinked if serialization raises
        # so a partial temp doesn't accumulate.
        #
        # Backup rotation: right before os.replace clobbers the existing file,
        # copy the still-good current config to a sibling .bak (one-overwrite
        # recovery — config isn't in git and os.replace keeps no backup, so a
        # bad write once nuked a user's settings with no way back). copy2 runs
        # inside the try, so if it fails we unlink the .tmp and re-raise without
        # touching CONFIG_FILE — the original survives intact.
        tmp_path = cls.CONFIG_FILE.with_suffix(cls.CONFIG_FILE.suffix + ".tmp")
        bak_path = cls.CONFIG_FILE.with_name(cls.CONFIG_FILE.name + ".bak")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            # First-ever save has nothing to back up — skip silently.
            if cls.CONFIG_FILE.exists():
                shutil.copy2(cls.CONFIG_FILE, bak_path)
            os.replace(tmp_path, cls.CONFIG_FILE)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def _parse_and_migrate(cls, path: Path) -> AnkiMinerConfig:
        """Parse a config JSON file and run all migration steps.

        Raises:
            json.JSONDecodeError: If the file contains invalid JSON.
            TypeError, ValueError: If the parsed data cannot be coerced into
                AnkiMinerConfig (e.g. wrong types, unexpected structure).
            OSError: If the file cannot be read.
        """
        with path.open("r", encoding="utf-8") as f:
            config_dict = json.load(f)

        # Convert string paths back to Path objects
        config_dict = cls._strings_to_paths(config_dict)

        # Migrate old field names
        config_dict = cls._migrate_field_names(config_dict)

        # Backfill any anki_fields keys that are new since the config was saved
        config_dict = cls._backfill_anki_fields(config_dict)

        # Fold legacy card-styling preset config into the manage_card_styling bool
        config_dict = cls._migrate_card_styling(config_dict)

        # One-shot re-seed: flip a persisted False→True for the v2.7.6/2.7.7
        # OFF-default era (a bare default flip can't reach them). Runs after
        # _migrate_card_styling so the key is settled before the era check.
        config_dict = cls._reseed_manage_card_styling(config_dict)

        # Migrate stale allowed_pos defaults (pre-v2.3.2 missing 代名詞)
        config_dict = cls._migrate_allowed_pos(config_dict)

        # Migrate legacy dictionary fields → dictionary_chain
        config_dict = cls._migrate_dictionary_chain(config_dict)

        # Migrate expression_audio_chain JSON dicts → AudioSourceEntry
        config_dict = cls._migrate_expression_audio_chain(config_dict)

        # Migrate frequency_chain JSON dicts → FreqEntry
        config_dict = cls._migrate_frequency_chain(config_dict)

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

        return AnkiMinerConfig(**config_dict)

    @classmethod
    def load_config(cls) -> AnkiMinerConfig:
        """Load configuration from JSON file.

        Returns:
            Loaded configuration, or default configuration if file doesn't exist

        Note:
            If the file exists but is invalid, attempts recovery from the .bak
            file before falling back to default configuration.
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
            return cls._parse_and_migrate(cls.CONFIG_FILE)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning("gui_config.json invalid (%s); attempting .bak recovery", e)
        except OSError as e:
            # An unreadable file (permissions, transient I/O error) must not
            # crash startup — try .bak before falling back to defaults.
            logger.warning("gui_config.json unreadable (%s); attempting .bak recovery", e)

        # One .bak attempt — no loop.
        bak_path = cls.CONFIG_FILE.with_name(cls.CONFIG_FILE.name + ".bak")
        try:
            config = cls._parse_and_migrate(bak_path)
            logger.warning("gui_config.json corrupt; recovered from .bak")
            return config
        except (json.JSONDecodeError, TypeError, ValueError, OSError) as bak_err:
            logger.warning("gui_config.json.bak also unusable (%s); using defaults", bak_err)
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
    def _migrate_expression_audio_chain(data: dict[str, Any]) -> dict[str, Any]:
        """Rebuild AudioSourceEntry instances when an existing expression_audio_chain
        is loaded as list[dict] from JSON. Missing chains fall through to the
        dataclass default (jpod101 enabled + googletts disabled = pre-feature behaviour).
        """
        from anki_miner.config import AudioSourceEntry

        raw_chain = data.get("expression_audio_chain")
        if raw_chain is None:
            return data

        chain: list[AudioSourceEntry] = []
        for item in raw_chain:
            if isinstance(item, dict):
                kind = item.get("kind")
                if kind in (
                    "pack",
                    "jpod101",
                    "googletts",
                    "custom",
                    "custom_json",
                    "jpod101_scrape",
                    "jisho_scrape",
                ):
                    chain.append(
                        AudioSourceEntry(
                            kind=kind,
                            pack_id=item.get("pack_id"),
                            url=item.get("url"),
                            enabled=bool(item.get("enabled", True)),
                        )
                    )
            elif isinstance(item, AudioSourceEntry):
                chain.append(item)
        # Append-if-missing: existing users whose persisted chain predates
        # googletts gain a disabled entry so the Settings UI can list it.
        # Disabled-by-default => factory skips it => pre-feature behaviour
        # preserved; the entry only needs to exist for the UI.
        if not any(entry.kind == "googletts" for entry in chain):
            chain.append(AudioSourceEntry(kind="googletts", enabled=False))
        data["expression_audio_chain"] = tuple(chain)
        return data

    @staticmethod
    def _migrate_frequency_chain(data: dict[str, Any]) -> dict[str, Any]:
        """Rebuild FreqEntry instances when an existing frequency_chain is
        loaded as list[dict] from JSON. A missing chain falls through to the
        dataclass default (empty tuple — no frequency sources).

        Malformed entries (non-dict items, missing/empty source_id) are dropped;
        items already constructed as FreqEntry pass through unchanged.
        """
        from anki_miner.config import FreqEntry

        raw_chain = data.get("frequency_chain")
        if raw_chain is None:
            return data

        chain: list[FreqEntry] = []
        for item in raw_chain:
            if isinstance(item, FreqEntry):
                chain.append(item)
            elif isinstance(item, dict):
                source_id = item.get("source_id")
                if isinstance(source_id, str) and source_id:
                    chain.append(
                        FreqEntry(
                            source_id=source_id,
                            enabled=bool(item.get("enabled", True)),
                        )
                    )
        data["frequency_chain"] = tuple(chain)
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
    def _backfill_anki_fields(data: dict[str, Any]) -> dict[str, Any]:
        """Merge in any anki_fields keys introduced after the config was saved.

        Old saved configs have an anki_fields dict that lacks keys added in newer
        versions (e.g. ``expression_audio``). Without this merge, loading such a
        config would silently drop the new key, causing KeyError or missing
        functionality downstream. The default value for each missing key is taken
        from the dataclass default factory so this stays in sync automatically.
        """
        saved = data.get("anki_fields")
        defaults = create_default_config().anki_fields
        if not isinstance(saved, dict):
            # null, string, or any non-dict value from a corrupt/legacy config:
            # replace with the full defaults so __post_init__ never sees a
            # non-dict anki_fields.
            if "anki_fields" in data:
                data["anki_fields"] = dict(defaults)
            return data

        for key, default_value in defaults.items():
            saved.setdefault(key, default_value)
        return data

    @staticmethod
    def _migrate_card_styling(data: dict[str, Any]) -> dict[str, Any]:
        """Fold legacy card-styling config into the ``manage_card_styling`` bool.

        Deterministic, no Anki probe. Chained, in order (skipped if a config
        already carries ``manage_card_styling``):

        1. Pre-preset boolean ``use_default_card_stylesheet`` (only when no
           ``card_style_preset`` key exists): BOTH values historically wrote a
           managed block (``True`` → ``"default"``, ``False`` → ``"none"``, and
           ``"none"`` still writes a block), so either way the user was managing
           the note type → ``manage_card_styling = True``.
        2. ``card_style_preset`` → bool: ``"off"`` (or empty) → ``False``; any
           other value (``default``/``minimal``/``none``/``yomitan-classic``/
           unknown) → ``True``.

        Configs with neither key are left without ``manage_card_styling`` so
        they inherit the dataclass default (True since v2.7.8). The legacy keys
        (``card_style_preset``, ``card_style_migrated``,
        ``use_default_card_stylesheet``) are left in place; the valid-keys filter
        in ``load_config`` drops them.

        Kept intentionally unchanged when the key is already present so that a
        config already carrying ``manage_card_styling`` is untouched here — the
        one-shot v2.7.6/2.7.7 re-seed to the new ON default lives in the separate
        ``_reseed_manage_card_styling`` step (run immediately after this one).
        """
        if "manage_card_styling" in data:
            return data
        if "card_style_preset" not in data and "use_default_card_stylesheet" in data:
            data["manage_card_styling"] = True
        elif "card_style_preset" in data:
            data["manage_card_styling"] = data["card_style_preset"] not in ("off", "")
        return data

    # Versions that shipped the Issue #44 card-styling rework with
    # ``manage_card_styling`` defaulting OFF. A config last saved by one of these
    # has ``False`` baked into gui_config.json (save_config serializes every
    # field, and the post-update version write / closeEvent persist the full
    # config), which ``_migrate_card_styling``'s present-key early-return would
    # otherwise preserve forever. ``last_known_version`` has shipped since v2.3.3,
    # so every config from this era is guaranteed to carry it.
    _STYLING_OFF_DEFAULT_ERA: frozenset[str] = frozenset({"2.7.6", "2.7.7"})

    @classmethod
    def _reseed_manage_card_styling(cls, data: dict[str, Any]) -> dict[str, Any]:
        """One-shot re-seed of a persisted ``manage_card_styling=False`` to True.

        Fires only for configs last saved by the OFF-default era (v2.7.6/2.7.7),
        detected via ``last_known_version``. A bare flip of the dataclass default
        is inert for these users because their ``False`` is already on disk and
        ``_migrate_card_styling`` respects the present key; this rescues them so
        post-rework cards regain styling without a manual toggle.

        One-shot and idempotent: at load time ``last_known_version`` on disk is
        still the OFF-era value; the version write advances it to the running
        version *after* ``load_config``, so this never fires again. Mirrors
        ``_migrate_allowed_pos`` — it acts only on the exact legacy-default state
        (``is False`` + era version), never on a value a user set under the new
        default (whose ``last_known_version`` is outside the era) or an explicit
        enable (already True). See config.py ``manage_card_styling``.

        Owned tradeoff: the v2.7.7 checkbox was default-OFF, so on disk a
        deliberate opt-out is indistinguishable from never-touched; this flips
        both to ON. Acceptable because the write is purely additive (user CSS
        byte-preserved) and one checkbox reverts it.
        """
        if data.get("manage_card_styling") is False and data.get("last_known_version") in cls._STYLING_OFF_DEFAULT_ERA:
            logger.info("Re-seeding manage_card_styling to True (v2.7.6/2.7.7 OFF-default era)")
            data["manage_card_styling"] = True
        return data

    @staticmethod
    def _config_to_serializable_dict(config: AnkiMinerConfig) -> dict[str, Any]:
        """Convert an AnkiMinerConfig to a plain dict suitable for JSON serialization.

        Unlike ``dataclasses.asdict``, this handles the MappingProxyType stored
        in ``anki_fields`` (asdict deepcopies non-dataclass fields and
        MappingProxyType is not deepcopy-able in CPython).  All other fields are
        handled exactly as asdict would: dataclass instances are recursed into,
        tuples and lists are element-wise converted.
        """

        def _to_serializable(value: Any) -> Any:
            if isinstance(value, types.MappingProxyType):
                return {k: _to_serializable(v) for k, v in value.items()}
            if dataclasses.is_dataclass(value) and not isinstance(value, type):
                return {f.name: _to_serializable(getattr(value, f.name)) for f in dataclasses.fields(value)}
            if isinstance(value, (list, tuple)):
                return [_to_serializable(item) for item in value]
            return value

        return {f.name: _to_serializable(getattr(config, f.name)) for f in fields(config)}

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
    def _path_field_names() -> frozenset[str]:
        """Return the set of AnkiMinerConfig field names whose type is Path or Path | None.

        Derived from the dataclass type annotations at call time so it stays in
        sync with the dataclass automatically — no hand-maintained list that can
        drift as new Path fields are added.
        """
        hints = typing.get_type_hints(AnkiMinerConfig)
        result: set[str] = set()
        for name, hint in hints.items():
            # Plain Path field, or a union containing Path (Path | None / Optional[Path])
            is_path = hint is Path
            is_union_with_path = (
                isinstance(hint, types.UnionType)  # Python 3.10+: Path | None
                or typing.get_origin(hint) is typing.Union  # Optional[Path]
            ) and Path in typing.get_args(hint)
            if is_path or is_union_with_path:
                result.add(name)
        return frozenset(result)

    @staticmethod
    def _strings_to_paths(data: dict[str, Any]) -> dict[str, Any]:
        """Convert string paths back to Path objects.

        The set of path keys is derived from AnkiMinerConfig field annotations
        (fields whose type is Path or Path | None) so it can never drift as new
        Path fields are added to the dataclass.

        Args:
            data: Dictionary with string paths

        Returns:
            Dictionary with appropriate strings converted to Path objects
        """
        path_keys = GUIConfigManager._path_field_names()

        result: dict[str, Any] = {}
        for key, value in data.items():
            if key in path_keys and isinstance(value, str):
                result[key] = Path(value)
            elif isinstance(value, dict):
                result[key] = GUIConfigManager._strings_to_paths(value)
            else:
                result[key] = value
        return result
