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

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QDialog

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.services.media_downloader import (
    PLAYLIST_PROBE_MAX,
    DownloadOptions,
    DownloadPlaylist,
    DownloadPlaylistEntry,
    UrlTracks,
)

# ---------------------------------------------------------------------------
# Patch-target constants
# ---------------------------------------------------------------------------

_AVAILABLE = "anki_miner.gui.widgets.download_tab.DownloadTab._ytdlp_ready"
_COMPUTE_AVAILABLE = "anki_miner.gui.widgets.download_tab.DownloadTab._compute_ytdlp_available"
_OS_ACCESS = "anki_miner.gui.widgets.download_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.download_tab.DownloadWorker"
_PICK_DIRECTORY = "anki_miner.gui.widgets._tool_tab_base.file_dialogs.pick_directory"
_PROBE_WORKER_CLS = "anki_miner.gui.widgets.download_tab.DownloadTracksProbeWorker"
_RESOLVE_WORKER_CLS = "anki_miner.gui.widgets.download_tab.DownloadPlaylistResolveWorker"
_PICKER_DIALOG_CLS = "anki_miner.gui.widgets.download_tab.LanguagePickerDialog"
_PLAYLIST_DIALOG_CLS = "anki_miner.gui.widgets.download_tab.PlaylistPickerDialog"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path, **overrides) -> AnkiMinerConfig:
    return AnkiMinerConfig(media_temp_folder=tmp_path / "tmp", **overrides)


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
        # The same fake stands in for the two probe workers.
        self.tracks_probed = MagicMock()
        self.playlist_resolved = MagicMock()
        self.probe_error = MagicMock()
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
        tab._set_sub_langs("ja,en")
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
        assert not tab.sub_langs_button.isEnabled()
        tab.write_subs_checkbox.setChecked(True)
        assert tab.sub_langs_button.isEnabled()


# ---------------------------------------------------------------------------
# Config loop and refusal polish (D4, D5, D6, D7)
# ---------------------------------------------------------------------------


class TestConfigLoopAndRefusal:
    def test_differ_ignores_whitespace_and_empty_langs(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab._set_sub_langs(" ja ")  # commits the normalized "ja"
        assert tab._normalized_sub_langs() == "ja"
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


# ---------------------------------------------------------------------------
# Language pickers (subtitle + audio) and track detection
# ---------------------------------------------------------------------------


def _put_cursor_on_line(tab: DownloadTab, line_index: int) -> None:
    cursor = tab.url_input.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    for _ in range(line_index):
        cursor.movePosition(QTextCursor.MoveOperation.Down)
    tab.url_input.setTextCursor(cursor)


def _expand(tab: DownloadTab) -> MagicMock:
    """Click Expand Playlist with the resolve worker faked; return the class mock."""
    with patch(_RESOLVE_WORKER_CLS, return_value=_FakeWorker()) as worker_cls:
        tab.expand_playlist_button.click()
    return worker_cls


def _accepting_dialog(urls: list[str]) -> MagicMock:
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.selected_urls.return_value = urls
    return dialog


class TestSubtitleLanguagePicker:
    def test_button_summarises_the_selection_with_names(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path, downloader_subtitle_langs="ja,en"), qtbot)
        assert "Japanese" in tab.sub_langs_button.text()
        assert "English" in tab.sub_langs_button.text()

    def test_an_expression_is_shown_verbatim(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path, downloader_subtitle_langs="all,-live_chat"), qtbot)
        assert tab.sub_langs_button.text() == "all,-live_chat"

    def test_accepting_the_dialog_persists_the_new_value(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.write_subs_checkbox.setChecked(True)
        emitted: list[AnkiMinerConfig] = []
        tab.config_changed.connect(emitted.append)
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.selected_langs.return_value = "ko,en"
        with patch(_PICKER_DIALOG_CLS, return_value=dialog):
            tab.sub_langs_button.click()
        assert tab._sub_langs == "ko,en"
        assert emitted[-1].downloader_subtitle_langs == "ko,en"
        assert "Korean" in tab.sub_langs_button.text()

    def test_rejecting_the_dialog_changes_nothing(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path, downloader_subtitle_langs="ja"), qtbot)
        tab.write_subs_checkbox.setChecked(True)
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        with patch(_PICKER_DIALOG_CLS, return_value=dialog):
            tab.sub_langs_button.click()
        assert tab._sub_langs == "ja"

    def test_detected_tracks_are_handed_to_the_dialog(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.write_subs_checkbox.setChecked(True)
        tracks = UrlTracks("T", ("ja",), (), (), False)
        tab._on_tracks_probed(tracks)
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        with patch(_PICKER_DIALOG_CLS, return_value=dialog) as dialog_cls:
            tab.sub_langs_button.click()
        assert dialog_cls.call_args.kwargs["detected"] is tracks


class TestAudioLanguageWidget:
    def test_default_is_any(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        assert tab.audio_lang_combo.currentData() == ""
        assert tab._build_options().audio_lang == ""

    def test_selection_reaches_the_options(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path, downloader_audio_lang="ja"), qtbot)
        assert tab._build_options().audio_lang == "ja"

    def test_a_custom_format_string_suppresses_the_preference(self, qtbot, tmp_path: Path) -> None:
        """Custom format is documented raw mode: the tab must not rewrite it."""
        config = _make_config(tmp_path, downloader_audio_lang="ja", downloader_custom_format="bv+ba")
        tab = _make_tab(config, qtbot)
        options = tab._build_options()
        assert options.format_selector == "bv+ba"
        assert options.audio_lang == ""

    def test_choosing_a_language_persists_it(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        emitted: list[AnkiMinerConfig] = []
        tab.config_changed.connect(emitted.append)
        tab.audio_lang_combo.setCurrentIndex(tab.audio_lang_combo.findData("ko"))
        assert emitted[-1].downloader_audio_lang == "ko"

    def test_the_combo_shows_names(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        labels = [tab.audio_lang_combo.itemText(i) for i in range(tab.audio_lang_combo.count())]
        assert any("Japanese" in label for label in labels)

    def test_a_saved_language_outside_the_curated_list_survives_restart(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path, downloader_audio_lang="sw"), qtbot)
        assert tab.audio_lang_combo.currentData() == "sw"
        assert "Swahili" in tab.audio_lang_combo.currentText()

    def test_reseeding_a_detected_only_language_does_not_persist(self, qtbot, tmp_path: Path) -> None:
        """The nested seeding guard must restore, not clear, the outer one."""
        tab = _make_tab(_make_config(tmp_path), qtbot)
        received: list[AnkiMinerConfig] = []
        tab.config_changed.connect(received.append)
        tab.config = replace(tab.config, downloader_audio_lang="sw")
        tab._apply_config_defaults()
        assert received == []
        assert tab.audio_lang_combo.currentData() == "sw"


class TestDetectTracks:
    def test_detect_is_disabled_without_a_url(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        assert tab.detect_button.isEnabled() is False
        tab.url_input.setPlainText("https://example.com/v")
        assert tab.detect_button.isEnabled() is True

    def test_detect_probes_the_first_url(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/a\nhttps://example.com/b")
        with patch(_PROBE_WORKER_CLS, return_value=_FakeWorker()) as worker_cls:
            tab.detect_button.click()
        assert worker_cls.call_args.args[1] == "https://example.com/a"

    def test_a_detected_audio_language_joins_the_combo(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab._on_tracks_probed(UrlTracks("T", ("ja",), (), ("ja", "sw"), False))
        assert tab.audio_lang_combo.findData("sw") >= 0
        assert tab._detected is not None

    def test_a_detected_language_already_listed_is_not_duplicated(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        before = tab.audio_lang_combo.count()
        tab._on_tracks_probed(UrlTracks("T", (), (), ("ja",), False))
        assert tab.audio_lang_combo.count() == before

    def test_detection_does_not_change_the_chosen_audio_language(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path, downloader_audio_lang="ja"), qtbot)
        tab._on_tracks_probed(UrlTracks("T", (), (), ("sw",), False))
        assert tab.audio_lang_combo.currentData() == "ja"

    def test_a_probe_failure_raises_a_screen_issue_and_re_enables_detect(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        tab._on_tracks_error("nope")
        assert tab.issue_banner().current_issue() is not None
        assert tab.detect_button.isEnabled() is True

    def test_a_result_for_a_url_that_left_the_box_is_dropped(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/a")
        fake = _FakeWorker()
        with patch(_PROBE_WORKER_CLS, return_value=fake):
            tab.detect_button.click()
        deliver = fake.tracks_probed.connect.call_args.args[0]
        tab.url_input.setPlainText("https://example.com/b")
        deliver(UrlTracks("T", ("ja",), (), ("sw",), False))
        assert tab._detected is None
        assert tab.audio_lang_combo.findData("sw") < 0
        tab.url_input.setPlainText("https://example.com/a\nhttps://example.com/b")
        deliver(UrlTracks("T", ("ja",), (), ("sw",), False))
        assert tab._detected is not None


# ---------------------------------------------------------------------------
# Playlist expansion
# ---------------------------------------------------------------------------


class TestExpandPlaylist:
    def test_button_is_disabled_without_a_url(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        assert tab.expand_playlist_button.isEnabled() is False

    def test_a_non_url_cursor_line_raises_a_screen_issue(self, qtbot, tmp_path: Path) -> None:
        # The button is live because line 0 is a URL; the cursor is on the
        # junk line, which is the case the guard exists for.
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/a\nnot a url")
        _put_cursor_on_line(tab, 1)
        tab.expand_playlist_button.click()
        assert tab.issue_banner().current_issue() is not None

    def test_the_cursor_line_is_the_one_resolved(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/a\nhttps://example.com/list")
        _put_cursor_on_line(tab, 1)
        with patch(_RESOLVE_WORKER_CLS, return_value=_FakeWorker()) as worker_cls:
            tab.expand_playlist_button.click()
        assert worker_cls.call_args.args[1] == "https://example.com/list"

    def test_accepted_entries_replace_the_cursor_line(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/keep\nhttps://example.com/list")
        tab._pending_playlist_url = "https://example.com/list"
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.selected_urls.return_value = ["https://example.com/1", "https://example.com/2"]
        with patch(_PLAYLIST_DIALOG_CLS, return_value=dialog):
            tab._on_playlist_resolved(DownloadPlaylist("L", (), 2))
        assert tab.url_input.toPlainText().splitlines() == [
            "https://example.com/keep",
            "https://example.com/1",
            "https://example.com/2",
        ]

    def test_urls_already_in_the_box_are_not_duplicated(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/1\nhttps://example.com/list")
        tab._pending_playlist_url = "https://example.com/list"
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Accepted
        dialog.selected_urls.return_value = ["https://example.com/1", "https://example.com/2"]
        with patch(_PLAYLIST_DIALOG_CLS, return_value=dialog):
            tab._on_playlist_resolved(DownloadPlaylist("L", (), 2))
        assert tab.url_input.toPlainText().splitlines() == [
            "https://example.com/1",
            "https://example.com/2",
        ]

    def test_a_rejected_dialog_leaves_the_line_alone(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list")
        tab._pending_playlist_url = "https://example.com/list"
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        with patch(_PLAYLIST_DIALOG_CLS, return_value=dialog):
            tab._on_playlist_resolved(DownloadPlaylist("L", (), 2))
        assert tab.url_input.toPlainText() == "https://example.com/list"

    def test_a_truncated_playlist_is_flagged_to_the_dialog(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list")
        tab._pending_playlist_url = "https://example.com/list"
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        entries = (DownloadPlaylistEntry(1, "V", "https://example.com/1", None),)
        with patch(_PLAYLIST_DIALOG_CLS, return_value=dialog) as dialog_cls:
            tab._on_playlist_resolved(DownloadPlaylist("L", entries, 900, truncated=True))
        assert dialog_cls.call_args.kwargs["truncated"] is True

    def test_a_complete_playlist_is_not_flagged_as_truncated(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list")
        tab._pending_playlist_url = "https://example.com/list"
        dialog = MagicMock()
        dialog.exec.return_value = QDialog.DialogCode.Rejected
        entries = (DownloadPlaylistEntry(1, "V", "https://example.com/1", None),)
        with patch(_PLAYLIST_DIALOG_CLS, return_value=dialog) as dialog_cls:
            tab._on_playlist_resolved(DownloadPlaylist("L", entries, 1))
        assert dialog_cls.call_args.kwargs["truncated"] is False

    def test_a_resolve_failure_raises_a_screen_issue_and_re_enables_the_button(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/v")
        tab._on_playlist_error("That URL is not a playlist")
        assert tab.issue_banner().current_issue() is not None
        assert tab.expand_playlist_button.isEnabled() is True

    def test_the_live_probe_workers_are_reported_for_close(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        worker = _FakeWorker()
        worker.start()
        tab._playlist_worker = worker
        assert worker in list(tab.iter_close_workers())

    def test_a_truncated_batch_advances_the_cursor_for_the_next_paste(self, qtbot, tmp_path: Path) -> None:
        """The playlist line is replaced as always (a bare playlist URL left in the
        box would download the whole playlist); pasting it again continues."""
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/keep\nhttps://example.com/list")
        _put_cursor_on_line(tab, 1)
        assert _expand(tab).call_args.kwargs["start"] == 1
        entries = tuple(DownloadPlaylistEntry(i, f"V{i}", f"https://example.com/{i}", None) for i in (1, 2))
        with patch(_PLAYLIST_DIALOG_CLS, return_value=_accepting_dialog([e.url for e in entries])):
            tab._on_playlist_resolved(DownloadPlaylist("L", entries, None, truncated=True))
        assert tab.url_input.toPlainText().splitlines() == [
            "https://example.com/keep",
            "https://example.com/1",
            "https://example.com/2",
        ]
        # Advances by the raw window (PLAYLIST_PROBE_MAX), not by the two usable entries.
        assert tab._playlist_cursor == {"https://example.com/list": 1 + PLAYLIST_PROBE_MAX}
        tab._on_playlist_finished()
        tab.url_input.appendPlainText("https://example.com/list")
        _put_cursor_on_line(tab, 3)
        assert _expand(tab).call_args.kwargs["start"] == 1 + PLAYLIST_PROBE_MAX

    def test_a_failed_page_forgets_the_cursor(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list")
        tab._playlist_cursor["https://example.com/list"] = 501
        _expand(tab)
        tab._on_playlist_error("The playlist is empty.")
        assert tab._playlist_cursor == {}

    def test_a_complete_batch_replaces_the_line_and_clears_the_cursor(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list")
        tab._playlist_cursor["https://example.com/list"] = 3
        assert _expand(tab).call_args.kwargs["start"] == 3
        entries = (DownloadPlaylistEntry(3, "V3", "https://example.com/3", None),)
        with patch(_PLAYLIST_DIALOG_CLS, return_value=_accepting_dialog(["https://example.com/3"])):
            tab._on_playlist_resolved(DownloadPlaylist("L", entries, 3))
        assert tab.url_input.toPlainText().splitlines() == ["https://example.com/3"]
        assert tab._playlist_cursor == {}

    def test_a_moved_playlist_line_is_still_the_one_replaced(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list\nhttps://example.com/keep")
        _put_cursor_on_line(tab, 0)
        _expand(tab)
        tab.url_input.setPlainText("https://example.com/new\nhttps://example.com/keep\nhttps://example.com/list")
        with patch(_PLAYLIST_DIALOG_CLS, return_value=_accepting_dialog(["https://example.com/1"])):
            tab._on_playlist_resolved(DownloadPlaylist("L", (), 1))
        assert tab.url_input.toPlainText().splitlines() == [
            "https://example.com/new",
            "https://example.com/keep",
            "https://example.com/1",
        ]

    def test_a_removed_playlist_line_adds_nothing(self, qtbot, tmp_path: Path) -> None:
        tab = _make_tab(_make_config(tmp_path), qtbot)
        tab.url_input.setPlainText("https://example.com/list")
        _expand(tab)
        tab.url_input.setPlainText("https://example.com/other")
        with patch(_PLAYLIST_DIALOG_CLS, return_value=_accepting_dialog(["https://example.com/1"])) as dialog_cls:
            tab._on_playlist_resolved(DownloadPlaylist("L", (), 1))
        dialog_cls.assert_not_called()
        assert tab.url_input.toPlainText() == "https://example.com/other"
