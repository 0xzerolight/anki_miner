"""Tests for the logging infrastructure added by OVH-010 / OVH-009.

Covers:
- AnkiMinerConfig.log_path field (default, str coercion)
- _configure_logging() attaches a RotatingFileHandler and writes to disk
- Worker catch-all exception handlers now emit logger.exception (traceback in log)
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")


# ---------------------------------------------------------------------------
# Config field tests
# ---------------------------------------------------------------------------


class TestLogPathConfig:
    """AnkiMinerConfig.log_path field."""

    def test_log_path_default_is_under_home(self):
        """log_path defaults to ANKI_MINER_HOME / 'anki_miner.log'."""
        from anki_miner.config import AnkiMinerConfig
        from anki_miner.config.paths import ANKI_MINER_HOME

        cfg = AnkiMinerConfig()
        assert cfg.log_path == ANKI_MINER_HOME / "anki_miner.log"

    def test_log_path_is_path_object(self):
        """log_path is always a Path instance after construction."""
        from anki_miner.config import AnkiMinerConfig

        cfg = AnkiMinerConfig()
        assert isinstance(cfg.log_path, Path)

    def test_log_path_coerced_from_string(self, tmp_path):
        """log_path accepts a str and coerces it to Path via __post_init__."""
        from anki_miner.config import AnkiMinerConfig

        str_path = str(tmp_path / "custom.log")
        cfg = AnkiMinerConfig(log_path=str_path)
        assert isinstance(cfg.log_path, Path)
        assert cfg.log_path == Path(str_path)

    def test_log_path_accepts_path_object(self, tmp_path):
        """log_path accepts a Path directly and keeps it as-is."""
        from anki_miner.config import AnkiMinerConfig

        p = tmp_path / "logs" / "app.log"
        cfg = AnkiMinerConfig(log_path=p)
        assert cfg.log_path == p


# ---------------------------------------------------------------------------
# _configure_logging() tests
# ---------------------------------------------------------------------------


class TestConfigureLogging:
    """_configure_logging() in app.py attaches a RotatingFileHandler."""

    def _import_configure_logging(self):
        from anki_miner.gui.app import _configure_logging

        return _configure_logging

    def test_creates_parent_dir_if_missing(self, tmp_path):
        """_configure_logging creates the parent directory when it doesn't exist."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "nested" / "dir" / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        try:
            configure_logging(log_path)
            assert log_path.parent.exists()
        finally:
            # Restore global logging state (levels + handlers) so later tests
            # don't run with root pinned at WARNING / anki_miner at DEBUG (F6).
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in list(root.handlers):
                if h not in handlers_before:
                    h.close()
                    root.removeHandler(h)

    def test_tolerates_str_log_path(self, tmp_path):
        """_configure_logging coerces a str argument to Path (F14 robustness)."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "nested" / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        try:
            configure_logging(str(log_path))  # str, not Path
            assert log_path.parent.exists()
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in list(root.handlers):
                if h not in handlers_before:
                    h.close()
                    root.removeHandler(h)

    def test_adds_rotating_file_handler(self, tmp_path):
        """_configure_logging adds a RotatingFileHandler to the root logger."""
        from logging.handlers import RotatingFileHandler

        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "test.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]
            assert any(isinstance(h, RotatingFileHandler) for h in added)
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_log_record_written_to_file(self, tmp_path):
        """After _configure_logging, an ERROR record is written to the log file."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]

            test_logger = logging.getLogger("anki_miner.test_logging_infra")
            test_logger.error("sentinel-test-message")

            # Flush all added handlers so the record lands on disk
            for h in added:
                h.flush()

            content = log_path.read_text(encoding="utf-8")
            assert "sentinel-test-message" in content
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_root_logger_level_is_warning(self, tmp_path):
        """_configure_logging sets root logger to WARNING (not DEBUG)."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]
            assert root.level == logging.WARNING
        finally:
            root.setLevel(root_level_before)
            # configure_logging pins anki_miner to DEBUG; restore it too, or the
            # leaked level flips DEBUG-gated production paths on for later tests
            # sharing this xdist worker (see _no_logger_level_leak in conftest).
            am_logger.setLevel(am_level_before)
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_anki_miner_logger_level_is_debug(self, tmp_path):
        """_configure_logging sets the anki_miner namespace logger to DEBUG."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]
            assert am_logger.level == logging.DEBUG
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_third_party_debug_not_written_anki_miner_debug_is(self, tmp_path):
        """Third-party DEBUG records are suppressed; anki_miner DEBUG records reach the file."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        handlers_before = list(root.handlers)
        root_level_before = root.level
        am_level_before = am_logger.level
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]

            # third-party DEBUG (propagates up to root; root is WARNING → dropped)
            logging.getLogger("yt_dlp").debug("third-party-debug-noise")
            # anki_miner DEBUG (anki_miner logger is DEBUG → passes through to root handler)
            logging.getLogger("anki_miner.some_module").debug("anki-miner-debug-record")

            for h in added:
                h.flush()

            content = "" if not log_path.exists() else log_path.read_text(encoding="utf-8")

            assert "third-party-debug-noise" not in content, "Third-party DEBUG leaked into log"
            assert "anki-miner-debug-record" in content, "anki_miner DEBUG was not captured"
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_idempotent_no_duplicate_handlers(self, tmp_path):
        """Calling _configure_logging twice leaves exactly one project handler (F5)."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        root_level_before = root.level
        am_level_before = am_logger.level
        try:
            configure_logging(log_path)
            configure_logging(log_path)
            sinks = [h for h in root.handlers if getattr(h, "_anki_miner_sink", False)]
            assert len(sinks) == 1, f"expected one project handler, got {len(sinks)}"
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in list(root.handlers):
                if getattr(h, "_anki_miner_sink", False):
                    h.close()
                    root.removeHandler(h)

    def test_repoint_redirects_to_new_path(self, tmp_path):
        """A second call with a new path re-points the sink; records go to the new file (F3)."""
        configure_logging = self._import_configure_logging()
        first = tmp_path / "first.log"
        second = tmp_path / "second.log"

        root = logging.getLogger()
        am_logger = logging.getLogger("anki_miner")
        root_level_before = root.level
        am_level_before = am_logger.level
        try:
            configure_logging(first)
            configure_logging(second)

            logging.getLogger("anki_miner.repoint_test").error("after-repoint")
            for h in root.handlers:
                if getattr(h, "_anki_miner_sink", False):
                    h.flush()

            assert "after-repoint" in second.read_text(encoding="utf-8")
            # The first file was opened with delay=True and never written → no record leaks there.
            first_content = "" if not first.exists() else first.read_text(encoding="utf-8")
            assert "after-repoint" not in first_content
        finally:
            root.setLevel(root_level_before)
            am_logger.setLevel(am_level_before)
            for h in list(root.handlers):
                if getattr(h, "_anki_miner_sink", False):
                    h.close()
                    root.removeHandler(h)


# ---------------------------------------------------------------------------
# Worker exception → logger.exception tests
# ---------------------------------------------------------------------------


class TestWorkerExceptionLogging:
    """Worker catch-alls call logger.exception so tracebacks land in the log."""

    def test_single_call_worker_logs_exception(self, qapp, caplog):
        """SingleCallWorker.run() calls logger.exception on unhandled exceptions."""
        from anki_miner.gui.workers.base_worker import SingleCallWorker

        def boom():
            raise RuntimeError("intentional test failure")

        worker = SingleCallWorker(boom)
        qapp.processEvents()

        with caplog.at_level(logging.ERROR, logger="anki_miner.gui.workers.base_worker"):
            worker.run()

        # logger.exception was called — the record has exc_info with the RuntimeError
        assert any(r.exc_info is not None for r in caplog.records), "No record with exc_info found"
        # The exception type appears in the formatted traceback
        assert any(
            r.exc_info is not None and r.exc_info[1] is not None and "intentional test failure" in str(r.exc_info[1])
            for r in caplog.records
        )

    def test_episode_worker_logs_exception(self, qapp, caplog):
        """EpisodeWorkerThread.run() calls logger.exception on unhandled exceptions."""
        from anki_miner.gui.workers.episode_worker import EpisodeWorkerThread

        processor = MagicMock(name="EpisodeProcessor")
        processor.process_episode.side_effect = KeyError("missing_key")

        worker = EpisodeWorkerThread(
            processor=processor,
            video_file=Path("/fake/video.mkv"),
            subtitle_file=Path("/fake/subs.ass"),
            preview_mode=False,
            progress_callback=MagicMock(),
        )

        with caplog.at_level(logging.ERROR, logger="anki_miner.gui.workers.episode_worker"):
            worker.run()

        assert any(r.exc_info is not None for r in caplog.records)

    def test_episode_processor_logs_exception(self, tmp_path, caplog):
        """EpisodeProcessor.process_episode broad except calls logger.exception."""
        from anki_miner.config import AnkiMinerConfig
        from anki_miner.orchestration.episode_processor import EpisodeProcessor
        from anki_miner.presenters import NullPresenter, NullProgressCallback

        cfg = AnkiMinerConfig(
            media_temp_folder=tmp_path / "temp",
            jmdict_path=tmp_path / "JMdict_e",
            dicts_root=tmp_path / "dicts",
            known_words_db_path=tmp_path / "known_words.db",
            history_db_path=tmp_path / "history.db",
            stats_db_path=tmp_path / "stats.db",
        )
        processor = EpisodeProcessor(
            config=cfg,
            subtitle_parser=MagicMock(name="SubtitleParser"),
            word_filter=MagicMock(name="WordFilter"),
            media_extractor=MagicMock(name="MediaExtractor"),
            definition_service=MagicMock(name="DefinitionService"),
            anki_service=MagicMock(name="AnkiService"),
            presenter=NullPresenter(),
        )

        # Patch _phase1_parse to raise an unexpected Exception (not AnkiMinerException)
        with (
            patch.object(processor, "_phase1_parse", side_effect=RuntimeError("unexpected boom")),
            caplog.at_level(logging.ERROR, logger="anki_miner.orchestration.episode_processor"),
        ):
            processor.process_episode(
                Path("/fake/video.mkv"),
                Path("/fake/subs.ass"),
                preview_mode=False,
                progress_callback=NullProgressCallback(),
            )

        assert any(r.exc_info is not None for r in caplog.records)


# ---------------------------------------------------------------------------
# OVH-041 — validation_service generic except blocks emit logger.exception
# ---------------------------------------------------------------------------


class TestValidationServiceExceptionLogging:
    """Generic except Exception blocks in ValidationService emit traceback via logger."""

    def _make_service(self):
        from anki_miner.config import AnkiMinerConfig
        from anki_miner.services.validation_service import ValidationService

        return ValidationService(AnkiMinerConfig())

    def test_check_ankiconnect_unexpected_exception_logs_traceback(self, caplog):
        """_check_ankiconnect generic except logs exc_info."""
        from unittest.mock import patch

        svc = self._make_service()
        with (
            patch(
                "anki_miner.services.validation_service.post_action",
                side_effect=RuntimeError("boom"),
            ),
            caplog.at_level(logging.ERROR, logger="anki_miner.services.validation_service"),
        ):
            ok, msg = svc._check_ankiconnect()

        assert not ok
        assert "Unexpected error" in msg
        assert any(r.exc_info is not None for r in caplog.records)

    def test_check_tool_unexpected_exception_logs_traceback(self, caplog):
        """_check_tool (ffmpeg/ffprobe) generic except logs exc_info."""
        from unittest.mock import patch

        svc = self._make_service()
        with (
            patch(
                "anki_miner.services.validation_service.subprocess.run",
                side_effect=OSError("disk error"),
            ),
            caplog.at_level(logging.ERROR, logger="anki_miner.services.validation_service"),
        ):
            ok, msg = svc._check_tool("ffmpeg", "ffmpeg")

        assert not ok
        assert any(r.exc_info is not None for r in caplog.records)

    def test_check_deck_exists_unexpected_exception_logs_traceback(self, caplog):
        """_check_deck_exists generic except logs exc_info."""
        from unittest.mock import patch

        svc = self._make_service()
        with (
            patch(
                "anki_miner.services.validation_service.post_action",
                side_effect=RuntimeError("unexpected"),
            ),
            caplog.at_level(logging.ERROR, logger="anki_miner.services.validation_service"),
        ):
            ok, msg = svc._check_deck_exists()

        assert not ok
        assert any(r.exc_info is not None for r in caplog.records)

    def test_check_note_type_exists_unexpected_exception_logs_traceback(self, caplog):
        """_check_note_type_exists generic except logs exc_info."""
        from unittest.mock import patch

        svc = self._make_service()
        with (
            patch(
                "anki_miner.services.validation_service.post_action",
                side_effect=RuntimeError("unexpected"),
            ),
            caplog.at_level(logging.ERROR, logger="anki_miner.services.validation_service"),
        ):
            ok, msg = svc._check_note_type_exists()

        assert not ok
        assert any(r.exc_info is not None for r in caplog.records)

    def test_check_field_names_exist_unexpected_exception_logs_traceback(self, caplog):
        """_check_field_names_exist generic except logs exc_info."""
        from unittest.mock import patch

        svc = self._make_service()
        with (
            patch(
                "anki_miner.services.validation_service.post_action",
                side_effect=RuntimeError("unexpected"),
            ),
            caplog.at_level(logging.ERROR, logger="anki_miner.services.validation_service"),
        ):
            ok, msg = svc._check_field_names_exist()

        assert not ok
        assert any(r.exc_info is not None for r in caplog.records)

    def test_validate_setup_temp_folder_unexpected_exception_logs_traceback(self, caplog):
        """validate_setup temp-folder generic except logs exc_info (never raises)."""
        from unittest.mock import patch

        svc = self._make_service()
        with (
            patch(
                "anki_miner.services.validation_service.ensure_directory",
                side_effect=PermissionError("no write"),
            ),
            caplog.at_level(logging.ERROR, logger="anki_miner.services.validation_service"),
        ):
            result = svc.validate_setup()

        # Never-raises contract: returns a ValidationResult, not an exception
        assert result is not None
        assert any(r.exc_info is not None for r in caplog.records)


# ---------------------------------------------------------------------------
# OVH-010 tail — main() uses config.log_path
# ---------------------------------------------------------------------------


class TestMainUsesConfigLogPath:
    """main() passes config.log_path (not the hardcoded default) to _configure_logging."""

    def test_main_passes_config_log_path_to_configure_logging(self, tmp_path):
        """_configure_logging is called with the path from GUIConfigManager.load_config()."""
        from unittest.mock import MagicMock, patch

        custom_log = tmp_path / "custom" / "app.log"

        mock_config = MagicMock()
        mock_config.log_path = custom_log

        captured: list[Path] = []

        def fake_configure_logging(path: Path) -> None:
            captured.append(path)

        with (
            patch(
                "anki_miner.gui.app.GUIConfigManager.load_config",
                return_value=mock_config,
            ),
            patch("anki_miner.gui.app._configure_logging", side_effect=fake_configure_logging),
            patch("anki_miner.gui.app.QApplication"),
            patch("anki_miner.gui.app.MainWindow"),
            patch("anki_miner.gui.app.GUIPresenter"),
            patch("anki_miner.gui.app.GUIProgressCallback"),
            patch("anki_miner.gui.app.StatsService"),
            patch("anki_miner.gui.app.Theme"),
            patch("anki_miner.gui.app.SingleEpisodeTab"),
            patch("anki_miner.gui.app.BatchProcessingTab"),
            patch("anki_miner.gui.app.DeckBuilderTab"),
            patch("anki_miner.gui.app.YouTubeTab"),
            patch("anki_miner.gui.app.AudiobookTab"),
            patch("anki_miner.gui.app.AnalyticsTab"),
            patch("anki_miner.gui.app.SettingsTab"),
            patch("anki_miner.gui.app.create_youtube_fetcher"),
            patch("anki_miner.gui.app._connect_settings_validation"),
            patch("sys.exit"),
        ):
            try:
                from anki_miner.gui import app as app_module

                app_module.main()
            except Exception:
                pass  # We only care that _configure_logging was called

        # main() now configures the default path first (so config-load warnings
        # are captured, F3), then re-points to config.log_path. The custom path
        # must be the one in effect after main() (the last call).
        assert len(captured) >= 1, "_configure_logging was never called"
        assert captured[-1] == custom_log, f"Expected final _configure_logging({custom_log!r}), got {captured[-1]!r}"
