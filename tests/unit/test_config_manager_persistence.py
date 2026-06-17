"""Tests for GUIConfigManager atomic save + resilient load (T-31).

The config file is the single source of truth for every GUI setting. A crash
mid-write must not wipe it (atomic temp + os.replace), and an unreadable file
must fall back to defaults rather than crash startup (OSError in the load
except tuple).
"""

from __future__ import annotations

import builtins
import types
from dataclasses import replace
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager


@pytest.fixture
def tmp_config(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", cfg_path)
    return cfg_path


class TestLoadResilience:
    def test_partial_write_truncation_falls_back_to_defaults(self, tmp_config: Path, caplog):
        """A power-loss-truncated (invalid-JSON) file must load defaults + warn.

        Pins the existing JSONDecodeError fallback so the new OSError branch
        doesn't regress it.
        """
        # Save a valid config, then corrupt it as a crash mid-write would.
        GUIConfigManager.save_config(replace(create_default_config(), theme="dark"))
        tmp_config.write_text('{"theme": "dark", "anki_dec', encoding="utf-8")

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == create_default_config().theme  # defaults, not "dark"
        assert any("Invalid config" in r.message for r in caplog.records)

    def test_unreadable_file_oserror_falls_back_to_defaults(self, tmp_config: Path, monkeypatch, caplog):
        """An OSError while reading (e.g. chmod 000) must NOT crash startup."""
        tmp_config.write_text('{"theme": "dark"}', encoding="utf-8")

        real_open = builtins.open

        def boom(self_path, *args, **kwargs):
            # Only the config file read raises; everything else passes through.
            if Path(self_path) == tmp_config and "r" in (args[0] if args else kwargs.get("mode", "r")):
                raise OSError("Permission denied")
            return real_open(self_path, *args, **kwargs)

        # Path.open delegates to io.open/builtins.open; patch at the Path level.
        monkeypatch.setattr(Path, "open", lambda self, *a, **k: boom(self, *a, **k))

        with caplog.at_level("WARNING"):
            loaded = GUIConfigManager.load_config()

        assert isinstance(loaded, AnkiMinerConfig)
        assert loaded.theme == create_default_config().theme
        assert any("config file" in r.message.lower() for r in caplog.records)


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
        assert isinstance(loaded.frequency_list_path, Path)
        assert isinstance(loaded.known_words_db_path, Path)
        assert isinstance(loaded.history_db_path, Path)
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
