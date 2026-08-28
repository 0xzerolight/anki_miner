"""Tests for DownloadTab (Utilities → Download, standalone yt-dlp downloader).

Covers construction, the yt-dlp availability guard, URL validation (blank /
invalid / T-34 dash-leading lines), worker kwargs assembly (dest + preset /
custom-format options), option persistence via config_changed, update_config
reseeding, output-folder choose/reset, cancel, and the reentrancy guard.

No real yt-dlp runs: DownloadWorker and the availability probe are patched.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.services.media_downloader import DownloadOptions

# ---------------------------------------------------------------------------
# Patch-target constants
# ---------------------------------------------------------------------------

_AVAILABLE = "anki_miner.gui.widgets.download_tab.DownloadTab._ytdlp_ready"
_COMPUTE_AVAILABLE = "anki_miner.gui.widgets.download_tab.DownloadTab._compute_ytdlp_available"
_OS_ACCESS = "anki_miner.gui.widgets.download_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.download_tab.DownloadWorker"
_PICK_DIRECTORY = "anki_miner.gui.widgets._tool_tab_base.file_dialogs.pick_directory"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    return AnkiMinerConfig(media_temp_folder=tmp_path / "tmp")


class _FakeWorker:
    """Minimal fake mimicking the DownloadWorker interface used by the tab."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.file_started = MagicMock()
        self.file_progress = MagicMock()
        self.file_finished = MagicMock()
        self.file_skipped = MagicMock()
        self.queue_finished = MagicMock()
        self.error = MagicMock()
        self.finished = MagicMock()
        self.deleteLater = MagicMock()
        self._started = False
        self._cancelled = False

    def start(self):
        self._started = True

    def cancel(self):
        self._cancelled = True

    def isRunning(self):
        return self._started and not self._cancelled

    def wait(self, *args):
        return True


def _make_tab(config, qtbot) -> DownloadTab:
    """Construct a DownloadTab with yt-dlp patched available=True."""
    with patch(_COMPUTE_AVAILABLE, return_value=True):
        tab = DownloadTab(config)
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.download_button.isEnabled, timeout=3000)
    return tab


def _start_download(tab: DownloadTab, fake_worker: _FakeWorker):
    """Click Download with availability + writability patched; return the class mock."""
    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker) as worker_cls,
    ):
        tab.download_button.click()
    return worker_cls


# ---------------------------------------------------------------------------
# Construction / contract
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_constructs_and_declares_contract(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        assert tab.TASK_ID == "tools.download"
        assert tab.TASK_OWNER is not None
        assert tab.TASK_OWNER.main_tab == "subtitles"
        assert tab.TASK_OWNER.subtab == "download"
        assert tab.OUTPUT_HISTORY_KEY == "tools.download.output"
        assert tab.worker_thread is None
        assert tab._custom_output_dir is None
        assert tab.url_input.tabChangesFocus() is True

    def test_unavailable_ytdlp_disables_primary(self, qtbot, tmp_path: Path) -> None:
        with patch(_COMPUTE_AVAILABLE, return_value=False):
            tab = DownloadTab(_make_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: tab.engine_notice_label.isVisibleTo(tab), timeout=3000)
        assert not tab.download_button.isEnabled()

    def test_suppress_optional_startup_skips_probe(self, qtbot, tmp_path: Path) -> None:
        tab = DownloadTab(_make_config(tmp_path), suppress_optional_startup=True)
        qtbot.addWidget(tab)
        assert tab._availability_worker is None


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


class TestUrlValidation:
    def test_blank_input_refuses_with_issue_no_worker(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        worker_cls = _start_download(tab, _FakeWorker())
        worker_cls.assert_not_called()
        assert tab.issue_banner().current_issue() is not None

    def test_invalid_lines_refuse_with_details(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/ok\nftp://bad\n--update-to\n")
        worker_cls = _start_download(tab, _FakeWorker())
        worker_cls.assert_not_called()
        issue = tab.issue_banner().current_issue()
        assert issue is not None
        assert "ftp://bad" in issue.details
        assert "--update-to" in issue.details

    def test_whitespace_and_blank_lines_dropped(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("\n  https://example.com/a  \n\nhttps://example.com/b\n")
        fake = _FakeWorker()
        worker_cls = _start_download(tab, fake)
        worker_cls.assert_called_once()
        assert worker_cls.call_args.args[1] == ["https://example.com/a", "https://example.com/b"]


# ---------------------------------------------------------------------------
# Worker construction
# ---------------------------------------------------------------------------


class TestWorkerConstruction:
    def test_worker_receives_urls_dest_and_options(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        fake = _FakeWorker()
        worker_cls = _start_download(tab, fake)
        worker_cls.assert_called_once()
        args = worker_cls.call_args
        assert args.args[0] is tab.config
        assert args.args[1] == ["https://example.com/v"]
        assert args.kwargs["dest_dir"] == tab._default_download_dir
        options = args.kwargs["options"]
        assert isinstance(options, DownloadOptions)
        assert options.format_selector == "bestvideo*+bestaudio/best"
        assert options.extract_audio_format is None
        assert fake._started is True
        assert tab.worker_thread is fake

    def test_audio_preset_maps_to_extract_options(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        tab.preset_combo.setCurrentIndex(tab.preset_combo.findData("audio_mp3"))
        worker_cls = _start_download(tab, _FakeWorker())
        options = worker_cls.call_args.kwargs["options"]
        assert options.format_selector == "bestaudio/best"
        assert options.extract_audio_format == "mp3"

    def test_custom_format_overrides_preset_and_audio(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        tab.preset_combo.setCurrentIndex(tab.preset_combo.findData("audio_mp3"))
        tab.custom_format_edit.setText("bestvideo[height<=480]+bestaudio")
        worker_cls = _start_download(tab, _FakeWorker())
        options = worker_cls.call_args.kwargs["options"]
        assert options.format_selector == "bestvideo[height<=480]+bestaudio"
        assert options.extract_audio_format is None

    def test_quality_presets_offered_in_descending_order(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        keys = [tab.preset_combo.itemData(i) for i in range(tab.preset_combo.count())]
        assert keys == ["best", "1440p", "1080p", "720p", "audio_mp3", "audio_m4a"]

    def test_1440p_preset_maps_to_height_capped_selector(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        tab.preset_combo.setCurrentIndex(tab.preset_combo.findData("1440p"))
        worker_cls = _start_download(tab, _FakeWorker())
        options = worker_cls.call_args.kwargs["options"]
        assert options.format_selector == "bestvideo[height<=1440]+bestaudio/best[height<=1440]"
        assert options.extract_audio_format is None

    def test_extras_map_to_options(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        tab.write_subs_checkbox.setChecked(True)
        tab.sub_langs_edit.setText("ja,en")
        tab.embed_thumbnail_checkbox.setChecked(True)
        tab.embed_metadata_checkbox.setChecked(True)
        worker_cls = _start_download(tab, _FakeWorker())
        options = worker_cls.call_args.kwargs["options"]
        assert options.write_subtitles is True
        assert options.subtitle_langs == "ja,en"
        assert options.embed_thumbnail is True
        assert options.embed_metadata is True

    def test_reentrancy_guard(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        fake = _FakeWorker()
        _start_download(tab, fake)
        second = _start_download(tab, _FakeWorker())
        second.assert_not_called()


# ---------------------------------------------------------------------------
# Option persistence
# ---------------------------------------------------------------------------


class TestOptionPersistence:
    def test_option_edit_emits_config_changed(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.preset_combo.setCurrentIndex(tab.preset_combo.findData("720p"))
        assert received
        assert received[-1].downloader_format_preset == "720p"
        tab.embed_thumbnail_checkbox.setChecked(True)
        assert received[-1].downloader_embed_thumbnail is True

    def test_seeding_suppresses_config_changed(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.config = replace(tab.config, downloader_format_preset="1080p")
        tab._apply_config_defaults()
        assert received == []
        assert tab.preset_combo.currentData() == "1080p"

    def test_update_config_reseeds_only_when_idle(self, qtbot, tmp_path: Path) -> None:
        # downloader_format_preset is itself a downloader_* field, so this
        # change carries no availability probe (D5) — only the idle reseed
        # path is under test here.
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.update_config(replace(tab.config, downloader_format_preset="audio_m4a"))
        assert tab.preset_combo.currentData() == "audio_m4a"

        tab.url_input.setPlainText("https://example.com/v")
        fake = _FakeWorker()
        _start_download(tab, fake)
        tab.update_config(replace(tab.config, downloader_format_preset="720p"))
        assert tab.preset_combo.currentData() == "audio_m4a"

    def test_sub_langs_enabled_with_checkbox(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        assert not tab.sub_langs_edit.isEnabled()
        tab.write_subs_checkbox.setChecked(True)
        assert tab.sub_langs_edit.isEnabled()


# ---------------------------------------------------------------------------
# Config loop and refusal polish (D4, D5, D6, D7)
# ---------------------------------------------------------------------------


class TestConfigLoopAndRefusal:
    def test_differ_ignores_whitespace_and_empty_langs(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.sub_langs_edit.setText(" ja ")
        tab._on_option_changed()  # commits the normalized "ja"
        tab.sub_langs_edit.setText(" ja ")  # uncommitted raw text again
        assert tab._options_differ_from_widgets() is False

    def test_downloader_only_config_change_skips_probe(self, qtbot, tmp_path: Path, monkeypatch) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        calls: list[int] = []
        monkeypatch.setattr(tab, "_refresh_engine_state", lambda: calls.append(1))

        downloader_only = dataclasses.replace(
            tab.config, downloader_embed_thumbnail=not tab.config.downloader_embed_thumbnail
        )
        tab.update_config(downloader_only)
        assert calls == []

        changed_elsewhere = dataclasses.replace(tab.config, youtube_cookies_from_browser="firefox")
        tab.update_config(changed_elsewhere)
        assert calls == [1]

    def test_probe_result_never_enables_button_mid_run(self, qtbot, tmp_path: Path, monkeypatch) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        monkeypatch.setattr("anki_miner.gui.widgets.download_tab.still_running", lambda w: True)
        tab._apply_probe_result(True)
        assert tab.download_button.isEnabled() is False

    def test_probe_result_enables_button_when_idle(self, qtbot, tmp_path: Path, monkeypatch) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        monkeypatch.setattr("anki_miner.gui.widgets.download_tab.still_running", lambda w: False)
        tab._apply_probe_result(True)
        assert tab.download_button.isEnabled() is True

    def test_unwritable_folder_raises_screen_issue(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        issues: list[object] = []
        with (
            patch(_OS_ACCESS, return_value=False),
            patch.object(tab, "show_screen_issue", side_effect=issues.append),
        ):
            tab._on_download()
        assert issues, "refusal must raise a ScreenIssue, not a log line"
        # Its own refusal, not the generic run-problem banner every other
        # logged ERROR raises (_on_log_problem).
        assert issues[0].summary != tab._strings.run_problem
        assert "not writable" in issues[0].summary.lower()


# ---------------------------------------------------------------------------
# Output folder / cancel
# ---------------------------------------------------------------------------


class TestOutputAndCancel:
    def test_choose_and_reset_output_folder(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        chosen = tmp_path / "downloads"
        chosen.mkdir()

        def _fake_pick(_parent, _title, _start, on_done):
            on_done(str(chosen))

        with patch(_PICK_DIRECTORY, side_effect=_fake_pick):
            tab.choose_output_button.click()
        assert tab._custom_output_dir == chosen
        assert tab.output_location_label.text() == str(chosen)

        tab.url_input.setPlainText("https://example.com/v")
        worker_cls = _start_download(tab, _FakeWorker())
        assert worker_cls.call_args.kwargs["dest_dir"] == chosen

        tab.clear_output_button.click()
        assert tab._custom_output_dir is None
        assert tab.output_location_label.text() == tab._strings.output_default

    def test_cancel_flips_buttons_and_cancels_worker(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        fake = _FakeWorker()
        _start_download(tab, fake)
        assert not tab.download_button.isEnabled()

        tab.cancel_button.click()
        assert fake._cancelled is True
        assert tab._cancelled is True
        assert not tab.cancel_button.isEnabled()

    def test_iter_close_workers_yields_active_worker(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        fake = _FakeWorker()
        _start_download(tab, fake)
        assert fake in list(tab.iter_close_workers())
