"""Tests for GUIConfigManager atomic save + resilient load (T-31).

The config file is the single source of truth for every GUI setting. A crash
mid-write must not wipe it (atomic temp + os.replace), and an unreadable file
must fall back to defaults rather than crash startup (OSError in the load
except tuple).
"""

from __future__ import annotations

import builtins
import json
import os
import stat
import types
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_path)
    return cfg_path


class TestLoadResilience:
    def test_malformed_config_root_recovers_to_defaults(self, tmp_config: Path):
        tmp_config.write_text('["not", "a", "mapping"]', encoding="utf-8")

        loaded = GUIConfigManager.load_config()

        assert loaded == create_default_config()

    def test_oversized_config_recovers_to_defaults(self, tmp_config: Path):
        tmp_config.write_text('{"theme":"dark","padding":"' + ("x" * 3_000_000) + '"}', encoding="utf-8")

        loaded = GUIConfigManager.load_config()

        assert loaded == create_default_config()

    def test_wrong_typed_fields_use_defaults_without_discarding_valid_fields(self, tmp_config: Path):
        tmp_config.write_text(
            '{"anki_deck_name":"Kept","check_for_updates":"false","max_parallel_workers":"many"}',
            encoding="utf-8",
        )

        loaded = GUIConfigManager.load_config()
        defaults = create_default_config()

        assert loaded.anki_deck_name == "Kept"
        assert loaded.check_for_updates is defaults.check_for_updates
        assert loaded.max_parallel_workers == defaults.max_parallel_workers

    def test_wrong_typed_nested_config_uses_field_default(self, tmp_config: Path):
        tmp_config.write_text(
            '{"anki_deck_name":"Kept","dictionary_chain":[{"kind":"indexed","dict_id":"local","enabled":"yes"}]}',
            encoding="utf-8",
        )

        loaded = GUIConfigManager.load_config()

        assert loaded.anki_deck_name == "Kept"
        assert loaded.dictionary_chain == create_default_config().dictionary_chain

    def test_corrupt_primary_recovers_from_bak(self, tmp_config: Path, caplog):
        """A corrupt primary must be recovered from .bak rather than defaulting.

        Sequence: save "dark" (no .bak yet) → save "light" (dark rotates to .bak)
        → corrupt primary → load_config must return "dark" from .bak.
        """
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")

        # First save: no .bak produced.
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        # Second save: primary becomes "light", .bak holds "dark".
        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))
        assert bak_path.exists()

        # Corrupt the primary file.
        tmp_config.write_text('{"theme": "light", CORRUPT', encoding="utf-8")

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == "dark"  # recovered from .bak, not defaults
        assert any(".bak" in r.message for r in caplog.records)

    def test_corrupt_primary_no_bak_falls_back_to_defaults(self, tmp_config: Path, caplog):
        """Primary corrupt, .bak absent → return defaults, no raise."""
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")

        # Only one save → no .bak is written.
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        assert not bak_path.exists()

        tmp_config.write_text('{"theme": CORRUPT', encoding="utf-8")

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == create_default_config().theme
        assert any("default" in r.message.lower() for r in caplog.records)

    def test_corrupt_primary_corrupt_bak_falls_back_to_defaults(self, tmp_config: Path, caplog):
        """Both primary and .bak corrupt → return defaults, no raise."""
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")

        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))
        assert bak_path.exists()

        tmp_config.write_text("{CORRUPT PRIMARY", encoding="utf-8")
        bak_path.write_text("{CORRUPT BAK", encoding="utf-8")

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == create_default_config().theme
        assert any("default" in r.message.lower() for r in caplog.records)

    def test_unreadable_file_oserror_recovers_from_bak(self, tmp_config: Path, monkeypatch, caplog):
        """An OSError while reading the primary must try .bak before defaulting."""
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")

        # Two saves so that .bak holds "dark" while primary holds "light".
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))
        assert bak_path.exists()

        real_open = builtins.open

        def boom(self_path, *args, **kwargs):
            # Only the primary config file read raises; .bak and everything else
            # passes through.
            mode = args[0] if args else kwargs.get("mode", "r")
            if Path(self_path) == tmp_config and "r" in mode:
                raise OSError("Permission denied")
            return real_open(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", lambda self, *a, **k: boom(self, *a, **k))

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == "dark"  # recovered from .bak
        assert any(".bak" in r.message for r in caplog.records)

    def test_unreadable_primary_no_bak_falls_back_to_defaults(self, tmp_config: Path, monkeypatch, caplog):
        """An OSError while reading (e.g. chmod 000), no .bak → fall back to defaults."""
        tmp_config.write_text('{"theme": "dark"}', encoding="utf-8")

        real_open = builtins.open

        def boom(self_path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if Path(self_path) == tmp_config and "r" in mode:
                raise OSError("Permission denied")
            return real_open(self_path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", lambda self, *a, **k: boom(self, *a, **k))

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == create_default_config().theme
        assert any("config" in r.message.lower() for r in caplog.records)


class TestBackupRecoveryRepair:
    def test_missing_primary_recovers_from_bak_and_repairs_primary(self, tmp_config: Path):
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        corrupt_path = tmp_config.with_name(tmp_config.name + ".corrupt")
        bak_bytes = json.dumps(
            {
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                "theme": "dark",
            }
        ).encode()
        bak_path.write_bytes(bak_bytes)

        loaded = GUIConfigManager.load_config()

        assert loaded.theme == "dark"
        assert tmp_config.read_bytes() == bak_bytes
        assert bak_path.read_bytes() == bak_bytes
        assert not corrupt_path.exists()

    def test_recovery_repairs_primary_and_preserves_bak_and_corrupt_bytes(self, tmp_config: Path):
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        corrupt_path = tmp_config.with_name(tmp_config.name + ".corrupt")
        corrupt_bytes = b'{"theme":"light",BROKEN'
        bak_bytes = json.dumps(
            {
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                "theme": "dark",
            },
            indent=3,
        ).encode()
        tmp_config.write_bytes(corrupt_bytes)
        bak_path.write_bytes(bak_bytes)

        loaded = GUIConfigManager.load_config()

        assert loaded.theme == "dark"
        assert tmp_config.read_bytes() == bak_bytes
        assert bak_path.read_bytes() == bak_bytes
        assert corrupt_path.read_bytes() == corrupt_bytes

    def test_save_after_recovery_rotates_valid_primary_to_bak(self, tmp_config: Path):
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        tmp_config.write_bytes(b"{BROKEN PRIMARY")
        bak_path.write_text(
            json.dumps(
                {
                    "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                    "theme": "dark",
                }
            ),
            encoding="utf-8",
        )

        GUIConfigManager.load_config()
        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        rotated = json.loads(bak_path.read_text(encoding="utf-8"))
        assert rotated["theme"] == "dark"
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["theme"] == "light"


class TestFutureSchemaArchival:
    def test_future_schema_archives_primary_bytes_and_warns(self, tmp_config: Path, caplog):
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 4
        primary_bytes = json.dumps(
            {"config_schema_version": future_schema, "theme": "dark"},
            indent=3,
        ).encode()
        tmp_config.write_bytes(primary_bytes)
        archive = tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json")

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert loaded.theme == "dark"
        assert archive.read_bytes() == primary_bytes
        assert any(str(archive) in record.message for record in caplog.records)

    def test_identical_future_schema_reload_reuses_archive(self, tmp_config: Path):
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 1
        tmp_config.write_text(
            json.dumps({"config_schema_version": future_schema, "anki_deck_name": "Future"}),
            encoding="utf-8",
        )

        GUIConfigManager.load_config()
        GUIConfigManager.load_config()

        assert sorted(tmp_config.parent.glob(f"gui_config.from-schema-{future_schema}*.json")) == [
            tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json")
        ]

    def test_different_future_configs_with_same_schema_get_numbered_archives(self, tmp_config: Path):
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 2
        first_bytes = json.dumps({"config_schema_version": future_schema, "anki_deck_name": "First"}).encode()
        second_bytes = json.dumps({"config_schema_version": future_schema, "anki_deck_name": "Second"}).encode()

        tmp_config.write_bytes(first_bytes)
        GUIConfigManager.load_config()
        tmp_config.write_bytes(second_bytes)
        GUIConfigManager.load_config()

        assert tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json").read_bytes() == first_bytes
        assert tmp_config.with_name(f"gui_config.from-schema-{future_schema}.2.json").read_bytes() == second_bytes

    def test_future_schema_backup_recovery_archives_backup_bytes(self, tmp_config: Path):
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 5
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        bak_bytes = json.dumps(
            {
                "config_schema_version": future_schema,
                "theme": "dark",
            },
            indent=3,
        ).encode()
        archive = tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json")
        tmp_config.write_bytes(b"{BROKEN PRIMARY")
        bak_path.write_bytes(bak_bytes)

        loaded = GUIConfigManager.load_config()

        assert loaded.theme == "dark"
        assert archive.read_bytes() == bak_bytes

    def test_identical_numbered_future_schema_archive_is_reused(self, tmp_config: Path):
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 6
        future_bytes = json.dumps(
            {
                "config_schema_version": future_schema,
                "anki_deck_name": "Future",
            }
        ).encode()
        base = tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json")
        numbered = tmp_config.with_name(f"gui_config.from-schema-{future_schema}.2.json")
        tmp_config.write_bytes(future_bytes)
        base.write_bytes(b"different")
        numbered.write_bytes(future_bytes)

        GUIConfigManager.load_config()

        assert base.read_bytes() == b"different"
        assert numbered.read_bytes() == future_bytes
        assert not tmp_config.with_name(f"gui_config.from-schema-{future_schema}.3.json").exists()

    def test_archive_future_opt_out_writes_no_archive(self, tmp_config: Path, tmp_path: Path):
        """``archive_future=False`` loads a future-schema sidecar without archiving.

        The archive name is derived from ``CONFIG_FILE``, so archiving a
        sidecar would name it ``gui_config.from-schema-N.json`` — a file that
        never held gui_config.json's bytes.
        """
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 7
        sidecar = tmp_path / "anime.json"
        sidecar.write_text(
            json.dumps({"config_schema_version": future_schema, "theme": "dark"}),
            encoding="utf-8",
        )

        loaded = GUIConfigManager._parse_and_migrate(sidecar, archive_future=False)

        assert loaded.theme == "dark"
        assert list(tmp_path.glob("*from-schema*")) == []

    def test_archive_future_defaults_to_archiving(self, tmp_config: Path, tmp_path: Path):
        """The default keeps today's behaviour — the opt-out must be explicit."""
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 8
        sidecar = tmp_path / "anime.json"
        sidecar_bytes = json.dumps({"config_schema_version": future_schema, "theme": "dark"}).encode()
        sidecar.write_bytes(sidecar_bytes)

        GUIConfigManager._parse_and_migrate(sidecar)

        archive = tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json")
        assert archive.read_bytes() == sidecar_bytes

    def test_normal_saves_never_modify_future_schema_archives(self, tmp_config: Path):
        future_schema = GUIConfigManager.CONFIG_SCHEMA_VERSION + 3
        future_bytes = json.dumps({"config_schema_version": future_schema, "theme": "dark"}).encode()
        archive = tmp_config.with_name(f"gui_config.from-schema-{future_schema}.json")
        tmp_config.write_bytes(future_bytes)
        GUIConfigManager.load_config()

        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))
        GUIConfigManager.save_config(replace(create_default_config(), theme="system"))

        assert archive.read_bytes() == future_bytes
        assert not tmp_config.with_name(f"gui_config.from-schema-{future_schema}.2.json").exists()


class TestAtomicSave:
    def test_failed_dump_leaves_previous_file_intact(self, tmp_config: Path, monkeypatch):
        """If serialization fails mid-write, the prior config file is untouched.

        Non-atomic in-place truncation would leave an empty/partial file;
        staging to a temp + os.replace keeps the previous good file.
        """
        # Establish a known-good on-disk config.
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        original = tmp_config.read_text(encoding="utf-8")
        assert '"theme": "dark"' in original

        # Make json.dump blow up partway through the NEXT save.
        import anki_miner.gui.utils.config_manager as cm

        def exploding_dump(*args, **kwargs):
            raise ValueError("boom mid-serialize")

        monkeypatch.setattr(cm.json, "dump", exploding_dump)

        with pytest.raises(ValueError):
            GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        # The previous good file must still be there and unchanged.
        assert tmp_config.read_text(encoding="utf-8") == original
        # No orphaned temp file beside it.
        assert not tmp_config.with_suffix(tmp_config.suffix + ".tmp").exists()

    def test_save_then_load_round_trips(self, tmp_config: Path):
        """Atomic save must still produce a loadable file (no behaviour change)."""
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        assert GUIConfigManager.load_config().theme == "dark"

    def test_no_temp_left_after_successful_save(self, tmp_config: Path):
        GUIConfigManager.save_config(create_default_config())
        assert tmp_config.exists()
        assert not tmp_config.with_suffix(tmp_config.suffix + ".tmp").exists()

    def test_backup_rotation_preserves_prior_config(self, tmp_config: Path):
        """The previous good config is rotated to .bak before each overwrite.

        First save has nothing to back up; the second save's .bak must hold the
        FIRST config's contents (one-overwrite recovery), not the second's.
        """
        import json

        bak_path = tmp_config.with_name(tmp_config.name + ".bak")

        # First save: nothing existed, so no backup is created.
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        assert not bak_path.exists()

        # Second save overwrites; the prior (dark) config rotates to .bak.
        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        assert tmp_config.exists()
        assert json.loads(tmp_config.read_text(encoding="utf-8"))["theme"] == "light"

        assert bak_path.exists()
        assert json.loads(bak_path.read_text(encoding="utf-8"))["theme"] == "dark"

    def test_save_skips_backup_rotation_for_unparseable_primary(self, tmp_config: Path, caplog):
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        bak_bytes = json.dumps(
            {
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                "theme": "dark",
            }
        ).encode()
        tmp_config.write_bytes(b"{BROKEN PRIMARY")
        bak_path.write_bytes(bak_bytes)

        with caplog.at_level("WARNING"):
            GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        assert bak_path.read_bytes() == bak_bytes
        assert json.loads(tmp_config.read_bytes())["theme"] == "light"
        assert any(
            "rotation" in record.message.lower() and "unparseable" in record.message.lower()
            for record in caplog.records
        )

    def test_save_skips_backup_rotation_for_non_object_primary(self, tmp_config: Path):
        """A JSON array primary parses but is not a config — must not rotate."""
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        bak_bytes = json.dumps(
            {
                "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                "theme": "dark",
            }
        ).encode()
        tmp_config.write_bytes(b"[]")
        bak_path.write_bytes(bak_bytes)

        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        assert bak_path.read_bytes() == bak_bytes
        assert json.loads(tmp_config.read_bytes())["theme"] == "light"

    def test_save_aborts_when_primary_unreadable(self, tmp_config: Path, monkeypatch):
        """A read OSError is not corruption — the save must abort untouched."""
        import anki_miner.gui.utils.config_manager as cm

        primary_bytes = json.dumps({"theme": "dark"}).encode()
        tmp_config.write_bytes(primary_bytes)
        bak_path = tmp_config.with_name(tmp_config.name + ".bak")
        bak_path.write_bytes(b'{"theme": "dark"}')

        original_read_bytes = cm.Path.read_bytes

        def _fail_on_primary(self: Path) -> bytes:
            if self == tmp_config:
                raise OSError("transient read failure")
            return original_read_bytes(self)

        monkeypatch.setattr(cm.Path, "read_bytes", _fail_on_primary)
        with pytest.raises(OSError):
            GUIConfigManager.save_config(replace(create_default_config(), theme="light"))
        monkeypatch.undo()

        assert tmp_config.read_bytes() == primary_bytes
        assert bak_path.read_bytes() == b'{"theme": "dark"}'

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
    def test_backup_is_owner_only_before_copy(self, tmp_config: Path, monkeypatch):
        import anki_miner.gui.utils.config_manager as cm

        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        observed_modes: list[int | None] = []
        real_copyfile = cm.shutil.copyfile

        def inspect_mode_before_copy(src, dst, *args, **kwargs):
            destination = Path(dst)
            observed_modes.append(stat.S_IMODE(destination.stat().st_mode) if destination.exists() else None)
            return real_copyfile(src, dst, *args, **kwargs)

        monkeypatch.setattr(cm.shutil, "copyfile", inspect_mode_before_copy)
        old_umask = os.umask(0)
        try:
            GUIConfigManager.save_config(replace(create_default_config(), theme="light"))
        finally:
            os.umask(old_umask)

        assert observed_modes == [0o600]

    def test_non_posix_save_skips_chmod(self, tmp_config: Path, monkeypatch):
        import anki_miner.gui.utils.config_manager as cm

        chmod = MagicMock()
        monkeypatch.setattr(cm, "os", types.SimpleNamespace(name="nt", chmod=chmod, replace=os.replace))

        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        chmod.assert_not_called()

    def test_staging_file_name_is_unique_not_the_shared_fixed_tmp(self, tmp_config: Path, monkeypatch):
        """The single-instance guard is advisory ("Continue anyway"), so two
        processes can race a save. A shared, fixed-name ``.tmp`` staging file
        lets their writes interleave and byte-splice a corrupt primary; a
        unique name per save closes that race.
        """
        import anki_miner.gui.utils.config_manager as cm

        captured: list[str] = []
        real_dump = cm.json.dump

        def spying_dump(obj, fp, *args, **kwargs):
            captured.append(Path(fp.name).name)
            return real_dump(obj, fp, *args, **kwargs)

        monkeypatch.setattr(cm.json, "dump", spying_dump)

        GUIConfigManager.save_config(create_default_config())
        GUIConfigManager.save_config(create_default_config())

        assert len(captured) == 2
        fixed_name = tmp_config.name + ".tmp"
        assert captured[0] != fixed_name
        assert captured[1] != fixed_name
        assert captured[0] != captured[1]

    def test_no_leftover_sibling_of_any_name_after_successful_save(self, tmp_config: Path):
        """Generalizes test_no_temp_left_after_successful_save: no stray
        staging file of ANY name (not just the old fixed ``.tmp``) survives."""
        GUIConfigManager.save_config(create_default_config())

        assert tmp_config.exists()
        json.loads(tmp_config.read_text(encoding="utf-8"))
        leftovers = [p for p in tmp_config.parent.iterdir() if p != tmp_config]
        assert leftovers == []

    def test_dump_failure_leaves_previous_file_intact_no_litter_of_any_name(self, tmp_config: Path, monkeypatch):
        """Generalizes test_failed_dump_leaves_previous_file_intact: checks for
        ANY leftover staging file, not just the old fixed ``.tmp`` name."""
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        original = tmp_config.read_bytes()

        import anki_miner.gui.utils.config_manager as cm

        def exploding_dump(*args, **kwargs):
            raise ValueError("boom mid-serialize")

        monkeypatch.setattr(cm.json, "dump", exploding_dump)

        with pytest.raises(ValueError):
            GUIConfigManager.save_config(replace(create_default_config(), theme="light"))

        assert tmp_config.read_bytes() == original
        leftovers = [p for p in tmp_config.parent.iterdir() if p != tmp_config]
        assert leftovers == []


class TestRoundTripImmutabilityAndPaths:
    """OVH-018 + OVH-031/OVH-072: save→load round-trip for all Path fields and
    immutable collection fields."""

    def test_all_path_fields_survive_round_trip(self, tmp_config: Path, tmp_path: Path):
        """Every Path-typed field must come back as Path (or None) after save→load.

        Covers the four previously-omitted fields (OVH-031/OVH-072):
        youtube_cookies_file, youtube_ffmpeg_location, ffmpeg_location, ffprobe_location.
        """
        cfg = replace(
            create_default_config(),
            youtube_cookies_file=tmp_path / "cookies.txt",
            youtube_ffmpeg_location=tmp_path / "ytffmpeg",
            ffmpeg_location=tmp_path / "ffmpeg",
            ffprobe_location=tmp_path / "ffprobe",
        )
        GUIConfigManager.save_config(cfg)
        loaded = GUIConfigManager.load_config()

        # Spot-check the four previously-omitted Path|None fields
        assert isinstance(loaded.youtube_cookies_file, Path)
        assert loaded.youtube_cookies_file == tmp_path / "cookies.txt"
        assert isinstance(loaded.youtube_ffmpeg_location, Path)
        assert loaded.youtube_ffmpeg_location == tmp_path / "ytffmpeg"
        assert isinstance(loaded.ffmpeg_location, Path)
        assert loaded.ffmpeg_location == tmp_path / "ffmpeg"
        assert isinstance(loaded.ffprobe_location, Path)
        assert loaded.ffprobe_location == tmp_path / "ffprobe"

        # Also verify the always-present Path fields are still Path objects
        assert isinstance(loaded.jmdict_path, Path)
        assert isinstance(loaded.dicts_root, Path)
        assert isinstance(loaded.audio_packs_root, Path)
        assert isinstance(loaded.pitch_accent_path, Path)
        assert isinstance(loaded.known_words_db_path, Path)
        assert isinstance(loaded.stats_db_path, Path)
        assert isinstance(loaded.log_path, Path)
        assert isinstance(loaded.themes_root, Path)
        assert isinstance(loaded.media_temp_folder, Path)

    def test_anki_fields_round_trips_correctly(self, tmp_config: Path):
        """anki_fields must survive save→load with correct values and as MappingProxyType."""
        custom_fields = dict(create_default_config().anki_fields)
        custom_fields["word"] = "CustomExpr"
        custom_fields["sentence"] = "CustomSent"
        cfg = AnkiMinerConfig(anki_fields=custom_fields)

        GUIConfigManager.save_config(cfg)
        loaded = GUIConfigManager.load_config()

        assert isinstance(loaded.anki_fields, types.MappingProxyType)
        assert loaded.anki_fields["word"] == "CustomExpr"
        assert loaded.anki_fields["sentence"] == "CustomSent"

    def test_card_type_marker_round_trips_correctly(self, tmp_config: Path):
        """card_type + card_type_marker_fields survive save→load (proxy + values)."""
        custom_markers = {**create_default_config().card_type_marker_fields, "click": "MyClick"}
        cfg = AnkiMinerConfig(card_type="click", card_type_marker_fields=custom_markers)

        GUIConfigManager.save_config(cfg)
        loaded = GUIConfigManager.load_config()

        assert loaded.card_type == "click"
        assert isinstance(loaded.card_type_marker_fields, types.MappingProxyType)
        assert loaded.card_type_marker_fields["click"] == "MyClick"
        assert loaded.card_type_marker_fields["audio"] == "IsAudioCard"

    def test_card_type_defaults_round_trip(self, tmp_config: Path):
        """A default config round-trips with card_type disabled and JPMN marker names."""
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.card_type == ""
        assert loaded.card_type_marker_fields["word_and_sentence"] == "IsWordAndSentenceCard"

    def test_allowed_pos_round_trips_as_tuple(self, tmp_config: Path):
        """allowed_pos must come back as a tuple after save→load."""
        cfg = AnkiMinerConfig(allowed_pos=("名詞", "動詞"))
        GUIConfigManager.save_config(cfg)
        loaded = GUIConfigManager.load_config()
        assert isinstance(loaded.allowed_pos, tuple)
        assert loaded.allowed_pos == ("名詞", "動詞")

    def test_excluded_subtypes_round_trips_as_tuple(self, tmp_config: Path):
        """excluded_subtypes must come back as a tuple after save→load."""
        cfg = AnkiMinerConfig(excluded_subtypes=("非自立", "数詞"))
        GUIConfigManager.save_config(cfg)
        loaded = GUIConfigManager.load_config()
        assert isinstance(loaded.excluded_subtypes, tuple)
        assert loaded.excluded_subtypes == ("非自立", "数詞")

    def test_condenser_fields_round_trip(self, tmp_config: Path):
        """All six condenser_* fields must survive save→load into gui_config.json."""
        import json

        cfg = replace(
            create_default_config(),
            condenser_padding_ms=750,
            condenser_offset_ms=-250,
            condenser_output_format="flac",
            condenser_bitrate_kbps=128,
            condenser_filtered_chars="XYZ★",
            condenser_write_subtitles=True,
        )
        GUIConfigManager.save_config(cfg)

        # Fields are actually serialized into the on-disk JSON.
        on_disk = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert on_disk["condenser_padding_ms"] == 750
        assert on_disk["condenser_offset_ms"] == -250
        assert on_disk["condenser_output_format"] == "flac"
        assert on_disk["condenser_bitrate_kbps"] == 128
        assert on_disk["condenser_filtered_chars"] == "XYZ★"
        assert on_disk["condenser_write_subtitles"] is True

        loaded = GUIConfigManager.load_config()
        assert loaded.condenser_padding_ms == 750
        assert loaded.condenser_offset_ms == -250
        assert loaded.condenser_output_format == "flac"
        assert loaded.condenser_bitrate_kbps == 128
        assert loaded.condenser_filtered_chars == "XYZ★"
        assert loaded.condenser_write_subtitles is True

    def test_downloader_defaults(self, tmp_config: Path):
        """A default config carries the Download tool's option defaults."""
        cfg = AnkiMinerConfig()
        assert cfg.downloader_format_preset == "best"
        assert cfg.downloader_custom_format == ""
        assert cfg.downloader_write_subtitles is False
        assert cfg.downloader_subtitle_langs == "ja"
        assert cfg.downloader_embed_thumbnail is False
        assert cfg.downloader_embed_metadata is False

    def test_downloader_fields_round_trip(self, tmp_config: Path):
        """All six downloader_* fields must survive save→load into gui_config.json."""
        import json

        cfg = replace(
            create_default_config(),
            downloader_format_preset="audio_mp3",
            downloader_custom_format="bestaudio[ext=m4a]",
            downloader_write_subtitles=True,
            downloader_subtitle_langs="ja,en",
            downloader_embed_thumbnail=True,
            downloader_embed_metadata=True,
        )
        GUIConfigManager.save_config(cfg)

        # Fields are actually serialized into the on-disk JSON.
        on_disk = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert on_disk["downloader_format_preset"] == "audio_mp3"
        assert on_disk["downloader_custom_format"] == "bestaudio[ext=m4a]"
        assert on_disk["downloader_write_subtitles"] is True
        assert on_disk["downloader_subtitle_langs"] == "ja,en"
        assert on_disk["downloader_embed_thumbnail"] is True
        assert on_disk["downloader_embed_metadata"] is True

        loaded = GUIConfigManager.load_config()
        assert loaded.downloader_format_preset == "audio_mp3"
        assert loaded.downloader_custom_format == "bestaudio[ext=m4a]"
        assert loaded.downloader_write_subtitles is True
        assert loaded.downloader_subtitle_langs == "ja,en"
        assert loaded.downloader_embed_thumbnail is True
        assert loaded.downloader_embed_metadata is True

    def test_condenser_defaults_round_trip(self, tmp_config: Path):
        """A default config round-trips with the documented condenser defaults."""
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert loaded.condenser_padding_ms == 500
        assert loaded.condenser_offset_ms == 0
        assert loaded.condenser_output_format == "mp3"
        assert loaded.condenser_bitrate_kbps == 96
        assert loaded.condenser_filtered_chars == "♪♫♬♩〜～"
        assert loaded.condenser_write_subtitles is False


class TestSchemaVersionMarker:
    """config_schema_version marker (ARC-002): stamped on save, tolerant on load."""

    def test_saved_json_carries_schema_version(self, tmp_config: Path):
        """save_config stamps the current CONFIG_SCHEMA_VERSION into the file."""
        import json

        GUIConfigManager.save_config(create_default_config())
        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert raw["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION

    def test_markerless_json_still_loads(self, tmp_config: Path):
        """A pre-versioning config (version 0, no marker) loads cleanly, no reset."""
        import json

        tmp_config.write_text(json.dumps({"anki_deck_name": "Legacy"}), encoding="utf-8")
        loaded = GUIConfigManager.load_config()
        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.anki_deck_name == "Legacy"

    def test_marker_does_not_leak_onto_dataclass(self, tmp_config: Path):
        """The marker is JSON-only; it must never become a dataclass attribute."""
        GUIConfigManager.save_config(create_default_config())
        loaded = GUIConfigManager.load_config()
        assert not hasattr(loaded, "config_schema_version")


class TestActiveProfileIdMarker:
    """active_profile_id / profile_name: JSON-only markers, like the schema version."""

    def test_isolation_fixture_resets_active_profile_id(self):
        """The autouse home isolation must hand every test a clean marker.

        Pins the ``tests/_home_isolation.py`` hook: without it the class
        attribute is process-global mutable state and a test that sets it would
        make an unrelated later save stamp a stale id.
        """
        assert GUIConfigManager.ACTIVE_PROFILE_ID is None

    def test_saved_json_carries_active_profile_id(self, tmp_config: Path, monkeypatch):
        monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", "anime")

        GUIConfigManager.save_config(create_default_config())

        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert raw["active_profile_id"] == "anime"

    def test_saved_json_omits_active_profile_id_when_unset(self, tmp_config: Path, monkeypatch):
        """No active profile means the key is ABSENT, not null."""
        monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", None)

        GUIConfigManager.save_config(create_default_config())

        raw = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert "active_profile_id" not in raw

    def test_markers_never_reach_the_dataclass(self, tmp_config: Path):
        """A file carrying both markers loads identically to one without them."""
        base = {
            "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
            "anki_deck_name": "Mining",
            "theme": "dark",
        }

        tmp_config.write_text(json.dumps(base), encoding="utf-8")
        without_markers = GUIConfigManager.load_config()

        tmp_config.write_text(
            json.dumps({**base, "active_profile_id": "anime", "profile_name": "Anime"}),
            encoding="utf-8",
        )
        with_markers = GUIConfigManager.load_config()

        assert with_markers == without_markers
        assert not hasattr(with_markers, "active_profile_id")
        assert not hasattr(with_markers, "profile_name")

    def test_markers_are_not_logged_as_dropped_unknown_keys(self, tmp_config: Path, caplog):
        """Popped before the unknown-key filter, so they never spam every load."""
        tmp_config.write_text(
            json.dumps(
                {
                    "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                    "active_profile_id": "anime",
                    "profile_name": "Anime",
                    "removed_legacy_setting": 1,
                }
            ),
            encoding="utf-8",
        )

        with caplog.at_level("DEBUG", logger="anki_miner.gui.utils.config_manager"):
            GUIConfigManager.load_config()

        assert "removed_legacy_setting" in caplog.text
        assert "active_profile_id" not in caplog.text
        assert "profile_name" not in caplog.text

    def test_read_active_profile_id_returns_stored_id(self, tmp_config: Path):
        tmp_config.write_text(
            json.dumps({"active_profile_id": "anime", "theme": "dark"}),
            encoding="utf-8",
        )

        assert GUIConfigManager.read_active_profile_id() == "anime"

    def test_read_active_profile_id_returns_none_for_missing_file(self, tmp_config: Path):
        assert not tmp_config.exists()
        assert GUIConfigManager.read_active_profile_id() is None

    def test_a_missing_config_is_read_silently(self, tmp_config: Path, caplog):
        """A fresh install has no config yet; that is not a fault to warn about.

        Without the exists() guard the bounded reader logs a WARNING for the
        file it cannot open, on every first boot, from a step whose whole answer
        is the perfectly ordinary "no active profile".
        """
        assert not tmp_config.exists()

        with caplog.at_level("WARNING"):
            assert GUIConfigManager.read_active_profile_id() is None

        assert caplog.records == []

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param("{BROKEN", id="invalid-json"),
            pytest.param('["not", "a", "mapping"]', id="list-root"),
            pytest.param('{"theme": "dark"}', id="key-absent"),
            pytest.param('{"active_profile_id": 123}', id="not-a-string"),
            pytest.param('{"active_profile_id": ""}', id="empty-string"),
        ],
    )
    def test_read_active_profile_id_returns_none_for_unusable_marker(self, tmp_config: Path, raw: str):
        tmp_config.write_text(raw, encoding="utf-8")

        assert GUIConfigManager.read_active_profile_id() is None

    def test_read_active_profile_id_swallows_oserror(self, tmp_config: Path, monkeypatch):
        """An unreadable config must not crash the boot step that seeds the marker."""
        tmp_config.write_text(json.dumps({"active_profile_id": "anime"}), encoding="utf-8")

        def _boom(self: Path, *args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "open", _boom)

        assert GUIConfigManager.read_active_profile_id() is None


class TestNativeFileDialogsMigration:
    """Schema 4 turns the OS-native file pickers back on, once.

    The dataclass default alone reaches nobody who has already run the app:
    every existing gui_config.json carries an explicit False that a load would
    otherwise preserve. Both halves of the shim matter — the load path here,
    and the import path (the field is portable, so a pre-v4 export would write
    the old value straight back).
    """

    def test_pre_v4_config_gets_native_pickers(self, tmp_config: Path):
        tmp_config.write_text(
            json.dumps({"config_schema_version": 3, "use_native_file_dialogs": False}),
            encoding="utf-8",
        )

        assert GUIConfigManager.load_config().use_native_file_dialogs is True

    def test_unversioned_config_gets_native_pickers(self, tmp_config: Path):
        tmp_config.write_text(json.dumps({"use_native_file_dialogs": False}), encoding="utf-8")

        assert GUIConfigManager.load_config().use_native_file_dialogs is True

    def test_v4_opt_out_is_preserved(self, tmp_config: Path):
        tmp_config.write_text(
            json.dumps(
                {
                    "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                    "use_native_file_dialogs": False,
                }
            ),
            encoding="utf-8",
        )

        assert GUIConfigManager.load_config().use_native_file_dialogs is False

    def test_saved_config_is_stamped_with_the_current_schema(self, tmp_config: Path):
        GUIConfigManager.save_config(create_default_config())

        written = json.loads(tmp_config.read_text(encoding="utf-8"))
        assert written["config_schema_version"] == GUIConfigManager.CONFIG_SCHEMA_VERSION
        assert written["use_native_file_dialogs"] is True

    def test_pre_v4_import_does_not_revert_the_user(self, tmp_config: Path, tmp_path: Path):
        source = tmp_path / "old-settings.json"
        source.write_text(
            json.dumps({"config_schema_version": 3, "use_native_file_dialogs": False}),
            encoding="utf-8",
        )

        result = GUIConfigManager.import_config(source, create_default_config())

        assert result.config.use_native_file_dialogs is True

    def test_v4_import_honours_an_explicit_opt_out(self, tmp_config: Path, tmp_path: Path):
        source = tmp_path / "new-settings.json"
        source.write_text(
            json.dumps(
                {
                    "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
                    "use_native_file_dialogs": False,
                }
            ),
            encoding="utf-8",
        )

        result = GUIConfigManager.import_config(source, create_default_config())

        assert result.config.use_native_file_dialogs is False
