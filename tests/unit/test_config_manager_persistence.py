"""Tests for GUIConfigManager atomic save + resilient load (T-31).

The config file is the single source of truth for every GUI setting. A crash
mid-write must not wipe it (atomic temp + os.replace), and an unreadable file
must fall back to defaults rather than crash startup (OSError in the load
except tuple).
"""

from __future__ import annotations

import builtins
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
