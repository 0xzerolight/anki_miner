"""Native-crash capture.

Eight identical process deaths in one field report and not one line in the log
explaining any of them — because SIGABRT never reaches Python's traceback
machinery. These pin the sink that fixes that.
"""

from __future__ import annotations

import faulthandler
import logging

import pytest

from anki_miner.gui import app as app_module


@pytest.fixture(autouse=True)
def _restore_faulthandler():
    """Leave the process exactly as found: the module caches its stream, and a
    leaked enable() would follow this test into every later one."""
    was_enabled = faulthandler.is_enabled()
    previous = app_module._crash_stream
    app_module._crash_stream = None
    yield
    stream = app_module._crash_stream
    faulthandler.disable()
    if stream is not None and stream is not previous:
        stream.close()
    app_module._crash_stream = previous
    if was_enabled:
        faulthandler.enable()


class TestEnable:
    def test_enables_and_opens_the_stream(self, tmp_path):
        app_module._enable_faulthandler(tmp_path / app_module.CRASH_LOG_NAME)
        assert faulthandler.is_enabled()
        assert app_module.crash_stream() is not None

    def test_creates_a_missing_parent_directory(self, tmp_path):
        app_module._enable_faulthandler(tmp_path / "nested" / app_module.CRASH_LOG_NAME)
        assert (tmp_path / "nested").is_dir()

    def test_second_call_is_a_no_op(self, tmp_path):
        app_module._enable_faulthandler(tmp_path / app_module.CRASH_LOG_NAME)
        first = app_module.crash_stream()
        app_module._enable_faulthandler(tmp_path / "other.crash")
        assert app_module.crash_stream() is first
        assert not (tmp_path / "other.crash").exists()

    def test_unwritable_path_does_not_block_startup(self, tmp_path):
        blocked = tmp_path / "afile"
        blocked.write_text("not a directory", encoding="utf-8")
        app_module._enable_faulthandler(blocked / "nested" / "x.crash")
        assert app_module.crash_stream() is None


class TestFoldPreviousCrash:
    def test_previous_stack_is_logged_and_rotated(self, tmp_path, caplog):
        """This is what puts a native stack in front of whoever reads the log —
        and into the diagnostics bundle, which is usually all a maintainer gets."""
        crash = tmp_path / app_module.CRASH_LOG_NAME
        crash.write_text("Fatal Python error: Segmentation fault\n", encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger=app_module.logger.name):
            app_module._enable_faulthandler(crash)
        assert "Segmentation fault" in caplog.text
        assert (tmp_path / f"{app_module.CRASH_LOG_NAME}.1").is_file()

    def test_empty_file_is_not_reported(self, tmp_path, caplog):
        crash = tmp_path / app_module.CRASH_LOG_NAME
        crash.write_text("   \n", encoding="utf-8")
        with caplog.at_level(logging.ERROR, logger=app_module.logger.name):
            app_module._enable_faulthandler(crash)
        assert "native crash" not in caplog.text
        assert not (tmp_path / f"{app_module.CRASH_LOG_NAME}.1").exists()

    def test_absent_file_is_not_reported(self, tmp_path, caplog):
        with caplog.at_level(logging.ERROR, logger=app_module.logger.name):
            app_module._enable_faulthandler(tmp_path / app_module.CRASH_LOG_NAME)
        assert "native crash" not in caplog.text


class TestBundleCollection:
    def test_crash_files_are_collected(self, tmp_path, monkeypatch):
        from anki_miner.config import paths
        from anki_miner.diagnostics import bundle

        monkeypatch.setattr(paths, "ANKI_MINER_HOME", tmp_path)
        monkeypatch.setattr(bundle.paths, "ANKI_MINER_HOME", tmp_path, raising=False)
        (tmp_path / "anki_miner.crash").write_text("boom", encoding="utf-8")
        (tmp_path / "anki_miner.crash.1").write_text("older boom", encoding="utf-8")

        members, _missing = bundle.collect_log_members()
        names = {name for name, _content in members}
        assert "anki_miner.crash" in names
        assert "anki_miner.crash.1" in names
