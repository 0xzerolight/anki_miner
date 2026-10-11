"""Tests for MokuroTab — availability guard, folder scan → worker, options, cancel, close."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets import mokuro_tab as mokuro_tab_module
from anki_miner.gui.widgets.mokuro_tab import MokuroTab
from anki_miner.services.mokuro_installer import MangaOcrState
from anki_miner.services.mokuro_volumes import MokuroVolume
from anki_miner.services.ocr.recognizers import RECOGNIZERS
from tests.unit._ocr_profiles import with_recognizer

_AVAILABLE = "anki_miner.gui.widgets.mokuro_tab.MokuroTab._mokuro_ready"
_COMPUTE_AVAILABLE = "anki_miner.gui.widgets.mokuro_tab.MokuroTab._compute_mokuro_available"
_STATE = "anki_miner.gui.widgets.mokuro_tab.manga_ocr_state"
_OS_ACCESS = "anki_miner.gui.widgets.mokuro_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.mokuro_tab.MokuroWorker"
_RUN_OFF_THREAD = "anki_miner.gui.widgets.mokuro_tab.run_off_thread"
_INSTALL_SUPPORTED = "anki_miner.gui.widgets.mokuro_tab.mokuro_installer.mokuro_install_supported"


class _FakeWorker:
    def __init__(self) -> None:
        for name in (
            "file_started",
            "file_progress",
            "file_finished",
            "file_skipped",
            "queue_finished",
            "error",
            "finished",
        ):
            setattr(self, name, MagicMock())
        self.deleteLater = MagicMock()
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def isRunning(self) -> bool:
        return self.started and not self.cancelled

    def wait(self, *_a) -> bool:
        return True


def _config(tmp_path) -> AnkiMinerConfig:
    return AnkiMinerConfig(media_temp_folder=tmp_path / "tmp", uv_root=tmp_path / "uv")


def _make_tab(config, qtbot) -> MokuroTab:
    with patch(_COMPUTE_AVAILABLE, return_value=True):
        tab = MokuroTab(config)
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.run_button.isEnabled, timeout=3000)
    return tab


def _series(tmp_path) -> Path:
    for n in ("vol1", "vol2"):
        (tmp_path / "series" / n).mkdir(parents=True)
        (tmp_path / "series" / n / "p.jpg").write_bytes(b"x")
    return tmp_path / "series"


def _run(tab, qtbot, fake):
    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake) as cls,
    ):
        tab.run_button.click()
        qtbot.waitUntil(lambda: fake.started, timeout=3000)
    return cls


class TestConstruction:
    def test_ids(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        assert tab.TASK_ID == "tools.mokuro"
        assert tab.TASK_OWNER.main_tab == "subtitles" and tab.TASK_OWNER.subtab == "mokuro"
        assert tab.OUTPUT_HISTORY_KEY == ""

    def test_unavailable_disables_run_and_shows_notice(self, qtbot, tmp_path):
        with patch(_COMPUTE_AVAILABLE, return_value=False):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
        assert not tab.run_button.isEnabled()

    def test_suppressed_startup_skips_probe(self, qtbot, tmp_path):
        tab = MokuroTab(_config(tmp_path), suppress_optional_startup=True)
        qtbot.addWidget(tab)
        assert tab._availability_worker is None

    def test_options_seed_from_config(self, qtbot, tmp_path):
        import dataclasses

        tab = _make_tab(dataclasses.replace(_config(tmp_path), mokuro_use_gpu=False), qtbot)
        assert tab.gpu_checkbox.isChecked() is False
        assert tab.redo_checkbox.isChecked() is False


class TestSetupCard:
    """The Manga OCR setup card: in-app Install, one line once mokuro is found."""

    def test_install_button_visible_and_enabled_when_mokuro_unresolved(self, qtbot, tmp_path):
        """Waits for the (mokuro-unresolved) probe to actually land, then checks
        the button a user would see — not just its construction-time default."""
        with patch(_COMPUTE_AVAILABLE, return_value=False), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: tab.mokuro_status_label.text() == "Not installed", timeout=3000)
        assert not tab.setup_row.isHidden()
        assert tab.installed_row.isHidden()
        assert not tab.install_mokuro_button.isHidden()
        assert tab.install_mokuro_button.isEnabled()
        assert tab.install_mokuro_button.text() == "Install mokuro"

    def test_install_button_click_emits_request_and_disables(self, qtbot, tmp_path):
        with patch(_COMPUTE_AVAILABLE, return_value=False), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
        fired: list = []
        tab.mokuro_install_requested.connect(lambda: fired.append(True))
        assert tab.install_mokuro_button.isEnabled()  # genuinely enabled, not forced
        tab.install_mokuro_button.click()
        assert fired and not tab.install_mokuro_button.isEnabled()

    def test_failed_install_reenables_button_and_keeps_the_failure_message(self, qtbot, tmp_path):
        with patch(_COMPUTE_AVAILABLE, return_value=False), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
        tab.install_mokuro_button.click()
        assert tab._mokuro_install_active
        # What the app.py wiring's set_status writes on a failed install,
        # BEFORE notify_install_finished(False) runs.
        tab.set_mokuro_status("boom")

        tab.notify_install_finished(False)

        assert not tab._mokuro_install_active
        assert tab.install_mokuro_button.isEnabled()
        assert tab.mokuro_status_label.text() == "boom"

    def test_install_disabled_while_running_then_reenabled_when_finished(self, qtbot, tmp_path):
        """Install/Reinstall must not race a live OCR run: it disables the
        moment a run starts (still_running(worker_thread)) and re-enables only
        once _on_worker_finished confirms the QThread actually exited."""
        with patch(_INSTALL_SUPPORTED, return_value=True):
            tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)

        assert not tab.install_mokuro_button.isEnabled()

        tab._on_worker_finished()

        assert tab.install_mokuro_button.isEnabled()

    def test_installed_mokuro_collapses_the_card_to_one_line(self, qtbot, tmp_path):
        """E08: once found, the setup card is "mokuro is installed · Change…"."""
        with patch(_COMPUTE_AVAILABLE, return_value=True), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: not tab.installed_row.isHidden(), timeout=3000)

        assert tab.setup_row.isHidden()

        tab.change_setup_button.click()

        assert not tab.setup_row.isHidden()
        assert tab.install_mokuro_button.text() == "Reinstall mokuro"

    def _installed_tab(self, qtbot, tmp_path) -> MokuroTab:
        tab = MokuroTab(_config(tmp_path))
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(lambda: not tab.installed_row.isHidden(), timeout=3000)
        return tab

    def test_a_successful_reinstall_collapses_the_card_again(self, qtbot, tmp_path):
        """B4.4: Change… then a good Reinstall returns to the one-line card."""
        with patch(_COMPUTE_AVAILABLE, return_value=True), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = self._installed_tab(qtbot, tmp_path)
            tab.change_setup_button.click()
            tab.install_mokuro_button.click()
            tab.notify_install_finished(True)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: not tab.installed_row.isHidden(), timeout=3000)

        assert tab.setup_row.isHidden()

    def test_a_failed_reinstall_keeps_the_card_open(self, qtbot, tmp_path):
        """A failed Reinstall leaves Install on screen to retry."""
        with patch(_COMPUTE_AVAILABLE, return_value=True), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = self._installed_tab(qtbot, tmp_path)
            tab.change_setup_button.click()
            tab.install_mokuro_button.click()
            tab.notify_install_finished(False)

        assert not tab.setup_row.isHidden()
        assert tab.installed_row.isHidden()

    def test_there_is_no_path_override(self, qtbot, tmp_path):
        """D15 item 3: a self-built mokuro is set in gui_config.json (mokuro_location)."""
        tab = _make_tab(_config(tmp_path), qtbot)

        assert not hasattr(tab, "mokuro_selector")
        assert not hasattr(tab, "flush_pending_edits")

    def test_the_status_is_not_success_green(self, qtbot, tmp_path):
        """E08 + overlap 19: "Not installed" is the one neutral colour, "Installed" the success one."""
        tab = _make_tab(_config(tmp_path), qtbot)

        tab.set_mokuro_status("Not installed")
        assert tab.mokuro_status_label.objectName() == "validation-status"
        assert tab.mokuro_status_label.property("status") == "info"

        tab.set_mokuro_status("Installed")
        assert tab.mokuro_status_label.property("status") == "success"


_JA_NOTICE = "mokuro is not installed. Install it in the Manga OCR setup section below."
_MODEL_MISSING = "This language needs its OCR model."
_OWN_MOKURO = "This language needs Anki Miner's own mokuro. Install it below."


class TestLanguageStates:
    """The setup card and notice read the mining language's MangaOcrState (Review Focus 1, 2)."""

    @staticmethod
    def _tab(qtbot, tmp_path, monkeypatch, state, *, language: str = "ko") -> MokuroTab:
        if language != "ja":
            with_recognizer(monkeypatch, mokuro_tab_module, language, "korean")
        config = replace(_config(tmp_path), language=language)
        with patch(_STATE, return_value=state), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = MokuroTab(config)
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: tab.mokuro_status_label.text() != "", timeout=3000)
        return tab

    @staticmethod
    def _emitted(tab: MokuroTab) -> list[bool]:
        fired: list[bool] = []
        tab.mokuro_install_requested.connect(fired.append)
        tab.install_mokuro_button.click()
        return fired

    def test_japanese_missing_reads_as_today(self, qtbot, tmp_path, monkeypatch):
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(False, False), language="ja")

        assert tab.engine_notice_label.text() == _JA_NOTICE
        assert not tab.engine_notice_label.isHidden()
        assert tab.install_mokuro_button.text() == "Install mokuro"
        assert tab.mokuro_status_label.text() == "Not installed"
        assert not tab.run_button.isEnabled()
        assert self._emitted(tab) == [False]

    def test_japanese_found_reads_as_today(self, qtbot, tmp_path, monkeypatch):
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(True, True), language="ja")

        assert not tab.installed_row.isHidden() and tab.setup_row.isHidden()
        assert tab.engine_notice_label.isHidden()
        assert tab.run_button.isEnabled()
        tab.change_setup_button.click()
        assert tab.install_mokuro_button.text() == "Reinstall mokuro"
        assert self._emitted(tab) == [True]

    def test_a_managed_mokuro_without_the_language_parts_offers_the_model(self, qtbot, tmp_path, monkeypatch):
        """Review Focus 1: a managed (Japanese) venv in ko downloads only the model, never torch."""
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(False, True))

        assert not tab.setup_row.isHidden() and tab.installed_row.isHidden()
        assert tab.install_mokuro_button.text() == "Download OCR model"
        assert tab.mokuro_status_label.text() == _MODEL_MISSING
        assert tab.engine_notice_label.text() == _MODEL_MISSING
        assert not tab.engine_notice_label.isHidden()
        assert not tab.run_button.isEnabled()
        assert self._emitted(tab) == [False]

    @pytest.mark.parametrize(
        ("runtime_installed", "model_missing", "mb"),
        [(False, True, 30), (True, True, 13), (False, False, 17)],
        ids=["runtime-and-model", "model-only", "runtime-only"],
    )
    def test_the_model_tooltip_counts_only_what_is_missing(
        self, qtbot, tmp_path, monkeypatch, runtime_installed, model_missing, mb
    ):
        """The korean recogniser is 13.5 MB, the onnxruntime wheel about 17 MB."""
        asked: list[tuple] = []

        def missing(models_root, specs):
            asked.append(tuple(specs))
            return tuple(specs) if model_missing else ()

        monkeypatch.setattr(mokuro_tab_module.model_installer, "missing", missing)
        monkeypatch.setattr(
            mokuro_tab_module.mokuro_installer, "onnxruntime_installed", lambda uv_root: runtime_installed
        )
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(False, True))

        assert tab.install_mokuro_button.toolTip() == (
            f"Downloads the text-recognition model for your mining language (about {mb} MB) into Anki Miner's folder."
        )
        assert asked and set(asked) == {(RECOGNIZERS["korean"].model,)}

    def test_a_path_mokuro_in_a_recogniser_language_asks_for_the_in_app_one(self, qtbot, tmp_path, monkeypatch):
        """Review Focus 2: a PATH or override mokuro cannot run the recogniser."""
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(False, False))

        assert tab.install_mokuro_button.text() == "Install mokuro"
        assert "1 GB" in tab.install_mokuro_button.toolTip()
        assert tab.mokuro_status_label.text() == "Not installed"
        assert tab.engine_notice_label.text() == _OWN_MOKURO
        assert not tab.engine_notice_label.isHidden()
        assert self._emitted(tab) == [False]

    def test_everything_present_is_the_one_line_card_and_change_reinstalls(self, qtbot, tmp_path, monkeypatch):
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(True, True))

        assert not tab.installed_row.isHidden() and tab.setup_row.isHidden()
        assert tab.engine_notice_label.isHidden()
        assert tab.run_button.isEnabled()
        tab.change_setup_button.click()
        assert tab.install_mokuro_button.text() == "Reinstall mokuro"
        assert "1 GB" in tab.install_mokuro_button.toolTip()
        assert self._emitted(tab) == [True]

    def test_a_refresh_reapplies_the_stored_state_not_a_bare_bool(self, qtbot, tmp_path, monkeypatch):
        """Run start/end refreshes must keep "Download OCR model", not fall back to "Install mokuro"."""
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(False, True))

        tab._scan_pending_run = True
        tab._refresh_mokuro_setup_state()
        assert not tab.install_mokuro_button.isEnabled()

        tab._scan_pending_run = False
        tab._refresh_mokuro_setup_state()
        assert tab.install_mokuro_button.isEnabled()
        assert tab.install_mokuro_button.text() == "Download OCR model"
        assert tab.mokuro_status_label.text() == _MODEL_MISSING

    def test_change_reapplies_the_stored_state(self, qtbot, tmp_path, monkeypatch):
        tab = self._tab(qtbot, tmp_path, monkeypatch, MangaOcrState(True, True))
        tab._mokuro_is_available = False  # a stale bool must not decide the card

        tab.change_setup_button.click()

        assert tab.install_mokuro_button.text() == "Reinstall mokuro"
        assert tab.mokuro_status_label.text() == "Installed"

    def test_a_failed_probe_reads_not_installed(self, qtbot, tmp_path, monkeypatch):
        """The base class answers a raising probe with _apply_probe_result(False)."""
        with patch(_STATE, side_effect=RuntimeError("boom")), patch(_INSTALL_SUPPORTED, return_value=True):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: tab.mokuro_status_label.text() == "Not installed", timeout=3000)

        assert tab._mokuro_state == MangaOcrState(False, False)
        assert not tab.run_button.isEnabled()
        assert not tab.engine_notice_label.isHidden()
        assert tab.install_mokuro_button.text() == "Install mokuro"


class TestRun:
    def test_scan_then_worker_with_volumes_and_options(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        tab.redo_checkbox.setChecked(True)
        tab.gpu_checkbox.setChecked(False)
        fake = _FakeWorker()
        cls = _run(tab, qtbot, fake)
        args, kwargs = cls.call_args
        assert [v.source.name for v in args[1]] == ["vol1", "vol2"]
        assert all(isinstance(v, MokuroVolume) for v in args[1])
        assert kwargs["options"].force_cpu is True and kwargs["options"].no_cache is True
        assert kwargs["skip_processed"] is False
        assert tab._item_total() == 2
        assert not tab.run_button.isEnabled() and not tab.cancel_button.isHidden()

    def test_no_folder_raises_screen_issue(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        issues: list = []
        with patch(_AVAILABLE, return_value=True), patch.object(tab, "show_screen_issue", side_effect=issues.append):
            tab._on_run()
        assert issues and "folder" in issues[0].summary.lower()

    def test_empty_folder_raises_screen_issue(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        (tmp_path / "empty").mkdir()
        tab.folder_selector.set_path(str(tmp_path / "empty"))
        issues: list = []
        with (
            patch(_AVAILABLE, return_value=True),
            patch.object(tab, "show_screen_issue", side_effect=issues.append),
            patch(_WORKER_CLS) as cls,
        ):
            tab._on_run()
            qtbot.waitUntil(lambda: bool(issues), timeout=3000)
        cls.assert_not_called()
        assert "no manga volumes" in issues[0].summary.lower()

    def test_unwritable_folder_raises_screen_issue(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        issues: list = []
        with (
            patch(_AVAILABLE, return_value=True),
            patch(_OS_ACCESS, return_value=False),
            patch.object(tab, "show_screen_issue", side_effect=issues.append),
            patch(_WORKER_CLS) as cls,
        ):
            tab._on_run()
            qtbot.waitUntil(lambda: bool(issues), timeout=3000)
        cls.assert_not_called()
        assert "not writable" in issues[0].summary.lower()

    def test_probe_landing_during_run_scan_keeps_run_disabled(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab._scan_pending_run = True
        tab._apply_probe_result(True)
        assert not tab.run_button.isEnabled()

    def test_pending_run_scan_blocks_a_second_scan(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        # The scan thread has stopped but its result_ready has not run yet: the
        # flag alone must hold the gate.
        tab._scan_worker = None
        tab._scan_pending_run = True
        with patch(_AVAILABLE, return_value=True), patch(_RUN_OFF_THREAD) as dispatch:
            tab._on_run()
        dispatch.assert_not_called()

    def test_reentrancy_guard(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)
        with patch(_AVAILABLE, return_value=True), patch(_WORKER_CLS) as cls:
            tab._on_run()
        cls.assert_not_called()


class TestPreviewAndPersistence:
    def test_folder_change_updates_volumes_label(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        series = _series(tmp_path)
        (tmp_path / "series" / "vol1.mokuro").write_text("{}")
        # Drive the slot path_changed is wired to directly, so the test stays
        # signal-agnostic (set_path would reach it via textChanged).
        tab._on_folder_changed(str(series))
        qtbot.waitUntil(lambda: "2" in tab.volumes_label.text(), timeout=3000)
        assert "1" in tab.volumes_label.text()  # already processed count

    def test_stale_preview_scan_is_dropped(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        series = _series(tmp_path)
        tab._on_folder_changed(str(series))
        tab._on_folder_changed("")  # user cleared the field before the scan landed
        assert tab._scan_worker is not None and tab._scan_worker.wait(3000)
        qtbot.wait(50)
        assert tab.volumes_label.text() == ""

    def test_gpu_toggle_persists_via_run_options_changed(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        seen: list = []
        tab.run_options_changed.connect(seen.append)
        tab.gpu_checkbox.setChecked(False)
        assert seen and seen[-1].mokuro_use_gpu is False

    def test_redo_toggle_is_transient(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        seen: list = []
        tab.run_options_changed.connect(seen.append)
        tab.redo_checkbox.setChecked(True)
        assert seen == []

    def test_update_config_with_only_gpu_change_skips_probe(self, qtbot, tmp_path):
        import dataclasses

        tab = _make_tab(_config(tmp_path), qtbot)
        with patch(_COMPUTE_AVAILABLE, return_value=True) as compute:
            tab.update_config(dataclasses.replace(tab.config, mokuro_use_gpu=False))
            assert compute.call_count == 0
            tab.update_config(dataclasses.replace(tab.config, mokuro_location=tmp_path / "m"))
            assert tab._availability_worker.wait(3000)
            assert compute.call_count == 1

    def test_notify_install_finished_reprobes_and_enables_run(self, qtbot, tmp_path):
        """An install changes no config value, so update_config's mask would skip
        the probe; the install wiring tells the tab directly."""
        with patch(_COMPUTE_AVAILABLE, return_value=False):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
        assert not tab.run_button.isEnabled()

        with patch(_COMPUTE_AVAILABLE, return_value=True) as compute:
            tab.notify_install_finished(True)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(tab.run_button.isEnabled, timeout=3000)
        assert compute.call_count == 1
        assert tab.engine_notice_label.isHidden()


class TestCancelAndClose:
    def test_cancel_forwards_to_worker(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)
        tab.cancel_button.click()
        assert fake.cancelled and not tab.cancel_button.isEnabled()

    def test_iter_close_workers_yields_run_worker(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)
        assert fake in list(tab.iter_close_workers())
