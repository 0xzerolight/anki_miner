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

    # Schema version stamped into every saved gui_config.json. Bump it only
    # when introducing a migration shim that a load MUST run for files written
    # under an older schema; that shim then gates on the loaded marker being
    # below this floor.
    #
    # Floor policy: every migration shim below version 1 was deleted
    # 2026-07-13 (the pre-v2.3.2 allowed_pos backfill, the QSettings→JSON theme
    # carry-over, and the use_offline_dict strip). A config file with no marker
    # is treated as version 0; it still loads cleanly — unknown keys are
    # dropped and dataclass defaults fill any gaps.
    #
    # Version 2 (junk-reduction r3) is the first shim that actually gates on
    # the marker: on the LOAD path a config written under schema < 2 with no
    # enabled name wordsets is seeded to the default-ON set (see
    # _migrate_dict). The three chain rebuilds remain permanent deserializers,
    # not version shims, and are unaffected. Version 3 disables the legacy
    # default-ON yt-dlp updater once; a v3 config may explicitly opt back in.
    CONFIG_SCHEMA_VERSION = 3

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

        # Stamp the schema version so future loads can tell which shims (if any)
        # this file needs. It is a JSON-only marker, not a dataclass field, so
        # the load path drops it before constructing AnkiMinerConfig.
        config_dict["config_schema_version"] = cls.CONFIG_SCHEMA_VERSION

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
        # bad write once nuked a user's settings with no way back). The copy runs
        # inside the try, so if it fails we unlink the .tmp and re-raise without
        # touching CONFIG_FILE — the original survives intact.
        tmp_path = cls.CONFIG_FILE.with_suffix(cls.CONFIG_FILE.suffix + ".tmp")
        bak_path = cls.CONFIG_FILE.with_name(cls.CONFIG_FILE.name + ".bak")
        try:
            tmp_path.touch(mode=0o600, exist_ok=True)
            if os.name == "posix":
                os.chmod(tmp_path, 0o600)
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            # First-ever save has nothing to back up — skip silently.
            if cls.CONFIG_FILE.exists():
                bak_path.touch(mode=0o600, exist_ok=True)
                if os.name == "posix":
                    os.chmod(bak_path, 0o600)
                shutil.copyfile(cls.CONFIG_FILE, bak_path)
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

        # LOAD path runs schema migrations for existing gui_config.json files;
        # the import path calls _migrate_dict without these load-only flags.
        return AnkiMinerConfig(
            **cls._migrate_dict(
                config_dict,
                seed_wordsets=True,
                disable_legacy_ytdlp_update=True,
            )
        )

    @classmethod
    def _migrate_dict(
        cls,
        config_dict: dict[str, Any],
        *,
        backfill_anki_fields: bool = True,
        seed_wordsets: bool = False,
        disable_legacy_ytdlp_update: bool = False,
    ) -> dict[str, Any]:
        """Run the full pre-construction migration pipeline on a raw JSON dict.

        Shared by the normal load path (:meth:`_parse_and_migrate`) and the
        settings-import path, so both get identical version tolerance: string→
        Path conversion, field renames, chain rebuilds, and the unknown-key
        drop that keeps ``AnkiMinerConfig(**...)`` from raising on removed
        fields.

        Args:
            backfill_anki_fields: When False, skip default-filling missing
                ``anki_fields`` sub-keys (the sub-key renames still apply).
                The import-overlay path sets this so a partial ``anki_fields``
                can be merged onto the current mapping — the backfilled dict
                would otherwise clobber unlisted current sub-keys with
                defaults.
            seed_wordsets: When True (LOAD path only), seed the default-ON
                name wordsets on a schema < 2 config that has none enabled.
                The import path leaves it False: an imported settings file
                carries no schema marker, so a deliberate all-off export must
                not be force-re-enabled here.
            disable_legacy_ytdlp_update: When True (LOAD path only), force the
                updater off for configs written under schema < 3. Schema 3+
                preserves an explicit opt-in.
        """
        # Convert string paths back to Path objects
        config_dict = cls._strings_to_paths(config_dict)

        # Migrate old field names
        config_dict = cls._migrate_field_names(config_dict)

        # Backfill any anki_fields keys that are new since the config was saved
        if backfill_anki_fields:
            config_dict = cls._backfill_anki_fields(config_dict)

        # Migrate legacy dictionary fields → dictionary_chain
        config_dict = cls._migrate_dictionary_chain(config_dict)

        # Migrate expression_audio_chain JSON dicts → AudioSourceEntry
        config_dict = cls._migrate_expression_audio_chain(config_dict)

        # Migrate frequency_chain JSON dicts → FreqEntry
        config_dict = cls._migrate_frequency_chain(config_dict)

        # Default-ON seed for name wordsets (junk-reduction r3). A config
        # written under schema < 2 that carries no enabled wordsets predates
        # the default-ON rollout, so seed the full bundled set. This is the
        # first shim to gate on the marker, so it MUST read it before the pop
        # below. LOAD-path only (seed_wordsets): the import path never seeds —
        # an imported settings file has no marker, so a deliberate all-off
        # export would otherwise be force-re-enabled. A non-empty saved list is
        # left untouched; the value tracks the dataclass default automatically.
        if (
            seed_wordsets
            and config_dict.get("config_schema_version", 0) < 2
            and not config_dict.get("excluded_wordsets")
        ):
            config_dict["excluded_wordsets"] = create_default_config().excluded_wordsets

        # P0 containment (048): pre-v3 files serialized the old default-ON
        # updater choice. Force it off once; after a v3 save, a deliberate user
        # opt-in remains true on later loads.
        if disable_legacy_ytdlp_update and config_dict.get("config_schema_version", 0) < 3:
            config_dict["auto_update_ytdlp"] = False

        # Drop the schema-version marker (see CONFIG_SCHEMA_VERSION): a JSON-
        # only key, never a dataclass field. A missing marker means the file
        # predates schema versioning (version 0). Popped here so it neither
        # reaches AnkiMinerConfig nor logs as a dropped unknown key below.
        config_dict.pop("config_schema_version", None)

        # Drop keys not in the current dataclass (e.g., removed fields from old
        # versions). Without this filter, AnkiMinerConfig(**config_dict) raises
        # TypeError and the except below would silently reset the entire user
        # config to defaults.
        valid_keys = {f.name for f in fields(AnkiMinerConfig)}
        dropped = set(config_dict) - valid_keys
        if dropped:
            logger.debug("Dropping unknown config keys: %s", sorted(dropped))
        return {k: v for k, v in config_dict.items() if k in valid_keys}

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
            return create_default_config()

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

    # Envelope marker key for exported settings files (see export_config).
    _EXPORT_MARKER = "anki_miner_settings"

    @classmethod
    def machine_specific_fields(cls) -> frozenset[str]:
        """Config fields that must not travel between machines.

        Everything path-typed (auto-derived, so new Path fields can't leak),
        plus non-path state that is meaningless or harmful elsewhere:
        first-run flags, update-checker state, the three resource-ID chains
        (their ``dict_id``/``pack_id``/``source_id`` entries reference
        resources installed under THIS machine's roots — imported elsewhere
        they render as silent "(missing)" chain rows), the local browser for
        cookie extraction, and the host GPU backend. Deliberately portable:
        ``theme`` (built-ins always resolve) and ``max_parallel_workers``.
        """
        return cls._path_field_names() | {
            "first_run_shortcut_done",
            "first_run_setup_done",
            "last_known_version",
            "skipped_update_version",
            "dictionary_chain",
            "expression_audio_chain",
            "frequency_chain",
            "youtube_cookies_from_browser",
            "asr_device",
        }

    @classmethod
    def export_config(cls, config: AnkiMinerConfig, path: Path) -> None:
        """Write a portable settings export to ``path``.

        The payload is an envelope ``{"anki_miner_settings": 1, "app_version":
        ..., "settings": {...}}`` whose ``settings`` dict is the normal
        gui_config.json serialization minus :meth:`machine_specific_fields`.

        Raises:
            OSError: If the file cannot be written.
        """
        from anki_miner import __version__

        settings = cls._paths_to_strings(cls._config_to_serializable_dict(config))
        excluded = cls.machine_specific_fields()
        settings = {k: v for k, v in settings.items() if k not in excluded}
        payload = {cls._EXPORT_MARKER: 1, "app_version": __version__, "settings": settings}

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def import_config(cls, path: Path, current_config: AnkiMinerConfig) -> AnkiMinerConfig:
        """Overlay a settings file onto ``current_config`` and return the result.

        Accepts both the export envelope and a flat dict (a raw
        gui_config.json is importable). Version tolerance comes from
        :meth:`_migrate_dict` (renames applied, unknown keys dropped);
        machine-specific fields are stripped from the incoming data as well,
        so a full dump from another machine can't plant broken paths or
        dangling resource chains. Keys absent from the file keep their
        current values — including at the ``anki_fields`` /
        ``card_type_marker_fields`` sub-key level, where present dicts are
        merged onto the current mapping and non-dict values are discarded
        (matching ``load_config``'s tolerance for corrupt values).

        Raises:
            json.JSONDecodeError: Invalid JSON.
            ValueError: Valid JSON that is not a settings dict.
            TypeError: Values the config constructor rejects.
            OSError: If the file cannot be read.
        """
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        data = raw
        if isinstance(raw, dict) and cls._EXPORT_MARKER in raw:
            data = raw.get("settings")
        if not isinstance(data, dict):
            raise ValueError("Not a settings file: expected a JSON object of config fields")

        incoming = cls._migrate_dict(dict(data), backfill_anki_fields=False)
        excluded = cls.machine_specific_fields()
        incoming = {k: v for k, v in incoming.items() if k not in excluded}

        # Sub-key overlay for the two mapping fields: a present dict merges
        # onto the current mapping (file wins per sub-key, unlisted sub-keys
        # keep current); a non-dict value is dropped so current is kept.
        for key in ("anki_fields", "card_type_marker_fields"):
            value = incoming.get(key)
            if isinstance(value, dict):
                incoming[key] = {**dict(getattr(current_config, key)), **value}
            elif key in incoming:
                del incoming[key]

        return dataclasses.replace(current_config, **incoming)

    @staticmethod
    def _migrate_dictionary_chain(data: dict[str, Any]) -> dict[str, Any]:
        """Rebuild ChainEntry instances when an existing dictionary_chain is
        loaded as list[dict] from JSON. Missing chains fall through to the
        dataclass defaults (jmdict-english + jisho).
        """
        from anki_miner.config import ChainEntry

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
