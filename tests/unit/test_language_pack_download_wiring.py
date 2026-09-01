"""The in-app language-pack downloads, end to end through the GUI seam.

A frozen bundle cannot carry every mining language's engine and model -- the
Korean model alone is ~88 MB -- so each language that declares a pack has to be
downloadable from the UI or a bundled user can never mine it: the pack is also
what the availability probe gates on, so the language is absent from the mining-
language selector until the download lands. The rows therefore live beside that
selector (Settings -> Mining Language), and the plumbing mirrors the CUDA pack:
panel signal -> SettingsTab -> app wiring -> BackgroundTaskController -> InstallWorker.

One row per language, built from the manifests rather than hand-written per
language: the ko-shaped bespoke row this replaced would have been copied for zh
and again for every language after it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.gui.widgets.panels.mining_language_settings_panel import MiningLanguageSettingsPanel
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent
from anki_miner.services import language_pack_installer
from tests.unit._worker_sync import _run_worker_sync

_INSTALL = "anki_miner.services.language_pack_installer.install_language_pack"

#: A pack for a language that exists nowhere else. Its import names can never be
#: importable, so the row state is decided by the disk tier alone -- this venv
#: has kiwipiepy and jieba installed, and a real manifest would report itself
#: satisfied by ``find_spec`` before any pack directory is consulted.
_SYNTHETIC = LanguagePack(
    code="xx",
    approx_download_mb=42,
    components=(
        PackComponent(
            import_name="xxpkg",
            required=True,
            sentinels=("__init__.py",),
            universal=ArtifactSpec(
                url="https://example.invalid/xxpkg.whl",
                sha256="0" * 64,
                kind="wheel",
                member_prefix="xxpkg/",
            ),
        ),
    ),
)

#: The same pack with no artifact for any platform this test can run on.
_UNSUPPORTED = LanguagePack(
    code="xx",
    approx_download_mb=42,
    components=(
        PackComponent(
            import_name="xxpkg",
            required=True,
            sentinels=("__init__.py",),
            per_platform={("noplat", "noarch"): _SYNTHETIC.components[0].universal},
        ),
    ),
)


def _synthetic_panel(qtbot, monkeypatch, tmp_path, *, pack=_SYNTHETIC, importable=False):
    """A panel carrying one synthetic-language row, with a home under *tmp_path*."""
    import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

    monkeypatch.setattr(module, "AVAILABLE_LANGUAGES", ("ja", "xx"))
    monkeypatch.setattr(module, "pack_already_importable", lambda _pack: importable)
    monkeypatch.setattr(language_pack_installer, "load_pack", lambda code: pack if code == "xx" else None)
    monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
    panel = MiningLanguageSettingsPanel()
    qtbot.addWidget(panel)
    return panel


def _seed_pack(code: str, pack: LanguagePack) -> None:
    """Put a complete extraction of every component in *code*'s pack on disk."""
    root = language_pack_installer.language_pack_root(code)
    for comp in pack.components:
        package = root / comp.import_name
        package.mkdir(parents=True, exist_ok=True)
        for name in comp.sentinels:
            (package / name).write_bytes(b"x")
        spec = comp.universal or next(iter((comp.per_platform or {}).values()), None)
        for prefix in () if spec is None else spec.root_members:
            (root / f"{prefix}so").write_bytes(b"x")


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
        built[0].result_ready.emit(True, "한국어 pack installed successfully.")

        assert statuses == ["한국어 pack (1/2): downloading"]
        assert results == [(True, "한국어 pack installed successfully.")]

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


class TestPanelRows:
    def test_a_language_with_no_pack_grows_no_row(self, qtbot) -> None:
        """ja's engine is bundled; a row offering nothing to download is noise."""
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)

        assert "ja" not in panel.language_pack_rows
        assert "ko" in panel.language_pack_rows

    def test_the_row_is_named_for_the_language_it_unlocks(self, qtbot) -> None:
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)

        assert "한국어" in panel.language_pack_rows["ko"].button.text()
        assert "中文" in panel.language_pack_rows["zh"].button.text()

    def test_the_row_offers_the_download_with_its_size(self, qtbot, monkeypatch, tmp_path) -> None:
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path)
        row = panel.language_pack_rows["xx"]

        assert row.button.isVisibleTo(panel)
        assert row.button.isEnabled()
        assert row.status_label.text() == "Not installed - about 42 MB download"

    def test_a_satisfied_language_with_nothing_on_disk_hides_its_row(self, qtbot, monkeypatch, tmp_path) -> None:
        """A pip install with the language's extra needs no pack and must see no row."""
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path, importable=True)

        assert not panel.language_pack_rows["xx"].button.isVisibleTo(panel)

    def test_an_unsupported_platform_hides_the_row(self, qtbot, monkeypatch, tmp_path) -> None:
        """No artifact resolves here, so the button could only ever fail."""
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path, pack=_UNSUPPORTED)

        assert not panel.language_pack_rows["xx"].button.isVisibleTo(panel)

    def test_an_installed_pack_reports_itself(self, qtbot, monkeypatch, tmp_path) -> None:
        """Installed has to be reachable: the row must not vanish once a pack lands."""
        import anki_miner.gui.widgets.panels.mining_language_settings_panel as module

        monkeypatch.setattr(module, "AVAILABLE_LANGUAGES", ("ja", "xx"))
        monkeypatch.setattr(language_pack_installer, "load_pack", lambda code: _SYNTHETIC if code == "xx" else None)
        monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
        _seed_pack("xx", _SYNTHETIC)
        # What a finished download leaves behind: the packages are importable
        # (the pack root is on sys.path) AND the pack is on disk.
        monkeypatch.setattr(module, "pack_already_importable", lambda _pack: True)
        panel = MiningLanguageSettingsPanel()
        qtbot.addWidget(panel)
        row = panel.language_pack_rows["xx"]

        assert row.button.isVisibleTo(panel)
        assert row.status_label.text() == "Installed"
        assert not row.button.isEnabled()

    def test_pressing_it_asks_the_caller_to_download_that_language(self, qtbot, monkeypatch, tmp_path) -> None:
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path)
        requested: list[str] = []
        panel.language_pack_download_requested.connect(requested.append)

        panel.language_pack_rows["xx"].button.click()
        # The per-code in-flight guard: a second press changes nothing.
        panel.language_pack_rows["xx"].button.click()

        assert requested == ["xx"]
        assert not panel.language_pack_rows["xx"].button.isEnabled()

    def test_status_lines_land_on_the_row_that_asked(self, qtbot, monkeypatch, tmp_path) -> None:
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path)

        panel.set_language_pack_status("xx", "xx pack (1/1): downloading (50%)")

        assert panel.language_pack_rows["xx"].status_label.text() == "xx pack (1/1): downloading (50%)"

    def test_a_status_for_a_language_with_no_row_is_ignored(self, qtbot, monkeypatch, tmp_path) -> None:
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path)

        panel.set_language_pack_status("ja", "Downloading…")  # must not raise

    def test_finishing_refreshes_the_row_and_the_selector(self, qtbot, monkeypatch, tmp_path) -> None:
        # The availability probe gates on the pack, so the language is missing
        # from the selector until it lands -- the finish hook has to repopulate
        # it or the user downloads the pack and still cannot pick the language.
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path)
        panel.language_pack_rows["xx"].button.click()
        _seed_pack("xx", _SYNTHETIC)

        with qtbot.assertNotEmitted(panel.mining_language_requested, wait=10):
            panel.notify_language_pack_download_finished("xx")

        assert panel.language_pack_rows["xx"].status_label.text() == "Installed"
        assert not panel.language_pack_rows["xx"].button.isEnabled()

    def test_finishing_repopulates_the_language_selector(self, qtbot, monkeypatch, tmp_path) -> None:
        panel = _synthetic_panel(qtbot, monkeypatch, tmp_path)
        panel.set_mining_language("zh")
        repopulated: list[bool] = []
        monkeypatch.setattr(panel, "_repopulate_mining_languages", lambda: repopulated.append(True))

        panel.notify_language_pack_download_finished("xx")

        assert repopulated == [True]


class TestSettingsTabForwarding:
    def test_the_tab_re_emits_the_code_and_forwards_status(self, test_config, qtbot) -> None:
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        with qtbot.waitSignal(tab.language_pack_download_requested, timeout=1000) as blocker:
            tab.mining_language_panel.language_pack_download_requested.emit("ko")
        assert blocker.args == ["ko"]
        assert tab.mining_language_panel.language_pack_rows["ko"].status_label.text() == "Downloading…"

        tab.set_language_pack_status("ko", "Installed")
        assert tab.mining_language_panel.language_pack_rows["ko"].status_label.text() == "Installed"

    def test_the_panel_is_outside_the_save_path(self, test_config, qtbot) -> None:
        """It writes no field; arming the debounce would re-save pre-switch state."""
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        tab = SettingsTab(test_config)
        qtbot.addWidget(tab)

        assert tab.mining_language_panel not in tab._save_panels


class TestAppWiring:
    @pytest.fixture
    def wired(self, monkeypatch, patch_heavy_init, test_config, qtbot):
        patch_heavy_init(test_config, stub_run_validation=False)
        from anki_miner.gui import app as app_module
        from anki_miner.gui.main_window import MainWindow
        from anki_miner.gui.widgets.settings_tab import SettingsTab

        window = MainWindow()
        qtbot.addWidget(window)
        settings_tab = SettingsTab(window.get_config())
        qtbot.addWidget(settings_tab)

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

        captured["on_finished"](True, "한국어 pack installed successfully.")

        assert order == ["inject", "notify:ko"]
        assert (
            settings_tab.mining_language_panel.language_pack_rows["ko"].status_label.text()
            == "한국어 pack installed successfully."
        )

    def test_the_finish_makes_the_revealed_row_searchable(self, wired, monkeypatch) -> None:
        """The row is hidden at index time and revealed by the download, so the
        index built during construction still calls it invisible."""
        _window, settings_tab, captured = wired
        settings_tab.language_pack_download_requested.emit("ko")
        rebuilt: list[bool] = []
        monkeypatch.setattr(settings_tab, "refresh_setting_search_index", lambda: rebuilt.append(True))

        captured["on_finished"](True, "한국어 pack installed successfully.")

        assert rebuilt == [True]

    def test_status_lines_reach_the_row_that_asked(self, wired) -> None:
        _window, settings_tab, captured = wired
        settings_tab.language_pack_download_requested.emit("ko")

        captured["status_cb"]("한국어 pack (1/2): downloading (10%)")

        assert (
            settings_tab.mining_language_panel.language_pack_rows["ko"].status_label.text()
            == "한국어 pack (1/2): downloading (10%)"
        )
