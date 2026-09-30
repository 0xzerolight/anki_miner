"""The in-app language-pack downloads, end to end through the GUI seam.

A frozen bundle cannot carry every mining language's engine and model -- the
Korean model alone is ~88 MB -- so each language that declares a pack has to be
downloadable from the UI or a bundled user can never mine it: the pack is also
what the availability probe gates on, so the language is absent from the mining-
language selector until the download lands. Since D12 the selector itself lists
such a language with "(download)" and offers "Download and switch" (Settings ->
Mining Language), and the plumbing mirrors the CUDA pack:
panel signal -> SettingsTab -> app wiring -> BackgroundTaskController -> InstallWorker.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.services import language_pack_installer
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.language_pack_installer.install_language_pack"


class TestInstallTask:
    def test_success_emits_result_true(self, qapp, tmp_path, monkeypatch) -> None:
        from anki_miner.gui.workers.install_worker import InstallWorker, language_pack_task

        monkeypatch.setattr(_INSTALL, lambda code, root, progress=None, cancelled_check=None: root)
        worker = InstallWorker(language_pack_task("ko", tmp_path, "한국어"))
        results: list[tuple] = []
        worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

        _run_worker_sync(worker)

        assert len(results) == 1
        ok, msg = results[0]
        assert ok is True
        assert "한국어" in msg

    def test_the_task_threads_the_cancel_check_and_progress(self, qapp, tmp_path, monkeypatch) -> None:
        from anki_miner.gui.workers.install_worker import InstallWorker, language_pack_task

        seen: dict = {}

        def _install(code, root, progress=None, cancelled_check=None):
            seen["code"] = code
            seen["root"] = root
            seen["cancelled_check"] = cancelled_check
            progress(50, 100, "KO pack (1/2): downloading")
            return root

        monkeypatch.setattr(_INSTALL, _install)
        worker = InstallWorker(language_pack_task("ko", tmp_path, "한국어"))
        statuses: list[str] = []
        worker.status.connect(statuses.append)

        _run_worker_sync(worker)

        assert seen["code"] == "ko"
        assert seen["root"] == tmp_path
        # The task hands the installer a live view of the worker's cancel flag,
        # not a snapshot taken before the run.
        assert seen["cancelled_check"]() is False
        worker.cancel()
        assert seen["cancelled_check"]() is True
        assert any("50" in text for text in statuses)

    def test_the_progress_line_names_the_language_not_the_code(self, qapp, tmp_path, monkeypatch) -> None:
        """The installer is GUI-free and labels with the code; "KO pack" is jargon."""
        from anki_miner.gui.workers.install_worker import InstallWorker, language_pack_task

        def _install(code, root, progress=None, cancelled_check=None):
            progress(1, 2, "KO pack (1/2): downloading")
            return root

        monkeypatch.setattr(_INSTALL, _install)
        worker = InstallWorker(language_pack_task("ko", tmp_path, "한국어"))
        statuses: list[str] = []
        worker.status.connect(statuses.append)

        _run_worker_sync(worker)

        progress_lines = [text for text in statuses if "(1/2)" in text]
        assert progress_lines
        assert all("한국어 pack (1/2)" in text for text in progress_lines)
        assert not any("KO pack" in text for text in progress_lines)

    def test_a_failure_reports_the_reason(self, qapp, tmp_path, monkeypatch) -> None:
        from anki_miner.exceptions import SetupError
        from anki_miner.gui.workers.install_worker import InstallWorker, language_pack_task

        def _boom(code, root, progress=None, cancelled_check=None):
            raise SetupError("kiwipiepy_model download checksum mismatch")

        monkeypatch.setattr(_INSTALL, _boom)
        worker = InstallWorker(language_pack_task("ko", tmp_path, "한국어"))
        results: list[tuple] = []
        worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

        _run_worker_sync(worker)

        assert results and results[0][0] is False
        assert "checksum mismatch" in results[0][1]


class _FakeInstallWorker(QObject):
    """Fake InstallWorker: status(str) + result_ready(bool, str) + native finished().

    Mirrors ``tests/unit/test_background_tasks.py``'s stand-in. No real QThread:
    scheduling one here reintroduces the xdist QThread flakiness the starter
    suites exist without.
    """

    status = pyqtSignal(str)
    result_ready = pyqtSignal(bool, str)
    finished = pyqtSignal()

    def __init__(self, task=None, parent=None) -> None:
        super().__init__(parent)
        self.task = task
        self._running = False
        self.deleteLater = MagicMock()  # type: ignore[method-assign]

    def isRunning(self) -> bool:  # noqa: N802
        return self._running

    def start(self) -> None:
        self._running = True

    def emit_finished(self) -> None:
        """Simulate thread exit (native QThread.finished, 0-arg)."""
        self._running = False
        self.finished.emit()


class TestControllerStarter:
    @pytest.fixture
    def controller(self, qapp, qtbot, monkeypatch):
        from PyQt6.QtWidgets import QWidget

        from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

        parent = QWidget()
        qtbot.addWidget(parent)
        built: list[_FakeInstallWorker] = []

        def _factory(task, parent=None):
            worker = _FakeInstallWorker(task, parent)
            built.append(worker)
            return worker

        monkeypatch.setattr("anki_miner.gui.workers.install_worker.InstallWorker", _factory)
        return BackgroundTaskController(parent), built

    def test_the_starter_keeps_one_handle_per_language(self, controller, tmp_path) -> None:
        tasks, built = controller
        assert tasks.language_pack_workers == {}

        tasks.start_language_pack_download("ko", tmp_path, lambda _t: None, lambda _ok, _m: None)
        # A second press while the first is live must not spawn a rival worker.
        tasks.start_language_pack_download("ko", tmp_path, lambda _t: None, lambda _ok, _m: None)

        assert tasks.language_pack_workers["ko"] is built[0]
        assert len(built) == 1

    def test_two_languages_download_side_by_side(self, controller, tmp_path) -> None:
        """The per-code key is the point: a ko download must not block zh."""
        tasks, built = controller
        tasks.start_language_pack_download("ko", tmp_path / "ko", lambda _t: None, lambda _ok, _m: None)
        tasks.start_language_pack_download("zh", tmp_path / "zh", lambda _t: None, lambda _ok, _m: None)

        assert [tasks.language_pack_workers["ko"], tasks.language_pack_workers["zh"]] == built
        assert len(built) == 2

    def test_status_and_result_reach_the_callers_slots(self, controller, tmp_path) -> None:
        tasks, built = controller
        statuses: list[str] = []
        results: list[tuple] = []
        tasks.start_language_pack_download("ko", tmp_path, statuses.append, lambda ok, msg: results.append((ok, msg)))

        built[0].status.emit("한국어 pack (1/2): downloading")
        built[0].result_ready.emit(True, "한국어 pack installed.")

        assert statuses == ["한국어 pack (1/2): downloading"]
        assert results == [(True, "한국어 pack installed.")]

    def test_the_handle_is_released_on_finished(self, controller, tmp_path) -> None:
        tasks, built = controller
        tasks.start_language_pack_download("ko", tmp_path, lambda _t: None, lambda _ok, _m: None)

        built[0].emit_finished()

        assert tasks.language_pack_workers["ko"] is None
        built[0].deleteLater.assert_called_once()
        # The row is downloadable again once the handle is free.
        tasks.start_language_pack_download("ko", tmp_path, lambda _t: None, lambda _ok, _m: None)
        assert tasks.language_pack_workers["ko"] is built[1]

    def test_shutdown_joins_the_dict_keyed_workers(self, controller, qtbot, tmp_path) -> None:
        """A missed join destroys a running QThread at close and aborts the process."""
        from PyQt6.QtWidgets import QTabWidget

        tasks, built = controller
        tasks.start_language_pack_download("zh", tmp_path, lambda _t: None, lambda _ok, _m: None)
        tabs = QTabWidget()
        qtbot.addWidget(tabs)
        joined: list = []
        tasks._join_worker_for_close = lambda worker, timeout_ms=0: (joined.append(worker), True)[1]

        tasks.shutdown(tabs)

        assert built[0] in joined


class TestSettingsTabForwarding:
    def test_the_tab_re_emits_the_code_and_forwards_status(self, test_config, qtbot, monkeypatch) -> None:
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        monkeypatch.setattr(
            "anki_miner.gui.utils.language_choices._pack_download_mb", lambda code: 50 if code == "ko" else None
        )
        monkeypatch.setattr("anki_miner.languages.ko.availability.module_importable", lambda _name: False)
        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)
        assert tab.mining_language_panel.propose_download("ko")

        with qtbot.waitSignal(tab.language_pack_download_requested, timeout=1000) as blocker:
            tab.mining_language_panel.language_pack_download_requested.emit("ko")
        assert blocker.args == ["ko"]
        assert tab.mining_language_panel.pending_download_status.text() == "Downloading…"

        tab.set_language_pack_status("ko", "Installed")
        assert tab.mining_language_panel.pending_download_status.text() == "Installed"

    def test_the_panel_joins_the_save_path_but_its_own_combo_stays_unwired(self, test_config, qtbot) -> None:
        """T10: the variant combos write ``script_variant``, so the panel now
        takes part in the Save round-trip. Its own ``mining_language_combo``
        still proposes a guarded switch and must never arm the auto-save
        debounce -- that would re-save the pre-switch panel state on top of
        the switch's own commit.
        """
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        assert tab.mining_language_panel in tab._save_panels

        assert tab._settings_dirty is False
        tab.mining_language_panel.mining_language_combo.setCurrentIndex(1)
        assert tab._settings_dirty is False


class TestAppWiring:
    @pytest.fixture
    def wired(self, monkeypatch, patch_heavy_init, test_config, qtbot):
        patch_heavy_init(test_config, stub_run_validation=False)
        from anki_miner.gui import app as app_module
        from anki_miner.gui.main_window import MainWindow
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        window = MainWindow()
        qtbot.addWidget(window)
        monkeypatch.setattr(
            "anki_miner.gui.utils.language_choices._pack_download_mb", lambda code: 50 if code == "ko" else None
        )
        monkeypatch.setattr("anki_miner.languages.ko.availability.module_importable", lambda _name: False)
        settings_tab = SettingsTab(window.get_config())
        qtbot.addWidget(settings_tab)
        settings_tab.mining_language_panel.propose_download("ko")

        captured: dict = {}

        def _fake_start(code, root, status_cb, on_finished):
            captured["code"] = code
            captured["root"] = root
            captured["status_cb"] = status_cb
            captured["on_finished"] = on_finished

        monkeypatch.setattr(window.background_tasks, "start_language_pack_download", _fake_start)
        app_module._connect_language_pack_download(window, settings_tab)
        yield window, settings_tab, captured
        window.deleteLater()

    def test_the_request_starts_the_download_at_that_languages_pack_root(self, wired) -> None:
        _window, settings_tab, captured = wired

        settings_tab.language_pack_download_requested.emit("zh")

        assert captured["code"] == "zh"
        assert captured["root"] == language_pack_installer.language_pack_root("zh")

    def test_the_finish_injects_the_syspath_before_the_panel_re_probes(self, wired, monkeypatch) -> None:
        """Repopulating the selector re-runs ``find_spec``; injection has to be first.

        Reversed, the panel probes a pack that is on disk but not yet importable
        and drops the language it just downloaded from the combo.
        """
        from anki_miner.gui import app as app_module

        _window, settings_tab, captured = wired
        settings_tab.language_pack_download_requested.emit("ko")
        order: list[str] = []
        monkeypatch.setattr(app_module, "ensure_language_packs_on_syspath", lambda: order.append("inject"))
        monkeypatch.setattr(
            settings_tab,
            "notify_language_pack_download_finished",
            lambda code: order.append(f"notify:{code}"),
        )

        captured["on_finished"](True, "한국어 pack installed.")

        assert order == ["inject", "notify:ko"]
        assert settings_tab.mining_language_panel.pending_download_status.text() == "한국어 pack installed."

    def test_the_finish_rebuilds_the_search_index(self, wired, monkeypatch) -> None:
        """The row is hidden at index time and revealed by the download, so the
        index built during construction still calls it invisible."""
        _window, settings_tab, captured = wired
        settings_tab.language_pack_download_requested.emit("ko")
        rebuilt: list[bool] = []
        monkeypatch.setattr(settings_tab, "refresh_setting_search_index", lambda: rebuilt.append(True))

        captured["on_finished"](True, "한국어 pack installed.")

        assert rebuilt == [True]

    def test_status_lines_reach_the_row_that_asked(self, wired) -> None:
        _window, settings_tab, captured = wired
        settings_tab.language_pack_download_requested.emit("ko")

        captured["status_cb"]("한국어 pack (1/2): downloading (10%)")

        assert (
            settings_tab.mining_language_panel.pending_download_status.text() == "한국어 pack (1/2): downloading (10%)"
        )
