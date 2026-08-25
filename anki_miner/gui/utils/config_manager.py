"""GUI configuration persistence manager."""

import dataclasses
import json
import logging
import os
import shutil
import types
import typing
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Any

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.services.startup_store_recovery import backup_config_repair_is_safe
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.bounded_reader import read_json_bounded

logger = logging.getLogger(__name__)

_CONFIG_MAX_BYTES = 2 * 1024 * 1024
_INVALID_CONFIG = object()


class _ConfigReadError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class ImportConfigResult:
    """Imported config plus non-fatal validation and migration feedback."""

    config: AnkiMinerConfig
    invalid_fields: list[str] = dataclasses.field(default_factory=list)
    notices: list[str] = dataclasses.field(default_factory=list)


class GUIConfigManager:
    """Manager for GUI configuration persistence.

    This class handles saving and loading user configuration to/from a JSON file
    stored in the user's home directory. It handles Path object serialization and
    provides fallback to default configuration if the file doesn't exist or is invalid.
    """

    CONFIG_FILE = ANKI_MINER_HOME / "gui_config.json"

    # Which named profile the live gui_config.json currently belongs to. A
    # process-lifetime, IN-MEMORY value: seeded once by the boot step (from
    # read_active_profile_id) and mutated only by the profile controller.
    #
    # save_config must NEVER read this from disk. It runs on every debounced
    # settings edit, from MainWindow.closeEvent's finally, and from
    # BackgroundTaskController._poll_deferred_close minutes after the window is
    # hidden; a disk read there (let alone a "missing -> create" write) would
    # add a new failure mode to all three.
    ACTIVE_PROFILE_ID: str | None = None

    # A recovered .bak must not become authoritative while an adjacent store
    # tombstone has no durable deletion-intent marker. Tied to CONFIG_FILE so
    # test/home retargeting cannot carry the hold onto another config.
    _DEFERRED_BACKUP_REPAIR_FOR: Path | None = None

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
    #
    # NOTE on auto_update_ytdlp: its dataclass default is now True again. That looks
    # like it reverses the version-3 shim but does not, and it needs NO new shim.
    # The field predates CONFIG_SCHEMA_VERSION 3 and _config_to_serializable_dict
    # writes every dataclass field, so any file stamped >= 3 already carries an
    # explicit value that a load preserves; files stamped < 3 still hit the clamp in
    # _migrate_dict. The new default therefore reaches only installs with no config
    # file at all. Do NOT add a "schema >= 3 and key missing -> False" rule to
    # compensate: _migrate_dict is shared with import_settings, whose contract is
    # that absent keys keep current values, so such a rule would silently disable
    # the updater on every settings import that omits the key.
    #
    # Version 4 flips use_native_file_dialogs on once. The dataclass default is
    # now True, but that alone reaches only installs with no config file:
    # _config_to_serializable_dict writes every field, so every existing
    # gui_config.json already carries an explicit False that a load preserves.
    # Both halves are needed, exactly like version 3 — the load shim below AND
    # the present-key-gated shim in import_config, without which importing any
    # pre-flip export silently reverts the user (the field is portable; it is
    # not in machine_specific_fields).
    CONFIG_SCHEMA_VERSION = 4

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
        if cls._DEFERRED_BACKUP_REPAIR_FOR == cls.CONFIG_FILE:
            if not backup_config_repair_is_safe(config):
                logger.warning(
                    "Configuration save deferred until retained deletion intent is durable: %s",
                    cls.CONFIG_FILE,
                )
                return
            cls._DEFERRED_BACKUP_REPAIR_FOR = None

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

        # Stamp which profile this live config belongs to. Also a JSON-only
        # marker; absent (not null) when no profile is active.
        if cls.ACTIVE_PROFILE_ID is not None:
            config_dict["active_profile_id"] = cls.ACTIVE_PROFILE_ID

        # Atomic write: stage to a unique sibling temp file then os.replace. A
        # truncating in-place write (open("w")) leaves invalid JSON if we
        # crash or lose power mid-serialize, which load_config then swallows
        # into factory defaults — wiping every user setting. Staging keeps the
        # previous good file intact until the new one is fully written;
        # os.replace is atomic on the same filesystem. The temp name must be
        # unique (not a shared fixed ".tmp") because the single-instance guard
        # is only advisory ("Continue anyway") — two racing instances writing
        # the same fixed name could interleave and byte-splice a corrupt
        # primary. atomic_write_path's finally unlinks the temp file if this
        # block raises, so a partial temp doesn't accumulate.
        #
        # Backup rotation: right before the context exit's os.replace clobbers
        # the existing file, copy the still-good current config to a sibling
        # .bak (one-overwrite recovery — config isn't in git and os.replace
        # keeps no backup, so a bad write once nuked a user's settings with no
        # way back). The copy runs inside the context, so if it fails the
        # exception propagates, the temp file is unlinked, and CONFIG_FILE is
        # never touched — the original survives intact.
        bak_path = cls.CONFIG_FILE.with_name(cls.CONFIG_FILE.name + ".bak")
        with atomic_write_path(cls.CONFIG_FILE) as tmp_path:
            if os.name == "posix":
                # atomic_write_path does not chmod. Do it on the temp file so
                # the config is never group/world-readable, not even
                # momentarily.
                os.chmod(tmp_path, 0o600)
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            # First-ever save has nothing to back up — skip silently.
            if cls.CONFIG_FILE.exists():
                # Rotation guard: never copy a corrupt primary over a possibly
                # valid .bak. Only DECODE failures skip rotation — a read
                # OSError propagates so the outer handler aborts the save with
                # the primary untouched (an unreadable file is not "corrupt").
                primary_bytes = cls.CONFIG_FILE.read_bytes()
                try:
                    rotatable = isinstance(json.loads(primary_bytes), dict)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    rotatable = False
                if not rotatable:
                    logger.warning(
                        "Backup rotation skipped for unparseable primary %s",
                        cls.CONFIG_FILE,
                    )
                else:
                    bak_path.touch(mode=0o600, exist_ok=True)
                    if os.name == "posix":
                        os.chmod(bak_path, 0o600)
                    shutil.copyfile(cls.CONFIG_FILE, bak_path)

    @classmethod
    def read_active_profile_id(cls) -> str | None:
        """Return the ``active_profile_id`` marker stored in gui_config.json.

        Best-effort raw read for the boot step that seeds
        :attr:`ACTIVE_PROFILE_ID`; it deliberately does not build a config.
        Never raises — every failure mode (missing file, OSError, undecodable
        JSON, non-object root, absent key, or a value that is not a non-empty
        string) yields ``None``, i.e. "no active profile".
        """
        if not cls.CONFIG_FILE.exists():
            # Checked here rather than left to the bounded reader, which logs a
            # WARNING for a file it cannot open: a fresh install has no config
            # yet, and "no active profile" is the normal answer, not a fault.
            # load_config_with_provenance guards the same way.
            return None
        raw = read_json_bounded(cls.CONFIG_FILE, _CONFIG_MAX_BYTES, _INVALID_CONFIG, "config")
        if raw is _INVALID_CONFIG or not isinstance(raw, dict):
            return None
        value = raw.get("active_profile_id")
        if isinstance(value, str) and value:
            return value
        return None

    @classmethod
    def _parse_and_migrate(cls, path: Path, *, archive_future: bool = True) -> AnkiMinerConfig:
        """Parse a config JSON file and run all migration steps.

        Args:
            archive_future: When True (the default, used for gui_config.json
                and its .bak), archive a file whose schema version exceeds
                CONFIG_SCHEMA_VERSION before the best-effort load. Callers
                reading a sidecar file must pass False:
                _archive_future_schema_config derives the archive name from
                cls.CONFIG_FILE, so a future-schema sidecar would be archived
                under a misleading gui_config.from-schema-N.json name and
                repeated reads would pile up .2/.3 collision variants of it.

        Raises:
            json.JSONDecodeError: If the file contains invalid JSON.
            TypeError, ValueError: If the parsed data cannot be coerced into
                AnkiMinerConfig (e.g. wrong types, unexpected structure).
            OSError: If the file cannot be read.
        """
        config_dict = read_json_bounded(path, _CONFIG_MAX_BYTES, _INVALID_CONFIG, "config")
        if config_dict is _INVALID_CONFIG:
            raise _ConfigReadError(f"Could not decode {path.name}")
        if not isinstance(config_dict, dict):
            logger.warning("Invalid config %s: expected a JSON object", path)
            raise _ConfigReadError(f"Invalid root in {path.name}")

        schema_version = config_dict.get("config_schema_version")
        if (
            archive_future
            and isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version > cls.CONFIG_SCHEMA_VERSION
        ):
            cls._archive_future_schema_config(path, schema_version)

        # LOAD path runs schema migrations for existing gui_config.json files.
        # Import handles its provenance-aware shims after this shared migration
        # so absent overlay keys cannot be synthesized.
        migrated = cls._migrate_dict(
            config_dict,
            seed_wordsets=True,
            disable_legacy_ytdlp_update=True,
            enable_native_file_dialogs=True,
            seed_first_run_flags=True,
        )
        return AnkiMinerConfig(**cls._decode_field_types(migrated))

    @classmethod
    def _archive_future_schema_config(cls, path: Path, schema_version: int) -> None:
        """Best-effort byte archive of a loaded config from a future schema."""
        try:
            source_bytes = path.read_bytes()
            archive_root = cls.CONFIG_FILE
            base = archive_root.with_name(f"{archive_root.stem}.from-schema-{schema_version}{archive_root.suffix}")
            candidate = base
            collision_index = 2

            while True:
                try:
                    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    try:
                        identical = candidate.read_bytes() == source_bytes
                    except OSError:
                        identical = False
                    if identical:
                        archive = candidate
                        break
                    candidate = archive_root.with_name(
                        f"{archive_root.stem}.from-schema-{schema_version}.{collision_index}{archive_root.suffix}"
                    )
                    collision_index += 1
                    continue

                try:
                    with os.fdopen(fd, "wb") as archive_file:
                        archive_file.write(source_bytes)
                except OSError:
                    candidate.unlink(missing_ok=True)
                    raise
                archive = candidate
                break
        except OSError as e:
            logger.warning(
                "Could not archive future-schema config %s (schema %s): %s",
                path,
                schema_version,
                e,
            )
            return

        logger.warning(
            "Future-schema config archived to %s before best-effort load",
            archive,
        )

    @classmethod
    def _migrate_dict(
        cls,
        config_dict: dict[str, Any],
        *,
        backfill_anki_fields: bool = True,
        seed_wordsets: bool = False,
        disable_legacy_ytdlp_update: bool = False,
        enable_native_file_dialogs: bool = False,
        seed_first_run_flags: bool = False,
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
            seed_wordsets: When True, seed the default-ON name wordsets on a
                schema < 2 config that has none enabled. Used for loads.
            disable_legacy_ytdlp_update: When True, force the updater off for
                configs written under schema < 3. Used for loads; schema 3+
                preserves an explicit opt-in.
            enable_native_file_dialogs: When True, force native file pickers on
                for configs written under schema < 4. Used for loads; schema 4+
                preserves an explicit opt-out.
            seed_first_run_flags: When True (LOAD path only), mark first-run
                flows done when their keys are absent from an existing config.
                Explicit stored values are preserved.
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

        # Migrate pitch_chain JSON dicts → PitchSourceEntry
        config_dict = cls._migrate_pitch_chain(config_dict)

        # Default-ON seed for name wordsets (junk-reduction r3). A config
        # written under schema < 2 that carries no enabled wordsets predates
        # the default-ON rollout, so seed the full bundled set. This is the
        # first shim to gate on the marker, so it MUST read it before the pop
        # below. Callers enable seed_wordsets only for a load or an import with
        # schema provenance. A non-empty saved list is left untouched; the
        # value tracks the dataclass default automatically.
        schema_version = config_dict.get("config_schema_version", 0)
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            schema_version = 0
        if seed_wordsets and schema_version < 2 and not config_dict.get("excluded_wordsets"):
            config_dict["excluded_wordsets"] = create_default_config().excluded_wordsets

        # P0 containment (048): pre-v3 files serialized the old default-ON
        # updater choice. Force it off once; after a v3 save, a deliberate user
        # opt-in remains true on later loads.
        if disable_legacy_ytdlp_update and schema_version < 3:
            config_dict["auto_update_ytdlp"] = False

        # Pre-v4 files serialized the old Qt-only picker choice, which existed
        # only because the blocking native call could freeze the GUI thread
        # (Issue #100). The pickers are non-blocking now, so turn native back on
        # once; after a v4 save, a deliberate user opt-out remains False.
        if enable_native_file_dialogs and schema_version < 4:
            config_dict["use_native_file_dialogs"] = True

        # Existing installs predate both first-run flows. Offer them only on a
        # genuinely fresh install; preserve explicit False so an interrupted or
        # deliberately re-enabled flow remains pending.
        if seed_first_run_flags:
            config_dict.setdefault("first_run_setup_done", True)
            config_dict.setdefault("first_run_shortcut_done", True)

        # Drop the three JSON-only marker keys — none is a dataclass field:
        #   config_schema_version (see CONFIG_SCHEMA_VERSION; a missing marker
        #     means the file predates schema versioning, i.e. version 0),
        #   active_profile_id (gui_config.json only — which profile the live
        #     config belongs to),
        #   profile_name (profile sidecar files only — that profile's display
        #     name).
        # Popped here so they neither reach AnkiMinerConfig nor log as dropped
        # unknown keys below on every single load.
        config_dict.pop("config_schema_version", None)
        config_dict.pop("active_profile_id", None)
        config_dict.pop("profile_name", None)

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
        return cls.load_config_with_provenance()[0]

    @classmethod
    def load_config_with_provenance(cls) -> tuple[AnkiMinerConfig, bool]:
        """Load config plus whether its chains authorize artifact collection."""
        bak_path = cls.CONFIG_FILE.with_name(cls.CONFIG_FILE.name + ".bak")
        if cls.CONFIG_FILE.exists():
            try:
                config = cls._parse_and_migrate(cls.CONFIG_FILE)
                if cls._DEFERRED_BACKUP_REPAIR_FOR == cls.CONFIG_FILE:
                    cls._DEFERRED_BACKUP_REPAIR_FOR = None
                return config, True
            except (_ConfigReadError, TypeError, ValueError) as e:
                logger.warning("gui_config.json invalid (%s); attempting .bak recovery", e)
            except OSError as e:
                # An unreadable file (permissions, transient I/O error) must not
                # crash startup — try .bak before falling back to defaults.
                logger.warning("gui_config.json unreadable (%s); attempting .bak recovery", e)
        elif not bak_path.exists():
            if cls._DEFERRED_BACKUP_REPAIR_FOR == cls.CONFIG_FILE:
                cls._DEFERRED_BACKUP_REPAIR_FOR = None
            return create_default_config(), False
        else:
            logger.warning("gui_config.json missing; attempting .bak recovery")

        # One .bak attempt — no loop.
        try:
            config = cls._parse_and_migrate(bak_path)
            if backup_config_repair_is_safe(config):
                if cls._DEFERRED_BACKUP_REPAIR_FOR == cls.CONFIG_FILE:
                    cls._DEFERRED_BACKUP_REPAIR_FOR = None
                cls._repair_primary_from_backup(bak_path)
            else:
                cls._DEFERRED_BACKUP_REPAIR_FOR = cls.CONFIG_FILE
            logger.warning("gui_config.json recovered from .bak")
            return config, False
        except (_ConfigReadError, TypeError, ValueError, OSError) as bak_err:
            if cls._DEFERRED_BACKUP_REPAIR_FOR == cls.CONFIG_FILE:
                cls._DEFERRED_BACKUP_REPAIR_FOR = None
            logger.warning("gui_config.json.bak also unusable (%s); using defaults", bak_err)
            return create_default_config(), False

    @classmethod
    def _repair_primary_from_backup(cls, bak_path: Path) -> None:
        """Best-effort write-through repair after a successful backup parse."""
        corrupt_path = cls.CONFIG_FILE.with_name(cls.CONFIG_FILE.name + ".corrupt")
        if cls.CONFIG_FILE.exists():
            try:
                shutil.copyfile(cls.CONFIG_FILE, corrupt_path)
            except OSError as e:
                logger.warning("Could not preserve corrupt gui_config.json at %s: %s", corrupt_path, e)

        try:
            with atomic_write_path(cls.CONFIG_FILE) as tmp_path:
                if os.name == "posix":
                    # atomic_write_path does not chmod. Do it on the temp file
                    # so the config is never group/world-readable, not even
                    # momentarily.
                    os.chmod(tmp_path, 0o600)
                shutil.copyfile(bak_path, tmp_path)
        except OSError as e:
            logger.warning("Could not repair gui_config.json from %s: %s", bak_path, e)

    # Envelope marker key for exported settings files (see export_config).
    _EXPORT_MARKER = "anki_miner_settings"
    _OLDER_YTDLP_IMPORT_NOTICE = "Auto-update of yt-dlp was disabled (settings imported from an older version)."
    _LEGACY_283_IMPORT_NOTICE = "Settings from version 2.8.3 were mapped conservatively to schema 2."

    @classmethod
    def machine_specific_fields(cls) -> frozenset[str]:
        """Config fields that must not travel between machines.

        Everything path-typed (auto-derived, so new Path fields can't leak),
        plus non-path state that is meaningless or harmful elsewhere:
        first-run flags, update-checker state, the four resource-ID chains
        (their ``dict_id``/``pack_id``/``source_id`` entries reference
        resources installed under THIS machine's roots — imported elsewhere
        they render as silent "(missing)" chain rows), the local browser for
        cookie extraction, and the host GPU backend. ``config_version`` is an
        internal monotonic identity and must not travel. Deliberately portable:
        ``theme`` (built-ins always resolve) and ``max_parallel_workers``.
        """
        return cls._path_field_names() | {
            "first_run_shortcut_done",
            "first_run_setup_done",
            # Records that THIS machine's pitch_accent.csv was folded into the
            # chain. Travelling, it would suppress the receiving machine's own
            # one-time migration and lose its legacy pitch data.
            "legacy_pitch_migrated",
            "last_known_version",
            "skipped_update_version",
            "dictionary_chain",
            "expression_audio_chain",
            "frequency_chain",
            "pitch_chain",
            "youtube_cookies_from_browser",
            "asr_device",
            "config_version",
        }

    @classmethod
    def export_config(cls, config: AnkiMinerConfig, path: Path) -> None:
        """Write a portable settings export to ``path``.

        The payload is an envelope ``{"anki_miner_settings": 1, "app_version":
        ..., "config_schema_version": ..., "settings": {...}}`` whose
        ``settings`` dict is the normal gui_config.json serialization minus
        :meth:`machine_specific_fields`.

        Raises:
            OSError: If the file cannot be written.
        """
        from anki_miner import __version__

        settings = cls._paths_to_strings(cls._config_to_serializable_dict(config))
        excluded = cls.machine_specific_fields()
        settings = {k: v for k, v in settings.items() if k not in excluded}
        payload = {
            cls._EXPORT_MARKER: 1,
            "app_version": __version__,
            "config_schema_version": cls.CONFIG_SCHEMA_VERSION,
            "settings": settings,
        }

        with atomic_write_path(path) as tmp_path, tmp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    @classmethod
    def import_config(cls, path: Path, current_config: AnkiMinerConfig) -> ImportConfigResult:
        """Overlay a settings file onto ``current_config`` and return feedback.

        Accepts both the export envelope and a flat dict (a raw
        gui_config.json is importable). Version tolerance comes from
        :meth:`_migrate_dict` (renames applied, unknown keys dropped);
        machine-specific fields are stripped from the incoming data as well,
        so a full dump from another machine can't plant broken paths or
        dangling resource chains. Keys absent from the file keep their
        current values — including at the ``anki_fields`` /
        ``card_type_marker_fields`` sub-key level, where present dicts are
        merged onto the current mapping. Invalid typed values are reported and
        dropped so the corresponding current values survive the overlay.

        Raises:
            json.JSONDecodeError: Invalid JSON.
            ValueError: Valid JSON that is not a settings dict.
            TypeError: Values the config constructor rejects.
            OSError: If the file cannot be read.
        """
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)

        data = raw
        source_schema: int | None = None
        conservative_283_mapping = False
        if isinstance(raw, dict) and cls._EXPORT_MARKER in raw:
            data = raw.get("settings")
            if "config_schema_version" in raw:
                marker = raw.get("config_schema_version")
                if isinstance(marker, int) and not isinstance(marker, bool):
                    source_schema = marker
            else:
                app_version = raw.get("app_version")
                if app_version in {"2.8.1", "2.8.2"}:
                    source_schema = 1
                elif app_version == "2.8.3":
                    source_schema = 2
                    conservative_283_mapping = True
        elif isinstance(raw, dict):
            marker = raw.get("config_schema_version")
            if isinstance(marker, int) and not isinstance(marker, bool):
                source_schema = marker
        if not isinstance(data, dict):
            raise ValueError("Not a settings file: expected a JSON object of config fields")

        migration_input = dict(data)
        if source_schema is not None:
            migration_input["config_schema_version"] = source_schema
        incoming = cls._migrate_dict(
            migration_input,
            backfill_anki_fields=False,
        )
        legacy_ytdlp_forced = False
        if source_schema is not None:
            # Exact-type gates: the shims must only rewrite well-formed legacy
            # values. Anything else (null, strings, ...) falls through to
            # _decode_value below and is rejected into invalid_fields instead
            # of being silently rewritten.
            if source_schema < 2 and data.get("excluded_wordsets") == []:
                incoming["excluded_wordsets"] = create_default_config().excluded_wordsets
            if source_schema < 3 and isinstance(data.get("auto_update_ytdlp"), bool):
                incoming["auto_update_ytdlp"] = False
                legacy_ytdlp_forced = True
            # The field is portable (not machine-specific), so without this a
            # pre-v4 export would write its Qt-only picker choice straight back
            # and silently undo the schema-4 load migration. No user notice:
            # unlike the yt-dlp updater this is a visible, one-click-reversible
            # UI preference, not a change in network behaviour.
            if source_schema < 4 and isinstance(data.get("use_native_file_dialogs"), bool):
                incoming["use_native_file_dialogs"] = True
        excluded = cls.machine_specific_fields()
        incoming = {k: v for k, v in incoming.items() if k not in excluded}

        invalid_fields: list[str] = []
        validated: dict[str, Any] = {}
        hints = typing.get_type_hints(AnkiMinerConfig)
        for key, value in incoming.items():
            valid, converted = cls._decode_value(value, hints[key])
            if valid:
                validated[key] = converted
            else:
                invalid_fields.append(key)
        incoming = validated

        # Sub-key overlay for the two mapping fields: a present dict merges
        # onto the current mapping (file wins per sub-key, unlisted sub-keys
        # keep current); a non-dict value is dropped so current is kept.
        for key in ("anki_fields", "card_type_marker_fields"):
            value = incoming.get(key)
            if isinstance(value, dict):
                incoming[key] = {**dict(getattr(current_config, key)), **value}
            elif key in incoming:
                del incoming[key]

        notices: list[str] = []
        if legacy_ytdlp_forced:
            notices.append(cls._OLDER_YTDLP_IMPORT_NOTICE)
        if conservative_283_mapping:
            notices.append(cls._LEGACY_283_IMPORT_NOTICE)

        return ImportConfigResult(
            config=dataclasses.replace(current_config, **incoming),
            invalid_fields=invalid_fields,
            notices=notices,
        )

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
        if not isinstance(raw_chain, (list, tuple)):
            data.pop("dictionary_chain")
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
                            enabled=item.get("enabled", True),
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
        if not isinstance(raw_chain, (list, tuple)):
            data.pop("expression_audio_chain")
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
                            enabled=item.get("enabled", True),
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
        if not isinstance(raw_chain, (list, tuple)):
            data.pop("frequency_chain")
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
                            enabled=item.get("enabled", True),
                        )
                    )
        data["frequency_chain"] = tuple(chain)
        return data

    @staticmethod
    def _migrate_pitch_chain(data: dict[str, Any]) -> dict[str, Any]:
        """Rebuild PitchSourceEntry instances when an existing pitch_chain is
        loaded as list[dict] from JSON. A missing chain falls through to the
        dataclass default (empty tuple — no pitch sources).

        Malformed entries (non-dict items, missing/empty source_id) are dropped;
        items already constructed as PitchSourceEntry pass through unchanged.
        """
        from anki_miner.config import PitchSourceEntry

        raw_chain = data.get("pitch_chain")
        if raw_chain is None:
            return data
        if not isinstance(raw_chain, (list, tuple)):
            data.pop("pitch_chain")
            return data

        chain: list[PitchSourceEntry] = []
        for item in raw_chain:
            if isinstance(item, PitchSourceEntry):
                chain.append(item)
            elif isinstance(item, dict):
                source_id = item.get("source_id")
                if isinstance(source_id, str) and source_id:
                    chain.append(
                        PitchSourceEntry(
                            source_id=source_id,
                            enabled=item.get("enabled", True),
                        )
                    )
        data["pitch_chain"] = tuple(chain)
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
    def _decode_field_types(data: dict[str, Any]) -> dict[str, Any]:
        """Drop wrong-typed decoded fields so dataclass defaults stay authoritative."""
        defaults = create_default_config()
        hints = typing.get_type_hints(AnkiMinerConfig)
        decoded: dict[str, Any] = {}
        for key, value in data.items():
            valid, converted = GUIConfigManager._decode_value(value, hints[key])
            if valid:
                decoded[key] = converted
            else:
                logger.warning("Invalid type for config field '%s'; using default", key)
                decoded[key] = getattr(defaults, key)
        return decoded

    @staticmethod
    def _decode_value(value: Any, annotation: Any) -> tuple[bool, Any]:
        origin = typing.get_origin(annotation)
        args = typing.get_args(annotation)
        if annotation is Any:
            return True, value
        if origin is typing.Literal:
            return any(type(value) is type(choice) and value == choice for choice in args), value
        if origin in (typing.Union, types.UnionType):
            for option in args:
                valid, converted = GUIConfigManager._decode_value(value, option)
                if valid:
                    return True, converted
            return False, value
        if annotation is bool:
            return type(value) is bool, value
        if annotation is int:
            return type(value) is int, value
        if annotation is float:
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
            return valid, float(value) if valid else value
        if annotation is str:
            return type(value) is str, value
        if annotation is type(None):
            return value is None, value
        if origin is tuple:
            if not isinstance(value, (list, tuple)):
                return False, value
            item_type = args[0] if args else Any
            converted_items = []
            for item in value:
                valid, converted = GUIConfigManager._decode_value(item, item_type)
                if not valid:
                    return False, value
                converted_items.append(converted)
            return True, tuple(converted_items)
        if origin in (dict, Mapping):
            if not isinstance(value, dict):
                return False, value
            key_type, value_type = args or (Any, Any)
            for item_key, item_value in value.items():
                if not GUIConfigManager._decode_value(item_key, key_type)[0]:
                    return False, value
                if not GUIConfigManager._decode_value(item_value, value_type)[0]:
                    return False, value
            return True, value
        if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
            if not isinstance(value, annotation):
                return False, value
            field_hints = typing.get_type_hints(annotation)
            for field in dataclasses.fields(annotation):
                if not GUIConfigManager._decode_value(getattr(value, field.name), field_hints[field.name])[0]:
                    return False, value
            return True, value
        if isinstance(annotation, type):
            return isinstance(value, annotation), value
        return False, value

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
