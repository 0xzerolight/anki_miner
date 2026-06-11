"""Tests for GUIConfigManager atomic save + resilient load (T-31).

The config file is the single source of truth for every GUI setting. A crash
mid-write must not wipe it (atomic temp + os.replace), and an unreadable file
must fall back to defaults rather than crash startup (OSError in the load
except tuple).
"""

from __future__ import annotations

import builtins
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
