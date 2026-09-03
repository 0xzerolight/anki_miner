"""Process-wide failure hooks: threads, unraisables, warnings and Qt.

Four whole classes of failure never reached ``anki_miner.log`` before these
hooks existed. A ``threading.Thread`` that raises prints to a stderr no frozen
build has; ``__del__`` failures vanish into ``sys.unraisablehook``;
``DeprecationWarning`` from a library goes to the warnings machinery; and Qt's
own platform, GL and paint diagnostics go to ``qDebug``'s default handler,
which in a bundle writes nowhere at all. Each test below pins one of them
landing on a logger instead.
"""

from __future__ import annotations

import logging
import threading
import warnings

import pytest

pytest.importorskip("PyQt6.QtCore")

from PyQt6.QtCore import qWarning  # noqa: E402

from anki_miner.gui import qt_log_bridge  # noqa: E402
from anki_miner.utils import log_hooks  # noqa: E402


@pytest.fixture
def process_hooks():
    """Install the process hooks for one test and take them back out again."""
    log_hooks.install_process_log_hooks()
    try:
        yield log_hooks
    finally:
        log_hooks.uninstall_process_log_hooks()


@pytest.fixture
def qt_bridge():
    """Install the Qt message bridge for one test and restore Qt's handler."""
    qt_log_bridge.install_qt_message_handler()
    try:
        yield qt_log_bridge
    finally:
        qt_log_bridge.uninstall_qt_message_handler()


class TestThreadExceptions:
    def test_thread_failure_is_logged_with_its_thread_name(self, process_hooks, caplog):
        def _boom() -> None:
            1 / 0  # noqa: B018

        with caplog.at_level(logging.CRITICAL, logger=log_hooks.logger.name):
            thread = threading.Thread(target=_boom, name="probe-thread")
            thread.start()
            thread.join(5)

        records = [r for r in caplog.records if "Unhandled exception in thread" in r.getMessage()]
        assert len(records) == 1
        message = records[0].getMessage()
        assert "Unhandled exception in thread probe-thread: ZeroDivisionError:" in message
        assert records[0].levelno == logging.CRITICAL
        assert records[0].exc_info is not None

    def test_install_is_idempotent(self, process_hooks):
        first = threading.excepthook
        log_hooks.install_process_log_hooks()
        assert threading.excepthook is first

    def test_uninstall_restores_the_previous_hooks(self):
        import sys

        before_thread = threading.excepthook
        before_unraisable = sys.unraisablehook
        log_hooks.install_process_log_hooks()
        assert threading.excepthook is not before_thread
        log_hooks.uninstall_process_log_hooks()
        assert threading.excepthook is before_thread
        assert sys.unraisablehook is before_unraisable


class TestUnraisable:
    def test_unraisable_failure_is_logged(self, process_hooks, caplog):
        import sys

        class _Broken:
            def __repr__(self) -> str:
                return "<broken object>"

        try:
            raise ValueError("del failed")
        except ValueError as exc:
            unraisable = type(
                "Unraisable",
                (),
                {
                    "exc_type": ValueError,
                    "exc_value": exc,
                    "exc_traceback": exc.__traceback__,
                    "err_msg": None,
                    "object": _Broken(),
                },
            )()
            with caplog.at_level(logging.ERROR, logger=log_hooks.logger.name):
                sys.unraisablehook(unraisable)

        records = [r for r in caplog.records if "Unraisable exception" in r.getMessage()]
        assert len(records) == 1
        assert "ValueError: del failed" in records[0].getMessage()
        assert "<broken object>" in records[0].getMessage()


class TestWarningsCapture:
    def test_warnings_reach_the_py_warnings_logger(self, process_hooks, caplog):
        with caplog.at_level(logging.WARNING, logger="py.warnings"), warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("probe deprecation", DeprecationWarning, stacklevel=1)

        assert any("probe deprecation" in r.getMessage() for r in caplog.records)


class TestQtMessageBridge:
    def test_qt_warning_lands_on_the_qt_logger(self, qt_bridge, caplog):
        with caplog.at_level(logging.WARNING, logger="anki_miner.qt"):
            qWarning(b"probe qt warning")

        records = [r for r in caplog.records if r.name == "anki_miner.qt"]
        assert len(records) == 1
        assert "probe qt warning" in records[0].getMessage()
        assert records[0].levelno == logging.WARNING

    def test_repeated_identical_messages_collapse_to_one_record(self, qt_bridge, caplog):
        with caplog.at_level(logging.WARNING, logger="anki_miner.qt"):
            for _ in range(50):
                qWarning(b"identical qt warning")
            during = [r for r in caplog.records if r.name == "anki_miner.qt"]
            assert len(during) == 1, "the 49 repeats must not each get a record"

            qt_log_bridge.flush_qt_repeats()

        records = [r.getMessage() for r in caplog.records if r.name == "anki_miner.qt"]
        assert len(records) == 2
        assert "identical qt warning" in records[0]
        assert "(repeated 49 more times)" in records[1]

    def test_install_is_idempotent(self, qt_bridge, caplog):
        qt_log_bridge.install_qt_message_handler()
        with caplog.at_level(logging.WARNING, logger="anki_miner.qt"):
            qWarning(b"single install")
        assert len([r for r in caplog.records if r.name == "anki_miner.qt"]) == 1
