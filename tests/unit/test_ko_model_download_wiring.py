"""The in-app Korean model download, end to end through the GUI seam.

The bundle ships the kiwipiepy engine without its ~88 MB model, so the pack has
to be reachable from the UI or a bundled user can never mine Korean: the model is
also what the availability probe gates on, so ko is absent from the mining-language
selector until the download lands. The row therefore lives beside that selector
(Settings -> Mining Language), and the plumbing mirrors the CUDA pack:
panel signal -> SettingsTab -> app wiring -> BackgroundTaskController -> InstallWorker.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.panels.mining_language_settings_panel import MiningLanguageSettingsPanel
from anki_miner.services import language_pack_installer
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.language_pack_installer.install_language_pack"


def _install_fake_pack(root) -> None:
    pack = language_pack_installer.load_pack("ko")
    comp = next(c for c in pack.components if c.import_name == "kiwipiepy_model")
    model = root / comp.import_name
    model.mkdir(parents=True, exist_ok=True)
    for name in comp.sentinels:
        (model / name).write_bytes(b"x")


class TestInstallTask:
    def test_success_emits_result_true(self, qapp, tmp_path, monkeypatch) -> None:
        from anki_miner.gui.workers.install_worker import InstallWorker, ko_model_task

        monkeypatch.setattr(_INSTALL, lambda code, root, progress=None, cancelled_check=None: root)
        worker = InstallWorker(ko_model_task(tmp_path))
        results: list[tuple] = []
        worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

        _run_worker_sync(worker)

        assert len(results) == 1
        ok, msg = results[0]
        assert ok is True
        assert isinstance(msg, str)

    def test_the_task_threads_the_cancel_check_and_progress(self, qapp, tmp_path, monkeypatch) -> None:
        from anki_miner.gui.workers.install_worker import InstallWorker, ko_model_task

        seen: dict = {}

        def _install(code, root, progress=None, cancelled_check=None):
            seen["code"] = code
            seen["root"] = root
            seen["cancelled_check"] = cancelled_check
            progress(50, 100, "KO pack (1/1): downloading")
            return root

        monkeypatch.setattr(_INSTALL, _install)
        worker = InstallWorker(ko_model_task(tmp_path))
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

    def test_a_failure_reports_the_reason(self, qapp, tmp_path, monkeypatch) -> None:
        from anki_miner.exceptions import SetupError
        from anki_miner.gui.workers.install_worker import InstallWorker, ko_model_task

        def _boom(code, root, progress=None, cancelled_check=None):
            raise SetupError("Korean model download checksum mismatch")

        monkeypatch.setattr(_INSTALL, _boom)
        worker = InstallWorker(ko_model_task(tmp_path))
        results: list[tuple] = []
        worker.result_ready.connect(lambda ok, msg: results.append((ok, msg)))

        _run_worker_sync(worker)

        assert results and results[0][0] is False
        assert "checksum mismatch" in results[0][1]


class TestControllerStarter:
    def test_the_starter_keeps_one_addressable_handle(self, qapp, tmp_path, monkeypatch, qtbot) -> None:
        from PyQt6.QtWidgets import QWidget

        from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

        parent = QWidget()
        qtbot.addWidget(parent)
        controller = BackgroundTaskController(parent)
        monkeypatch.setattr(_INSTALL, lambda code, root, progress=None, cancelled_check=None: root)

        assert controller.ko_model_download_worker is None
        controller.start_ko_model_download(tmp_path, lambda _text: None, lambda _ok, _msg: None)
        worker = controller.ko_model_download_worker
        assert worker is not None
        # A second press while the first is live must not spawn a rival worker.
        controller.start_ko_model_download(tmp_path, lambda _text: None, lambda _ok, _msg: None)
        assert controller.ko_model_download_worker is worker
        worker.cancel()
        worker.wait(5000)


class TestPanelRow:
    def test_the_row_hides_where_the_engine_is_absent(self, monkeypatch, qtbot) -> None:
        # Without kiwipiepy the model alone buys nothing, so offering it is noise.
        import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

        monkeypatch.setattr(module, "kiwipiepy_installed", lambda: False)
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)

        assert not panel.download_ko_model_button.isVisibleTo(panel)

    def test_the_row_offers_the_download_when_the_model_is_missing(self, monkeypatch, tmp_path, qtbot) -> None:
        import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

        monkeypatch.setattr(module, "kiwipiepy_installed", lambda: True)
        monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)

        assert panel.download_ko_model_button.isVisibleTo(panel)
        assert panel.download_ko_model_button.isEnabled()
        assert panel.ko_model_status_label.text() == "Not installed"

    def test_an_installed_pack_reports_itself(self, monkeypatch, tmp_path, qtbot) -> None:
        import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

        monkeypatch.setattr(module, "kiwipiepy_installed", lambda: True)
        monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
        _install_fake_pack(language_pack_installer.language_pack_root("ko"))
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)

        assert panel.ko_model_status_label.text() == "Installed"
        assert not panel.download_ko_model_button.isEnabled()

    def test_pressing_it_asks_the_caller_to_download(self, monkeypatch, tmp_path, qtbot) -> None:
        import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

        monkeypatch.setattr(module, "kiwipiepy_installed", lambda: True)
        monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)

        with qtbot.waitSignal(panel.ko_model_download_requested, timeout=1000):
            panel.download_ko_model_button.click()

        assert not panel.download_ko_model_button.isEnabled()

    def test_finishing_refreshes_the_row_and_the_selector(self, monkeypatch, tmp_path, qtbot) -> None:
        # The availability probe gates on the model, so ko is missing from the
        # selector until the pack lands — the finish hook has to repopulate it or
        # the user downloads the model and still cannot pick Korean.
        import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

        monkeypatch.setattr(module, "kiwipiepy_installed", lambda: True)
        monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)
        panel.download_ko_model_button.click()
        _install_fake_pack(language_pack_installer.language_pack_root("ko"))

        with qtbot.assertNotEmitted(panel.mining_language_requested, wait=10):
            panel.notify_ko_model_download_finished()

        assert panel.ko_model_status_label.text() == "Installed"
        codes = [panel.mining_language_combo.itemData(i) for i in range(panel.mining_language_combo.count())]
        assert "ko" in codes


class TestSettingsTabForwarding:
    def test_the_tab_re_emits_and_forwards_status(self, test_config, qtbot) -> None:
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        with qtbot.waitSignal(tab.ko_model_download_requested, timeout=1000):
            tab.mining_language_panel.ko_model_download_requested.emit()
        assert tab.mining_language_panel.ko_model_status_label.text() == "Downloading…"

        tab.set_ko_model_status("Installed")
        assert tab.mining_language_panel.ko_model_status_label.text() == "Installed"

    def test_the_panel_is_outside_the_save_path(self, test_config, qtbot) -> None:
        """It writes no field; arming the debounce would re-save pre-switch state."""
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        assert tab.mining_language_panel not in tab._save_panels


class TestAppWiring:
    def test_the_request_starts_the_download_at_the_pack_root(
        self, monkeypatch, patch_heavy_init, test_config, qtbot
    ) -> None:
        patch_heavy_init(test_config, stub_run_validation=False)
        from anki_miner.gui import app as app_module
        from anki_miner.gui.main_window import MainWindow
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        window = MainWindow()
        qtbot.addWidget(window)
        settings_tab = SettingsTab(window.get_config())
        qtbot.addWidget(settings_tab)

        captured: dict = {}

        def _fake_start(root, status_cb, on_finished):
            captured["root"] = root
            captured["on_finished"] = on_finished

        monkeypatch.setattr(window.background_tasks, "start_ko_model_download", _fake_start)
        app_module._connect_ko_model_download(window, settings_tab)

        settings_tab.ko_model_download_requested.emit()
        assert captured["root"] == language_pack_installer.language_pack_root("ko")

        calls: list = []
        monkeypatch.setattr(
            settings_tab.mining_language_panel,
            "notify_ko_model_download_finished",
            lambda: calls.append(True),
        )
        captured["on_finished"](True, "Korean model installed successfully.")

        assert settings_tab.mining_language_panel.ko_model_status_label.text() == "Korean model installed successfully."
        assert calls == [True]
        window.deleteLater()
