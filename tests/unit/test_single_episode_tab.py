"""Tests for SingleEpisodeTab audio track override wiring (Issue #35)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, test_config):
    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    yield widget
    widget.deleteLater()


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------


def test_initial_audio_track_override_is_none(tab):
    assert tab._audio_track_override is None


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
    with (
        patch("anki_miner.gui.widgets.single_episode_tab.list_audio_streams") as mock_list,
        patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn,
    ):
        tab.video_selector.get_path = MagicMock(return_value="")
        tab._on_tracks_clicked()
        mock_warn.assert_called_once()
        mock_list.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Dialog opens; override stored on Accept
# ---------------------------------------------------------------------------


def test_tracks_clicked_stores_override_on_accept(tab, tmp_path):
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


def test_tracks_clicked_keeps_override_on_cancel(tab, tmp_path):
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

    assert tab._audio_track_override == 0


# ---------------------------------------------------------------------------
# 7. Tracks probe passes the resolved ffprobe binary
# ---------------------------------------------------------------------------


def test_tracks_clicked_passes_resolved_ffprobe(qapp, test_config, tmp_path):
    import dataclasses

    from anki_miner.utils import ffmpeg_resolver

    fake_ffprobe = tmp_path / "my_ffprobe"
    fake_ffprobe.write_text("#!/bin/sh\n")
    cfg = dataclasses.replace(test_config, ffprobe_location=str(fake_ffprobe))

    widget = SingleEpisodeTab(
        config=cfg,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
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
        tab._start_processing(preview_mode=False)

    mock_worker_cls.assert_called_once()
    _, kwargs = mock_worker_cls.call_args
    assert kwargs.get("audio_track_override") == 1


# ---------------------------------------------------------------------------
# 8. _on_timing_clicked passes override to SubtitleViewer
# ---------------------------------------------------------------------------


def test_timing_clicked_passes_override_to_subtitle_viewer(tab, tmp_path):
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

    result = MagicMock(name="ProcessingResult")
    tab._on_processing_finished(result)
    assert tab._audio_track_override is None


# ---------------------------------------------------------------------------
# 10. Override resets after process error
# ---------------------------------------------------------------------------


def test_override_resets_after_processing_error(tab):
    tab._audio_track_override = 1
    tab.worker_thread = MagicMock(name="EpisodeWorkerThread")
    tab.worker_thread.isRunning.return_value = False

    tab._on_processing_error("Something went wrong")
    assert tab._audio_track_override is None


# ---------------------------------------------------------------------------
# 11. Inline auto-stream lookup uses JAPANESE_LANGUAGE_CODES
# ---------------------------------------------------------------------------


def test_tracks_clicked_auto_detected_uses_inline_lookup(tab, tmp_path):
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
        tab._start_processing(preview_mode=False)

    assert tab.timing_button.isHidden(), "timing_button should be hidden during processing"
    assert tab.tracks_button.isHidden(), "tracks_button should be hidden during processing"

    tab._restore_buttons()

    assert not tab.timing_button.isHidden(), "timing_button should not be hidden after restore"
    assert not tab.tracks_button.isHidden(), "tracks_button should not be hidden after restore"


# ---------------------------------------------------------------------------
# 13. _on_curation_requested passes media_context and lookup_fn to dialog
# ---------------------------------------------------------------------------


def test_curation_requested_passes_media_context_and_lookup_fn(tab, tmp_path):
    """Dialog receives a CurationMediaContext and lookup_fn when files are set
    and a worker with a live processor is present."""
    from PyQt6.QtWidgets import QDialog

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.offset_spinbox.setValue(1.5)

    # Fake worker with processor.definition_service.lookup_all_offline
    fake_lookup = MagicMock(name="lookup_all_offline")
    fake_def_svc = MagicMock()
    fake_def_svc.lookup_all_offline = fake_lookup
    fake_proc = MagicMock()
    fake_proc.definition_service = fake_def_svc
    fake_worker = MagicMock()
    fake_worker.processor = fake_proc
    tab.worker_thread = fake_worker

    fake_entry = (0.0, 2.5, "テスト")
    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.return_value = [fake_entry]

    mock_dialog_instance = MagicMock()
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog_instance.DialogCode = QDialog.DialogCode
    mock_dialog_instance.get_selected_words.return_value = []
    mock_dialog_cls = MagicMock(return_value=mock_dialog_instance)
    mock_dialog_cls.DialogCode = QDialog.DialogCode

    words: list = []
    with (
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
        patch("anki_miner.gui.widgets._mining_tab_base.WordCurationDialog", mock_dialog_cls),
    ):
        tab._on_curation_requested(words)

    mock_dialog_cls.assert_called_once()
    call_args, call_kwargs = mock_dialog_cls.call_args
    assert call_kwargs.get("lookup_fn") is fake_lookup
    ctx = call_kwargs.get("media_context")
    assert ctx is not None
    assert ctx.video_file == fake_video
    assert ctx.subtitle_entries == [fake_entry]
    assert ctx.offset == pytest.approx(1.5)
    assert ctx.audio_track_override == tab._audio_track_override
    # Default config has no ffprobe override → bare literal forwarded into the context.
    assert ctx.ffprobe_cmd == "ffprobe"
    # Curation event must be set so the worker-thread mock can proceed
    assert tab._curation_event.is_set()


def test_curation_media_context_uses_resolved_ffprobe(qapp, test_config, tmp_path):
    """CurationMediaContext carries the resolved ffprobe path when config overrides it."""
    import dataclasses

    from PyQt6.QtWidgets import QDialog

    from anki_miner.utils import ffmpeg_resolver

    fake_ffprobe = tmp_path / "my_ffprobe"
    fake_ffprobe.write_text("#!/bin/sh\n")
    cfg = dataclasses.replace(test_config, ffprobe_location=str(fake_ffprobe))

    tab = SingleEpisodeTab(
        config=cfg,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    try:
        ffmpeg_resolver._clear_cache()
        tab._init_worker_thread = MagicMock()

        fake_video = tmp_path / "ep01.mkv"
        fake_video.touch()
        fake_subs = tmp_path / "ep01.ass"
        fake_subs.touch()
        tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
        tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))

        fake_entry = (0.0, 2.5, "テスト")
        mock_parser_cls = MagicMock()
        mock_parser_cls.return_value.parse_raw_entries.return_value = [fake_entry]

        mock_dialog_instance = MagicMock()
        mock_dialog_instance.exec.return_value = QDialog.DialogCode.Rejected
        mock_dialog_instance.DialogCode = QDialog.DialogCode
        mock_dialog_cls = MagicMock(return_value=mock_dialog_instance)
        mock_dialog_cls.DialogCode = QDialog.DialogCode

        with (
            patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
            patch("anki_miner.gui.widgets._mining_tab_base.WordCurationDialog", mock_dialog_cls),
        ):
            tab._on_curation_requested([])

        _, call_kwargs = mock_dialog_cls.call_args
        ctx = call_kwargs.get("media_context")
        assert ctx is not None
        assert ctx.ffprobe_cmd == str(fake_ffprobe)
    finally:
        ffmpeg_resolver._clear_cache()
        tab.deleteLater()


# ---------------------------------------------------------------------------
# 14. Subtitle parse failure → media_context=None, dialog still constructed
# ---------------------------------------------------------------------------


def test_curation_requested_parse_error_passes_none_media_context(tab, tmp_path):
    """When subtitle parsing raises, dialog is still called with media_context=None."""
    from PyQt6.QtWidgets import QDialog

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))

    # Parser raises on parse_raw_entries
    mock_parser_cls = MagicMock()
    mock_parser_cls.return_value.parse_raw_entries.side_effect = RuntimeError("bad file")

    mock_dialog_instance = MagicMock()
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Rejected
    mock_dialog_instance.DialogCode = QDialog.DialogCode
    mock_dialog_cls = MagicMock(return_value=mock_dialog_instance)
    mock_dialog_cls.DialogCode = QDialog.DialogCode

    tab.worker_thread = None  # no worker — lookup_fn will also be None

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            mock_dialog_cls,
        ),
    ):
        tab._on_curation_requested([])

    mock_dialog_cls.assert_called_once()
    _, call_kwargs = mock_dialog_cls.call_args
    assert call_kwargs.get("media_context") is None
    assert call_kwargs.get("lookup_fn") is None
    assert tab._curation_event.is_set()


# ---------------------------------------------------------------------------
# 15. worker_thread=None → lookup_fn=None
# ---------------------------------------------------------------------------


def test_curation_requested_no_worker_passes_none_lookup_fn(tab, tmp_path):
    """When worker_thread is None, lookup_fn=None is passed regardless of files."""
    from PyQt6.QtWidgets import QDialog

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

    mock_dialog_instance = MagicMock()
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Rejected
    mock_dialog_instance.DialogCode = QDialog.DialogCode
    mock_dialog_cls = MagicMock(return_value=mock_dialog_instance)
    mock_dialog_cls.DialogCode = QDialog.DialogCode

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            mock_dialog_cls,
        ),
    ):
        tab._on_curation_requested([])

    _, call_kwargs = mock_dialog_cls.call_args
    assert call_kwargs.get("lookup_fn") is None
    assert tab._curation_event.is_set()
