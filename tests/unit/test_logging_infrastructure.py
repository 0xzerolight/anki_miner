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
        handlers_before = list(root.handlers)
        try:
            configure_logging(log_path)
            assert log_path.parent.exists()
        finally:
            # Remove any handlers added by this test to avoid polluting others
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
        handlers_before = list(root.handlers)
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]
            assert any(isinstance(h, RotatingFileHandler) for h in added)
        finally:
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_log_record_written_to_file(self, tmp_path):
        """After _configure_logging, an ERROR record is written to the log file."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        handlers_before = list(root.handlers)
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
            for h in added:
                h.close()
                root.removeHandler(h)

    def test_root_logger_level_is_warning(self, tmp_path):
        """_configure_logging sets root logger to WARNING (not DEBUG)."""
        configure_logging = self._import_configure_logging()
        log_path = tmp_path / "app.log"

        root = logging.getLogger()
        handlers_before = list(root.handlers)
        root_level_before = root.level
        added: list[logging.Handler] = []
        try:
            configure_logging(log_path)
            added = [h for h in root.handlers if h not in handlers_before]
            assert root.level == logging.WARNING
        finally:
            root.setLevel(root_level_before)
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
