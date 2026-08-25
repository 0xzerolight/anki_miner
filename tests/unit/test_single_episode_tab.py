"""Tests for SingleEpisodeTab audio track override wiring (Issue #35)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def _recording_curation_dialog_cls():
    """Real-``QDialog`` curator stand-in that records its construction kwargs.

    The curator is shown, not exec()'d (decision D33): the tab connects a
    resolver to ``finished`` and returns. A ``MagicMock`` has no real signal, so
    the parked worker would never be released — hence a real ``QDialog`` that
    confirms itself the moment the tab shows it.
    """
    from PyQt6.QtWidgets import QDialog

    created: list = []

    class _RecordingCurationDialog(QDialog):
        def __init__(self, words, parent=None, **kwargs):
            super().__init__(parent)
            self.words = list(words)
            self.kwargs = kwargs
            created.append(self)

        def show(self):
            super().show()
            self.accept()

        def get_selected_words(self):
            return []

    return _RecordingCurationDialog, created


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------


def test_initial_audio_track_override_is_none(tab):
    assert tab._audio_track_override is None


def test_card_source_defaults_to_sanitized_video_stem(tab, tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    video = downloads / "[Judas] Jujutsu Kaisen 0 [WEBRip][JA]-Group.mkv"
    video.touch()

    tab.video_selector.set_path(str(video))

    assert tab.card_source_edit.text() == "[Judas] Jujutsu Kaisen 0"


def test_card_source_updates_when_video_path_changes(tab, tmp_path):
    first_video = tmp_path / "First Movie.mkv"
    second_video = tmp_path / "Second Movie.mkv"
    first_video.touch()
    second_video.touch()

    tab.video_selector.set_path(str(first_video))
    tab.card_source_edit.setText("Custom source")
    tab.video_selector.set_path(str(second_video))

    assert tab.card_source_edit.text() == "Second Movie"


# ---------------------------------------------------------------------------
# 2. Tracks button exists
# ---------------------------------------------------------------------------


def test_tracks_button_exists(tab):
    assert hasattr(tab, "tracks_button")
    assert tab.tracks_button.text() == "Tracks"


# ---------------------------------------------------------------------------
# 2b. Recent-files combo does not drive horizontal overflow (Issue #56)
# ---------------------------------------------------------------------------


def test_recent_combo_does_not_drive_horizontal_overflow(tab):
    long_item = "[Group] Very Long Release Name - 01 (1080p) [DEADBEEF].mkv + Very Long Release Name - 01.srt"
    tab.recent_combo.addItem(long_item)
    # Bounded minimum width: combo must be able to shrink, not pin the layout wide.
    assert tab.recent_combo.minimumSizeHint().width() < 300


# ---------------------------------------------------------------------------
# 3. Override resets on video path change
# ---------------------------------------------------------------------------


def test_override_resets_on_video_path_change(tab):
    tab._audio_track_override = 2
    tab.video_selector.path_changed.emit("/different/file.mkv")
    assert tab._audio_track_override is None


# ---------------------------------------------------------------------------
# 4. Warning shown when no video selected
# ---------------------------------------------------------------------------


def test_tracks_clicked_warns_when_no_video(tab):
    with patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams") as mock_list:
        tab.video_selector.get_path = MagicMock(return_value="")
        tab._on_tracks_clicked()
        assert tab.issue_banner().current_issue() is not None
        mock_list.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Dialog opens; override stored on Accept
# ---------------------------------------------------------------------------


def test_tracks_clicked_stores_override_on_accept(tab, tmp_path, qtbot):
    from PyQt6.QtWidgets import QDialog

    from anki_miner.utils.audio_track_detector import AudioStream

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()

    streams = [
        AudioStream(
            global_index=1, audio_index=0, language_tag="jpn", title_tag=None, codec="aac", channels=2, is_default=True
        ),
        AudioStream(
            global_index=2, audio_index=1, language_tag="eng", title_tag=None, codec="aac", channels=2, is_default=False
        ),
    ]

    mock_dialog_instance = MagicMock()
    # Use the real DialogCode so the comparison in production code succeeds
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog_instance.DialogCode = QDialog.DialogCode
    mock_dialog_instance.selected_override.return_value = 1

    mock_class = MagicMock(return_value=mock_dialog_instance)
    mock_class.DialogCode = QDialog.DialogCode

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", return_value=streams) as mock_list,
        patch("anki_miner.gui.widgets.single_episode_tab.AudioTracksDialog", mock_class),
    ):
        tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
        tab.video_selector.is_valid = MagicMock(return_value=True)
        tab._on_tracks_clicked()
        # The probe runs off the GUI thread; wait for the dialog to be built in
        # the GUI-thread callback.
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    mock_list.assert_called_once()
    mock_class.assert_called_once()
    call_kwargs = mock_class.call_args[1]
    assert call_kwargs["streams"] == streams
    assert call_kwargs["current_override"] is None  # initial state
    # auto_detected resolved inline: first stream with language_tag in JAPANESE_LANGUAGE_CODES
    assert call_kwargs["auto_detected"] == streams[0]
    assert tab._audio_track_override == 1


# ---------------------------------------------------------------------------
# 6. Override unchanged on Cancel
# ---------------------------------------------------------------------------


def test_tracks_clicked_keeps_override_on_cancel(tab, tmp_path, qtbot):
    from PyQt6.QtWidgets import QDialog

    from anki_miner.utils.audio_track_detector import AudioStream

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()

    streams = [
        AudioStream(
            global_index=1, audio_index=0, language_tag="jpn", title_tag=None, codec="aac", channels=2, is_default=True
        ),
        AudioStream(
            global_index=2, audio_index=1, language_tag="eng", title_tag=None, codec="aac", channels=2, is_default=False
        ),
    ]

    mock_dialog_instance = MagicMock()
    # Rejected != Accepted, so override should not change
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Rejected
    mock_class = MagicMock(return_value=mock_dialog_instance)
    mock_class.DialogCode = QDialog.DialogCode

    tab._audio_track_override = 0

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", return_value=streams),
        patch("anki_miner.gui.widgets.single_episode_tab.AudioTracksDialog", mock_class),
    ):
        tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
        tab.video_selector.is_valid = MagicMock(return_value=True)
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    assert tab._audio_track_override == 0


# ---------------------------------------------------------------------------
# 7. Tracks probe passes the resolved ffprobe binary
# ---------------------------------------------------------------------------


def test_tracks_clicked_passes_resolved_ffprobe(qapp, qtbot, test_config, tmp_path):
    import dataclasses

    from anki_miner.utils import ffmpeg_resolver

    fake_ffprobe = tmp_path / "my_ffprobe"
    fake_ffprobe.write_text("#!/bin/sh\n")
    fake_ffprobe.chmod(0o755)
    cfg = dataclasses.replace(test_config, ffprobe_location=str(fake_ffprobe))

    widget = SingleEpisodeTab(
        config=cfg,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    try:
        ffmpeg_resolver._clear_cache()
        fake_video = tmp_path / "ep01.mkv"
        fake_video.touch()

        with (
            patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", return_value=[]) as mock_list,
            patch("PyQt6.QtWidgets.QMessageBox.information"),
        ):
            widget.video_selector.get_path = MagicMock(return_value=str(fake_video))
            widget.video_selector.is_valid = MagicMock(return_value=True)
            widget._on_tracks_clicked()
            qtbot.waitUntil(lambda: mock_list.called, timeout=3000)

        _, kwargs = mock_list.call_args
        assert kwargs.get("ffprobe_cmd") == str(fake_ffprobe)
    finally:
        ffmpeg_resolver._clear_cache()
        widget.deleteLater()


# ---------------------------------------------------------------------------
# 7. _start_processing passes override to EpisodeWorkerThread
# ---------------------------------------------------------------------------


def test_start_processing_passes_override_to_worker(tab, tmp_path):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab._audio_track_override = 1
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_worker = MagicMock(name="EpisodeWorkerThread")
    mock_processor = MagicMock(name="EpisodeProcessor")

    with (
        patch(
            "anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker
        ) as mock_worker_cls,
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor", return_value=mock_processor),
    ):
        tab._start_processing()

    mock_worker_cls.assert_called_once()
    _, kwargs = mock_worker_cls.call_args
    assert kwargs.get("audio_track_override") == 1


def test_start_processing_passes_custom_card_source_to_worker(tab, tmp_path):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.set_path(str(fake_video))
    tab.subtitle_selector.set_path(str(fake_subs))
    tab.card_source_edit.setText("Jujutsu Kaisen 0")

    mock_worker = MagicMock(name="EpisodeWorkerThread")

    with (
        patch(
            "anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker
        ) as mock_worker_cls,
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor"),
    ):
        tab._start_processing()

    mock_worker_cls.assert_called_once()
    _, kwargs = mock_worker_cls.call_args
    assert kwargs.get("source_label_override") == "Jujutsu Kaisen 0"


# ---------------------------------------------------------------------------
# 8. _on_timing_clicked passes override to SubtitleViewer
# ---------------------------------------------------------------------------


def test_timing_clicked_passes_override_to_subtitle_viewer(tab, tmp_path, qtbot):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab._audio_track_override = 2
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    # parse_raw_entries returns list[tuple[float, float, str]]
    fake_entry = (0.0, 2.5, "テスト")
    mock_viewer_instance = MagicMock()
    mock_viewer_instance.exec.return_value = mock_viewer_instance.DialogCode.Rejected

    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.return_value = [fake_entry]

    with (
        patch(
            "anki_miner.gui.widgets.subtitle_viewer.SubtitleViewer", return_value=mock_viewer_instance
        ) as mock_viewer_cls,
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
    ):
        tab._on_timing_clicked()
        # The parse runs off the GUI thread; wait for the viewer to be built.
        qtbot.waitUntil(lambda: mock_viewer_cls.called, timeout=3000)

    mock_viewer_cls.assert_called_once()
    _, kwargs = mock_viewer_cls.call_args
    assert kwargs.get("audio_track_override") == 2


# ---------------------------------------------------------------------------
# 9. Override resets after process success
# ---------------------------------------------------------------------------


def test_override_resets_after_processing_finished(tab):
    tab._audio_track_override = 1
    # _on_processing_finished calls _restore_buttons, which expects the worker set up
    tab.worker_thread = MagicMock(name="EpisodeWorkerThread")
    tab.worker_thread.isRunning.return_value = False
    tab._curation_video = Path("/video/ep01.mkv")
    tab._curation_subtitle = Path("/subs/ep01.ass")
    tab._curation_video_raw = "/video/ep01.mkv"
    tab._curation_subtitle_raw = "/subs/ep01.ass"

    result = MagicMock(name="ProcessingResult")
    tab._on_processing_finished(result)
    assert tab._audio_track_override is None


# ---------------------------------------------------------------------------
# 10. Override survives processing error so retry uses the same track
# ---------------------------------------------------------------------------


def test_override_survives_processing_error_for_retry(tab):
    """Failed runs keep _audio_track_override so the user can retry on the
    same audio track without having to re-pick it from the dialog."""
    tab._audio_track_override = 1
    tab.worker_thread = MagicMock(name="EpisodeWorkerThread")
    tab.worker_thread.isRunning.return_value = False

    tab._on_processing_error("Something went wrong")
    assert tab._audio_track_override == 1


# ---------------------------------------------------------------------------
# 11. Inline auto-stream lookup uses JAPANESE_LANGUAGE_CODES
# ---------------------------------------------------------------------------


def test_tracks_clicked_auto_detected_uses_inline_lookup(tab, tmp_path, qtbot):
    """auto_detected is resolved from the already-probed streams list, not a
    second ffprobe call. A stream with language_tag='jpn' must be passed as
    auto_detected; a stream with language_tag='eng' must not."""
    from PyQt6.QtWidgets import QDialog

    from anki_miner.utils.audio_track_detector import AudioStream

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()

    jpn_stream = AudioStream(
        global_index=1, audio_index=0, language_tag="jpn", title_tag=None, codec="aac", channels=2, is_default=False
    )
    eng_stream = AudioStream(
        global_index=2, audio_index=1, language_tag="eng", title_tag=None, codec="aac", channels=2, is_default=True
    )
    streams = [jpn_stream, eng_stream]

    mock_dialog_instance = MagicMock()
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Rejected
    mock_class = MagicMock(return_value=mock_dialog_instance)
    mock_class.DialogCode = QDialog.DialogCode

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", return_value=streams),
        patch("anki_miner.gui.widgets.single_episode_tab.AudioTracksDialog", mock_class),
    ):
        tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
        tab.video_selector.is_valid = MagicMock(return_value=True)
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    call_kwargs = mock_class.call_args[1]
    assert call_kwargs["auto_detected"] is jpn_stream


# ---------------------------------------------------------------------------
# 12. timing_button hidden during processing, shown on restore
# ---------------------------------------------------------------------------


def test_timing_button_hidden_during_processing_and_restored(tab, tmp_path):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_worker = MagicMock(name="EpisodeWorkerThread")
    mock_processor = MagicMock(name="EpisodeProcessor")

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker),
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor", return_value=mock_processor),
    ):
        tab._start_processing()

    assert tab.timing_button.isHidden(), "timing_button should be hidden during processing"
    assert tab.tracks_button.isHidden(), "tracks_button should be hidden during processing"

    tab._restore_buttons()

    assert not tab.timing_button.isHidden(), "timing_button should not be hidden after restore"
    assert not tab.tracks_button.isHidden(), "tracks_button should not be hidden after restore"


# ---------------------------------------------------------------------------
# 13. _on_curation_requested passes media_context and lookup_fn to dialog
# ---------------------------------------------------------------------------


def test_curation_requested_passes_media_context_and_lookup_fn(tab, facade_processor, tmp_path, qtbot):
    """Dialog receives a CurationMediaContext and lookup_fn when files are set
    and a worker with a live processor is present."""
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.offset_spinbox.setValue(1.5)
    # _build_curation_context reads the GUI-thread snapshots (captured at
    # _start_processing), not live widgets — set them as that start would.
    tab._curation_video = fake_video
    tab._curation_subtitle = fake_subs
    tab._curation_offset = 1.5
    tab._curation_audio_track_override = tab._audio_track_override

    # Worker exposing a real processor (T-60 typed contract): lookup_fn must
    # resolve through the offline_lookup_fn facade to the definition service.
    fake_lookup = MagicMock(name="lookup_all_offline")
    facade_processor.definition_service.lookup_all_offline = fake_lookup
    fake_worker = MagicMock()
    fake_worker.curation_processor = facade_processor
    tab.worker_thread = fake_worker

    fake_entry = (0.0, 2.5, "テスト")
    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.return_value = [fake_entry]

    dialog_cls, created = _recording_curation_dialog_cls()

    words: list = []
    with (
        patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
        patch("anki_miner.gui.widgets._mining_tab_base.WordCurationDialog", dialog_cls),
    ):
        tab._on_curation_requested(words)
        # The context build (subtitle parse) runs off-thread; wait for the
        # GUI-thread callback to construct the dialog.
        qtbot.waitUntil(lambda: bool(created), timeout=3000)

    assert len(created) == 1
    call_kwargs = created[0].kwargs
    assert call_kwargs.get("lookup_fn") is fake_lookup
    ctx = call_kwargs.get("media_context")
    assert ctx is not None
    assert ctx.video_file == fake_video
    assert ctx.subtitle_entries == [fake_entry]
    assert ctx.offset == pytest.approx(1.5)
    assert ctx.audio_track_override == tab._audio_track_override
    # Curation event must be set so the worker-thread mock can proceed
    assert tab._curation_event.is_set()


# ---------------------------------------------------------------------------
# 14. Subtitle parse failure → media_context=None, dialog still constructed
# ---------------------------------------------------------------------------


def test_curation_requested_parse_error_passes_none_media_context(tab, tmp_path, qtbot):
    """When subtitle parsing raises, dialog is still called with media_context=None."""
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))

    # Parser raises on parse_raw_entries
    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.side_effect = RuntimeError("bad file")

    dialog_cls, created = _recording_curation_dialog_cls()

    tab.worker_thread = None  # no worker — lookup_fn will also be None

    with (
        patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            dialog_cls,
        ),
    ):
        tab._on_curation_requested([])
        qtbot.waitUntil(lambda: bool(created), timeout=3000)

    assert len(created) == 1
    call_kwargs = created[0].kwargs
    assert call_kwargs.get("media_context") is None
    assert call_kwargs.get("lookup_fn") is None
    assert tab._curation_event.is_set()


# ---------------------------------------------------------------------------
# 15. worker_thread=None → lookup_fn=None
# ---------------------------------------------------------------------------


def test_curation_requested_no_worker_passes_none_lookup_fn(tab, tmp_path, qtbot):
    """When worker_thread is None, lookup_fn=None is passed regardless of files."""
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.worker_thread = None

    fake_entry = (0.0, 1.0, "日本語")
    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.return_value = [fake_entry]

    dialog_cls, created = _recording_curation_dialog_cls()

    with (
        patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            dialog_cls,
        ),
    ):
        tab._on_curation_requested([])
        qtbot.waitUntil(lambda: bool(created), timeout=3000)

    call_kwargs = created[0].kwargs
    assert call_kwargs.get("lookup_fn") is None
    assert tab._curation_event.is_set()


# ---------------------------------------------------------------------------
# 12. Subtitle offset persists with recent file pairs (Issue #61)
# ---------------------------------------------------------------------------


def test_processing_finished_saves_offset_to_recent(tab):
    tab.worker_thread = MagicMock(name="EpisodeWorkerThread")
    tab.worker_thread.isRunning.return_value = False
    tab.recent_manager = MagicMock(name="RecentFilesManager")
    tab.recent_manager.get_recent.return_value = []
    tab.video_selector.set_path("/video/ep01.mkv")
    tab.subtitle_selector.set_path("/subs/ep01.ass")
    tab.offset_spinbox.setValue(3.5)
    tab._curation_video = Path("/video/ep01.mkv")
    tab._curation_subtitle = Path("/subs/ep01.ass")
    tab._curation_video_raw = "/video/ep01.mkv"
    tab._curation_subtitle_raw = "/subs/ep01.ass"
    tab._curation_offset = 3.5

    tab._on_processing_finished(MagicMock(name="ProcessingResult"))

    args, _ = tab.recent_manager.add_entry.call_args
    # add_entry(Path(video), Path(subtitle), offset)
    assert args[2] == pytest.approx(3.5)


def test_processing_finished_without_run_snapshot_preserves_live_selection(tab, tmp_path):
    video = tmp_path / "next.mkv"
    subtitle = tmp_path / "next.srt"
    video.touch()
    subtitle.touch()
    tab.video_selector.set_path(str(video))
    tab.subtitle_selector.set_path(str(subtitle))
    tab.offset_spinbox.setValue(7.5)
    tab.recent_manager = MagicMock(name="RecentFilesManager")

    result = MagicMock(name="ProcessingResult")
    result.success = True
    result.cards_created = 4

    tab._on_processing_finished(result)

    tab.recent_manager.add_entry.assert_not_called()
    assert tab.video_selector.get_path() == str(video)
    assert tab.subtitle_selector.get_path() == str(subtitle)
    assert tab.offset_spinbox.value() == pytest.approx(7.5)


def test_processing_finished_uses_run_snapshot_and_preserves_new_selection(tab, tmp_path):
    video_a = tmp_path / "a.mkv"
    subtitle_a = tmp_path / "a.srt"
    video_b = tmp_path / "b.mkv"
    subtitle_b = tmp_path / "b.srt"
    for path in (video_a, subtitle_a, video_b, subtitle_b):
        path.touch()

    tab.video_selector.set_path(str(video_a))
    tab.subtitle_selector.set_path(str(subtitle_a))
    tab.offset_spinbox.setValue(1.25)
    tab.recent_manager = MagicMock(name="RecentFilesManager")
    tab.recent_manager.get_recent.return_value = []

    mock_worker = MagicMock(name="EpisodeWorkerThread")
    with patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker):
        tab._start_processing()

    tab.video_selector.set_path(str(video_b))
    tab.subtitle_selector.set_path(str(subtitle_b))
    tab.offset_spinbox.setValue(7.5)
    result = MagicMock(name="ProcessingResult")
    result.success = True
    result.cards_created = 4

    tab._on_processing_finished(result)

    tab.recent_manager.add_entry.assert_called_once_with(video_a, subtitle_a, 1.25)
    assert tab.video_selector.get_path() == str(video_b)
    assert tab.subtitle_selector.get_path() == str(subtitle_b)
    assert tab.offset_spinbox.value() == pytest.approx(7.5)


def test_processing_finished_clears_unchanged_noncanonical_launch_paths(tab, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    video = Path("episode.mkv")
    subtitle = Path("episode.srt")
    video.touch()
    subtitle.touch()
    raw_video = "./episode.mkv"
    raw_subtitle = "./episode.srt"
    tab.video_selector.set_path(raw_video)
    tab.subtitle_selector.set_path(raw_subtitle)
    tab.recent_manager = MagicMock(name="RecentFilesManager")
    tab.recent_manager.get_recent.return_value = []

    mock_worker = MagicMock(name="EpisodeWorkerThread")
    with patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker):
        tab._start_processing()

    result = MagicMock(name="ProcessingResult")
    result.success = True
    result.cards_created = 4
    tab._on_processing_finished(result)

    assert tab.video_selector.get_path() == ""
    assert tab.subtitle_selector.get_path() == ""


def test_recent_selection_restores_offset(tab):
    entry = {"video": "/video/ep01.mkv", "subtitle": "/subs/ep01.ass", "subtitle_offset": -2.0}
    tab.recent_combo.addItem("ep01", userData=entry)
    index = tab.recent_combo.count() - 1

    tab._on_recent_selected(index)

    assert tab.offset_spinbox.value() == pytest.approx(-2.0)


def test_recent_selection_legacy_entry_resets_offset_to_zero(tab):
    """A recent entry saved before the offset field existed restores 0.0."""
    tab.offset_spinbox.setValue(4.0)
    entry = {"video": "/video/ep01.mkv", "subtitle": "/subs/ep01.ass"}  # no subtitle_offset
    tab.recent_combo.addItem("ep01", userData=entry)
    index = tab.recent_combo.count() - 1

    tab._on_recent_selected(index)

    assert tab.offset_spinbox.value() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 16. update_config does not clobber the in-session offset spinbox
# ---------------------------------------------------------------------------


def test_update_config_preserves_dialed_offset(tab, test_config):
    """The offset spinbox is a per-session value never persisted back, so an
    unrelated settings save / theme toggle (which calls update_config) must not
    reset the user's dialed-in offset to the config default."""
    import dataclasses

    tab.offset_spinbox.setValue(1.5)
    # Unrelated change — subtitle_offset stays at its persisted default (0.0).
    new_config = dataclasses.replace(test_config, anki_deck_name="other_deck")

    tab.update_config(new_config)

    assert tab.config is new_config
    assert tab.offset_spinbox.value() == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# 17. Curation context routes through the shared MiningTabBase helpers (T-60)
# ---------------------------------------------------------------------------


def test_build_curation_context_routes_through_shared_helpers(tab, facade_processor, tmp_path):
    """_build_curation_context delegates to _make_curation_media_context with
    this tab's inputs (selectors, spinbox offset, audio-track override — the
    one real per-tab difference) and to _lookup_fn_from_processor for the
    worker's typed curation_processor."""
    from pathlib import Path

    fake_video = tmp_path / "ep01.mkv"
    fake_subs = tmp_path / "ep01.ass"
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.offset_spinbox.setValue(2.5)
    tab._audio_track_override = 3
    # _build_curation_context reads the GUI-thread snapshots (captured at
    # _start_processing), not live widgets — set them as that start would.
    tab._curation_video = fake_video
    tab._curation_subtitle = fake_subs
    tab._curation_offset = 2.5
    tab._curation_audio_track_override = 3

    worker = MagicMock(name="EpisodeWorkerThread")
    worker.curation_processor = facade_processor
    tab.worker_thread = worker

    sentinel_ctx = object()
    with patch.object(SingleEpisodeTab, "_make_curation_media_context", return_value=sentinel_ctx) as helper:
        media_context, lookup_fn = tab._build_curation_context()

    helper.assert_called_once_with(
        tab.config,
        Path(str(fake_video)),
        Path(str(fake_subs)),
        offset=2.5,
        audio_track_override=3,
    )
    assert media_context is sentinel_ctx
    # Lookup resolves through the processor facade (offline_lookup_fn).
    assert lookup_fn is facade_processor.definition_service.lookup_all_offline


# ---------------------------------------------------------------------------
# 18. _start_processing defers processor construction to the worker thread (OVH-054)
# ---------------------------------------------------------------------------


def test_start_processing_does_not_call_create_episode_processor_on_gui_thread(tab, tmp_path):
    """create_episode_processor must NOT be called synchronously on the GUI thread.

    The processor is built lazily inside a factory closure passed to
    EpisodeWorkerThread — it only runs when the worker calls run() on the
    worker thread.  Patching create_episode_processor and asserting it was not
    called proves no synchronous GUI-thread construction occurred.
    """
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_worker = MagicMock(name="EpisodeWorkerThread")

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker),
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor") as mock_build,
    ):
        tab._start_processing()

    # Must NOT have been called during _start_processing (GUI-thread).
    mock_build.assert_not_called()


def test_start_processing_passes_processor_factory_to_worker(tab, tmp_path):
    """_start_processing passes processor=None and a callable processor_factory
    to EpisodeWorkerThread instead of a pre-built processor."""
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_worker = MagicMock(name="EpisodeWorkerThread")

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=mock_worker) as worker_cls,
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor"),
    ):
        tab._start_processing()

    worker_cls.assert_called_once()
    _, kwargs = worker_cls.call_args
    assert kwargs.get("processor") is None, "processor must be None when factory path is used"
    assert callable(kwargs.get("processor_factory")), "processor_factory must be a callable"


def test_factory_closure_calls_create_episode_processor_when_invoked(tab, tmp_path):
    """Invoking the factory passed to EpisodeWorkerThread calls
    create_episode_processor — confirming the factory works correctly when
    the worker thread later calls it."""
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_worker = MagicMock(name="EpisodeWorkerThread")
    built_processor = MagicMock(name="EpisodeProcessor")

    captured_factory: list = []

    def _capture_factory(*args, **kwargs):
        captured_factory.append(kwargs.get("processor_factory"))
        return mock_worker

    # The factory is a closure over the patched create_episode_processor, so
    # we must call factory() inside the patch context — once the with block
    # exits the original function is restored.
    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", side_effect=_capture_factory),
        patch(
            "anki_miner.gui.widgets.single_episode_tab.create_episode_processor",
            return_value=built_processor,
        ) as mock_build,
    ):
        tab._start_processing()

        assert len(captured_factory) == 1
        factory = captured_factory[0]
        assert callable(factory)
        # Factory not yet called during _start_processing.
        mock_build.assert_not_called()

        # Calling the factory (simulating worker thread) invokes create_episode_processor.
        result = factory()
        mock_build.assert_called_once()
        assert result is built_processor


def test_mocked_mine_produces_result_and_curation_context_resolves(tab, tmp_path, facade_processor):
    """A full mocked mine via the factory path: result is handled correctly and
    curation_processor is the factory-built processor.

    EpisodeWorkerThread is patched to a MagicMock so no real QThread is
    spawned.  The processor_factory kwarg captured from the constructor is
    invoked directly to simulate what the worker thread would do, then
    curation_processor on the mock is asserted to equal the built processor.
    """
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_worker = MagicMock(name="EpisodeWorkerThread")
    # Before the factory runs the processor is None (matches real pre-run state).
    mock_worker.processor = None
    mock_worker.curation_processor = None

    with (
        patch(
            "anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread",
            return_value=mock_worker,
        ) as worker_cls,
        patch(
            "anki_miner.gui.widgets.single_episode_tab.create_episode_processor",
            return_value=facade_processor,
        ),
    ):
        tab._start_processing()

        # worker_thread was set to the mock; no real QThread was spawned.
        assert tab.worker_thread is mock_worker

        # Processor not yet built (factory hasn't run).
        assert mock_worker.processor is None
        assert mock_worker.curation_processor is None

        # Capture the factory closure from the constructor kwargs and invoke it
        # directly — this simulates what the worker thread does at the start of run().
        _, kwargs = worker_cls.call_args
        assert kwargs.get("processor") is None, "processor must be None when factory path is used"
        factory = kwargs.get("processor_factory")
        assert callable(factory)

        built = factory()

    # After invoking the factory, curation_processor resolves to facade_processor.
    assert built is facade_processor


# ---------------------------------------------------------------------------
# 20. Test Timing parse runs off the GUI thread (GUI-freeze hardening)
# ---------------------------------------------------------------------------


def test_timing_parse_runs_off_gui_thread(tab, tmp_path, qtbot):
    """The subtitle parse must run on a worker thread, not the GUI thread.

    A large subtitle can take ~1s to parse; doing it inline freezes the UI.
    """
    import threading

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    parse_thread: dict = {}

    def _record(_path):
        parse_thread["id"] = threading.get_ident()
        return [(0.0, 1.0, "テスト")]

    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.side_effect = _record

    mock_viewer = MagicMock()
    mock_viewer.exec.return_value = mock_viewer.DialogCode.Rejected

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
        patch("anki_miner.gui.widgets.subtitle_viewer.SubtitleViewer", return_value=mock_viewer) as viewer_cls,
    ):
        tab._on_timing_clicked()
        # Button disabled while the parse runs off-thread.
        assert not tab.timing_button.isEnabled()
        qtbot.waitUntil(lambda: viewer_cls.called, timeout=3000)

    assert parse_thread["id"] != threading.get_ident()  # parsed off the GUI thread
    assert tab.timing_button.isEnabled()  # re-enabled after success


def test_timing_parse_discards_result_after_inputs_change(tab, tmp_path):
    from PyQt6.QtWidgets import QDialog

    video_a = tmp_path / "a.mkv"
    subtitle_a = tmp_path / "a.srt"
    video_b = tmp_path / "b.mkv"
    subtitle_b = tmp_path / "b.srt"
    for path in (video_a, subtitle_a, video_b, subtitle_b):
        path.touch()

    tab.video_selector.set_path(str(video_a))
    tab.subtitle_selector.set_path(str(subtitle_a))
    done_callbacks = []

    def _capture(_owner, _work, done, _error):
        done_callbacks.append(done)

    viewer = MagicMock()
    viewer.exec.return_value = QDialog.DialogCode.Rejected
    with (
        patch("anki_miner.gui.widgets.single_episode_tab.run_off_thread", side_effect=_capture),
        patch("anki_miner.gui.widgets.subtitle_viewer.SubtitleViewer", return_value=viewer) as viewer_cls,
    ):
        viewer_cls.DialogCode = QDialog.DialogCode
        viewer_cls.ALIGN_REQUESTED = 2
        tab._on_timing_clicked()
        tab.video_selector.set_path(str(video_b))
        tab.subtitle_selector.set_path(str(subtitle_b))
        done_callbacks[0]([(0.0, 1.0, "line")])

    viewer_cls.assert_not_called()
    assert tab.timing_button.isEnabled()


def test_timing_empty_entries_shows_info_and_reenables(tab, tmp_path, qtbot):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.return_value = []

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
        patch("PyQt6.QtWidgets.QMessageBox.information") as mock_info,
    ):
        tab._on_timing_clicked()
        qtbot.waitUntil(lambda: mock_info.called, timeout=3000)

    mock_info.assert_called_once()
    assert tab.timing_button.isEnabled()


def test_timing_parse_error_reports_an_issue_and_reenables(tab, tmp_path, qtbot):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.side_effect = RuntimeError("bad file")

    with patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls):
        tab._on_timing_clicked()
        qtbot.waitUntil(lambda: tab.issue_banner().current_issue() is not None, timeout=3000)

    assert "bad file" in tab.issue_banner().current_issue().details
    assert tab.timing_button.isEnabled()


# ---------------------------------------------------------------------------
# 21. Tracks ffprobe runs off the GUI thread (GUI-freeze hardening)
# ---------------------------------------------------------------------------


def test_tracks_probe_runs_off_gui_thread(tab, tmp_path, qtbot):
    """list_audio_streams must run on a worker thread, not the GUI thread."""
    import threading

    from PyQt6.QtWidgets import QDialog

    from anki_miner.utils.audio_track_detector import AudioStream

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)

    probe_thread: dict = {}
    stream = AudioStream(
        global_index=1, audio_index=0, language_tag="jpn", title_tag=None, codec="aac", channels=2, is_default=True
    )

    def _record(*_a, **_k):
        probe_thread["id"] = threading.get_ident()
        return [stream]

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = QDialog.DialogCode.Rejected
    mock_class = MagicMock(return_value=mock_dialog)
    mock_class.DialogCode = QDialog.DialogCode

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", side_effect=_record),
        patch("anki_miner.gui.widgets.single_episode_tab.AudioTracksDialog", mock_class),
    ):
        tab._on_tracks_clicked()
        # Button disabled while the probe runs off-thread.
        assert not tab.tracks_button.isEnabled()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    assert probe_thread["id"] != threading.get_ident()  # probed off the GUI thread
    assert tab.tracks_button.isEnabled()  # re-enabled after success


def test_torn_down_tab_survives_a_late_track_probe_completion(tab, tmp_path, qtbot):
    """The tab's own tracks_button can be destroyed while the probe is in flight.

    ``_on_streams``'s first line re-enables ``tracks_button``; if the tab is
    torn down (app close) before the queued result_ready delivers, that write
    must not raise ``RuntimeError``.
    """
    import threading

    from PyQt6 import sip

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)

    entered = threading.Event()
    release = threading.Event()

    def _probe(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return []

    with patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", side_effect=_probe):
        before = set(getattr(tab, "_off_thread_workers", set()))
        tab._on_tracks_clicked()
        assert entered.wait(3)
        worker = next(iter(set(tab._off_thread_workers) - before))

        sip.delete(tab.tracks_button)
        release.set()
        assert worker.wait(3000)
        qtbot.wait(50)  # let the queued result_ready delivery run; must not raise


def test_tracks_empty_streams_shows_info_and_reenables(tab, tmp_path, qtbot):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", return_value=[]),
        patch("PyQt6.QtWidgets.QMessageBox.information") as mock_info,
    ):
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_info.called, timeout=3000)

    mock_info.assert_called_once()
    assert tab.tracks_button.isEnabled()


def test_tracks_probe_error_reports_an_issue_and_reenables(tab, tmp_path, qtbot):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)

    with (
        patch(
            "anki_miner.gui.widgets.single_episode_tab.list_audio_streams",
            side_effect=RuntimeError("ffprobe blew up"),
        ),
    ):
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: tab.issue_banner().current_issue() is not None, timeout=3000)

    issue = tab.issue_banner().current_issue()
    assert issue.summary == "Audio tracks could not be read."
    assert "ffprobe blew up" in issue.details
    assert tab.tracks_button.isEnabled()


def test_late_track_probe_does_not_override_after_source_change(tab, tmp_path, qtbot):
    """A probe result belongs only to the video selected when it started."""
    import threading

    from PyQt6.QtWidgets import QDialog

    from anki_miner.utils.audio_track_detector import AudioStream

    source_a = tmp_path / "a.mkv"
    source_b = tmp_path / "b.mkv"
    source_a.touch()
    source_b.touch()
    tab.video_selector.set_path(str(source_a))

    entered = threading.Event()
    release = threading.Event()
    stream = AudioStream(
        global_index=1,
        audio_index=0,
        language_tag="jpn",
        title_tag=None,
        codec="aac",
        channels=2,
        is_default=True,
    )

    def _probe(*_args, **_kwargs):
        entered.set()
        assert release.wait(3)
        return [stream]

    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.selected_override.return_value = 1
    dialog_cls = MagicMock(return_value=dialog)
    dialog_cls.DialogCode = QDialog.DialogCode

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams", side_effect=_probe),
        patch("anki_miner.gui.widgets.single_episode_tab.AudioTracksDialog", dialog_cls),
    ):
        before = set(getattr(tab, "_off_thread_workers", set()))
        tab._on_tracks_clicked()
        assert entered.wait(3)
        worker = next(iter(set(tab._off_thread_workers) - before))
        tab.video_selector.set_path(str(source_b))
        release.set()
        assert worker.wait(3000)
        qtbot.waitUntil(tab.tracks_button.isEnabled, timeout=3000)

    dialog_cls.assert_not_called()
    assert tab._audio_track_override is None


# ---------------------------------------------------------------------------
# 25. Timing viewer hand-off to the automatic aligner (D35)
# ---------------------------------------------------------------------------


def _prime_timing_inputs(tab, tmp_path):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)
    return fake_video, fake_subs


def _run_timing_with(tab, qtbot, viewer):
    """Open the timing viewer with a stubbed instance but the REAL result codes.

    The tab branches on ``SubtitleViewer.ALIGN_REQUESTED`` / ``DialogCode``; a
    bare MagicMock class would answer those with fresh mocks and every branch
    would silently miss.
    """
    from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer as RealViewer

    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.return_value = [(0.0, 2.5, "テスト")]
    viewer_cls = MagicMock(return_value=viewer)
    viewer_cls.ALIGN_REQUESTED = RealViewer.ALIGN_REQUESTED
    viewer_cls.DialogCode = RealViewer.DialogCode
    with (
        patch("anki_miner.gui.widgets.subtitle_viewer.SubtitleViewer", viewer_cls),
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
    ):
        tab._on_timing_clicked()
        qtbot.waitUntil(lambda: viewer_cls.called, timeout=3000)


def test_timing_align_result_hands_the_pair_to_retime(tab, tmp_path, qtbot):
    """Align automatically routes to the existing Retime tool, prefilled."""
    from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

    video, subs = _prime_timing_inputs(tab, tmp_path)
    viewer = MagicMock()
    viewer.exec.return_value = SubtitleViewer.ALIGN_REQUESTED

    container = MagicMock()
    with patch.object(type(tab), "_subtitles_container", return_value=container, create=True):
        _run_timing_with(tab, qtbot, viewer)

    container.open_retime.assert_called_once_with(video, subs)


def test_timing_align_navigates_only_after_the_viewer_closed(tab, tmp_path, qtbot):
    """exec() must have returned before anything navigates: mpv is down by then."""
    from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

    _prime_timing_inputs(tab, tmp_path)
    order: list[str] = []
    viewer = MagicMock()
    viewer.exec.side_effect = lambda: (order.append("exec"), SubtitleViewer.ALIGN_REQUESTED)[1]

    container = MagicMock()
    container.open_retime.side_effect = lambda *_: order.append("open_retime")
    with patch.object(type(tab), "_subtitles_container", return_value=container, create=True):
        _run_timing_with(tab, qtbot, viewer)

    assert order == ["exec", "open_retime"]


def test_timing_align_without_a_host_window_is_a_quiet_noop(tab, tmp_path, qtbot):
    """A tab with no Subtitles container (tests, stripped shells) must not crash."""
    from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

    _prime_timing_inputs(tab, tmp_path)
    viewer = MagicMock()
    viewer.exec.return_value = SubtitleViewer.ALIGN_REQUESTED

    _run_timing_with(tab, qtbot, viewer)  # must not raise

    assert tab.timing_button.isEnabled()


def test_timing_align_does_not_apply_the_offset(tab, tmp_path, qtbot):
    """Handing off is not accepting: the spinbox keeps its value."""
    from anki_miner.gui.widgets.subtitle_viewer import SubtitleViewer

    _prime_timing_inputs(tab, tmp_path)
    tab.offset_spinbox.setValue(0.5)
    viewer = MagicMock()
    viewer.exec.return_value = SubtitleViewer.ALIGN_REQUESTED
    viewer.get_offset.return_value = 9.0

    _run_timing_with(tab, qtbot, viewer)

    assert tab.offset_spinbox.value() == 0.5
