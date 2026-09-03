"""What one session records about itself: end, effective config, home fallback.

Three gaps this closes, all of them things a maintainer had to ask the user
for by hand:

* **Session end.** Without it a log is a run of ``Session start`` lines with no
  way to tell a clean quit from a kill, an OS shutdown or a native crash. Two
  adjacent starts with nothing between them now mean the process died hard.
* **Effective config.** Backfill-shaped reports ("no fields were written")
  divide immediately on ``note_type`` plus the mapped ``fields``, and every
  chain length answers "is anything even enabled" before a single lookup is
  read.
* **Home fallback.** ``Path.home()`` failing silently relocates config, logs,
  dictionaries and caches into the system temp dir, where a reboot deletes
  them. The install then looks empty for no stated reason.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture
def fresh_session(monkeypatch):
    """A module whose once-per-process session-end flag has not been used yet."""
    from anki_miner.gui import app as app_module

    monkeypatch.setattr(app_module, "_SESSION_ENDED", False, raising=False)
    monkeypatch.setattr(app_module, "SESSION_ID", "abcd1234", raising=False)
    return app_module


class TestSessionEnd:
    def test_records_reason_exit_code_uptime_stalls_and_threads(self, fresh_session, caplog):
        app_module = fresh_session

        with caplog.at_level(logging.INFO, logger=app_module.logger.name):
            app_module._log_session_end(3, reason="exec-returned")

        records = [r for r in caplog.records if r.getMessage().startswith("Session end:")]
        assert len(records) == 1
        message = records[0].getMessage()
        assert "session_id=abcd1234" in message
        assert "reason=exec-returned" in message
        assert "exit_code=3" in message
        assert "uptime_s=" in message
        assert "stalls=" in message
        assert "threads=" in message
        assert records[0].levelno == logging.INFO

    def test_names_the_live_threads(self, fresh_session, caplog):
        app_module = fresh_session

        with caplog.at_level(logging.INFO, logger=app_module.logger.name):
            app_module._log_session_end(0, reason="exec-returned")

        message = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Session end:"))
        import threading

        assert threading.current_thread().name in message

    def test_second_call_is_a_no_op(self, fresh_session, caplog):
        """atexit fires after the explicit call; the second must not double-report."""
        app_module = fresh_session

        with caplog.at_level(logging.INFO, logger=app_module.logger.name):
            app_module._log_session_end(0, reason="exec-returned")
            app_module._log_session_end(None, reason="atexit")

        records = [r for r in caplog.records if r.getMessage().startswith("Session end:")]
        assert len(records) == 1
        assert "reason=exec-returned" in records[0].getMessage()

    def test_missing_exit_code_renders_as_absent(self, fresh_session, caplog):
        app_module = fresh_session

        with caplog.at_level(logging.INFO, logger=app_module.logger.name):
            app_module._log_session_end(None, reason="atexit")

        message = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Session end:"))
        assert "exit_code=-" in message


class TestEffectiveConfig:
    def test_records_the_settings_that_shape_a_run(self, caplog):
        from anki_miner.config import create_default_config
        from anki_miner.gui import app as app_module

        config = create_default_config()

        with caplog.at_level(logging.INFO, logger=app_module.logger.name):
            app_module._log_effective_config(config)

        records = [r for r in caplog.records if r.getMessage().startswith("Config effective:")]
        assert len(records) == 1
        message = records[0].getMessage()
        for field in (
            f"language={config.language}",
            f"ui_language={config.ui_language}",
            f"note_type={config.anki_note_type}",
            f"dicts_root={config.dicts_root}",
            f"ankiconnect={config.ankiconnect_url}",
            f"theme={config.theme}",
            f"log={config.log_path}",
        ):
            assert field in message
        assert f'deck="{config.anki_deck_name}"' in message  # whitespace-quoted by log_summary
        assert "fields=" in message
        assert "dicts=" in message
        assert "freqs=" in message
        assert "pitch=" in message
        assert "audio=" in message
        assert "zoom=" in message
        assert "native_dialogs=" in message
        assert "asr=" in message
        assert records[0].levelno == logging.INFO

    def test_reports_mapped_fields_and_enabled_chain_counts(self, caplog):
        from anki_miner.config import create_default_config
        from anki_miner.gui import app as app_module

        config = create_default_config()
        enabled = sum(1 for entry in config.dictionary_chain if entry.enabled)

        with caplog.at_level(logging.INFO, logger=app_module.logger.name):
            app_module._log_effective_config(config)

        message = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("Config effective:"))
        assert f"dicts={enabled}/{len(config.dictionary_chain)}" in message
        # Only mapped keys: an unmapped field is off, and listing it would make
        # every report look identically configured.
        assert "word" in message
        assert "expression_audio" not in message

    def test_a_broken_config_object_does_not_take_boot_down(self, caplog):
        from anki_miner.gui import app as app_module

        class _Exploding:
            def __getattr__(self, name):
                raise RuntimeError(f"no {name}")

        with caplog.at_level(logging.DEBUG, logger=app_module.logger.name):
            app_module._log_effective_config(_Exploding())

        assert not any(r.levelno >= logging.ERROR for r in caplog.records)


class TestHomeFallbackWarning:
    def test_relocated_home_is_reported_after_the_header(self, monkeypatch, caplog):
        from anki_miner.config import paths
        from anki_miner.gui import app as app_module

        monkeypatch.setattr(paths, "HOME_FALLBACK_REASON", "RuntimeError: x")
        root = logging.getLogger()
        anki_logger = logging.getLogger("anki_miner")
        levels = (root.level, anki_logger.level)
        try:
            with caplog.at_level(logging.INFO, logger=app_module.logger.name):
                app_module._log_session_boundary()
        finally:
            root.setLevel(levels[0])
            anki_logger.setLevel(levels[1])

        warnings = [r for r in caplog.records if r.getMessage().startswith("Home directory unavailable")]
        assert len(warnings) == 1
        assert warnings[0].levelno == logging.WARNING
        assert "(RuntimeError: x)" in warnings[0].getMessage()
        assert f"home and log relocated to {app_module.ANKI_MINER_HOME}" in warnings[0].getMessage()
        starts = [i for i, r in enumerate(caplog.records) if r.getMessage().startswith("Session start")]
        assert starts and caplog.records.index(warnings[0]) > starts[0]

    def test_silent_when_home_resolved_normally(self, monkeypatch, caplog):
        from anki_miner.config import paths
        from anki_miner.gui import app as app_module
        from anki_miner.gui import launch as launch_module

        monkeypatch.setattr(paths, "HOME_FALLBACK_REASON", None)
        monkeypatch.setattr(launch_module, "HOME_FALLBACK_REASON", None, raising=False)
        root = logging.getLogger()
        anki_logger = logging.getLogger("anki_miner")
        levels = (root.level, anki_logger.level)
        try:
            with caplog.at_level(logging.INFO, logger=app_module.logger.name):
                app_module._log_session_boundary()
        finally:
            root.setLevel(levels[0])
            anki_logger.setLevel(levels[1])

        assert not any(r.getMessage().startswith("Home directory unavailable") for r in caplog.records)

    def test_the_bootstrap_module_records_its_own_fallback_reason(self, monkeypatch):
        from anki_miner.gui import launch as launch_module

        monkeypatch.setattr(launch_module, "HOME_FALLBACK_REASON", None, raising=False)
        monkeypatch.setattr(
            launch_module.Path,
            "home",
            classmethod(lambda cls: (_ for _ in ()).throw(RuntimeError("no home"))),
        )

        home = launch_module._default_anki_miner_home()

        assert home.name == ".anki_miner"
        assert launch_module.HOME_FALLBACK_REASON == "RuntimeError: no home"
