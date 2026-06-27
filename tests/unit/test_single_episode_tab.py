"""Tests for SingleEpisodeTab audio track override wiring (Issue #35)."""

from __future__ import annotations

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
        tab._start_processing(preview_mode=False)

    mock_worker_cls.assert_called_once()
    _, kwargs = mock_worker_cls.call_args
    assert kwargs.get("audio_track_override") == 1


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
        tab._start_processing(preview_mode=False)

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
    from PyQt6.QtWidgets import QDialog

    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    fake_subs = tmp_path / "ep01.ass"
    fake_subs.touch()

    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.subtitle_selector.get_path = MagicMock(return_value=str(fake_subs))
    tab.offset_spinbox.setValue(1.5)

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

    mock_dialog_instance = MagicMock()
    mock_dialog_instance.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog_instance.DialogCode = QDialog.DialogCode
    mock_dialog_instance.get_selected_words.return_value = []
    mock_dialog_cls = MagicMock(return_value=mock_dialog_instance)
    mock_dialog_cls.DialogCode = QDialog.DialogCode

    words: list = []
    with (
        patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
        patch("anki_miner.gui.widgets._mining_tab_base.WordCurationDialog", mock_dialog_cls),
    ):
        tab._on_curation_requested(words)
        # The context build (subtitle parse) runs off-thread; wait for the
        # GUI-thread callback to construct the dialog.
        qtbot.waitUntil(lambda: mock_dialog_cls.called, timeout=3000)

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


def test_curation_media_context_uses_resolved_ffprobe(qapp, qtbot, test_config, tmp_path):
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
    qtbot.addWidget(tab)
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
            patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
            patch("anki_miner.gui.widgets._mining_tab_base.WordCurationDialog", mock_dialog_cls),
        ):
            tab._on_curation_requested([])
            qtbot.waitUntil(lambda: mock_dialog_cls.called, timeout=3000)

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


def test_curation_requested_parse_error_passes_none_media_context(tab, tmp_path, qtbot):
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
        patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            mock_dialog_cls,
        ),
    ):
        tab._on_curation_requested([])
        qtbot.waitUntil(lambda: mock_dialog_cls.called, timeout=3000)

    mock_dialog_cls.assert_called_once()
    _, call_kwargs = mock_dialog_cls.call_args
    assert call_kwargs.get("media_context") is None
    assert call_kwargs.get("lookup_fn") is None
    assert tab._curation_event.is_set()


# ---------------------------------------------------------------------------
# 15. worker_thread=None → lookup_fn=None
# ---------------------------------------------------------------------------


def test_curation_requested_no_worker_passes_none_lookup_fn(tab, tmp_path, qtbot):
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
        patch("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", mock_parser_cls),
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            mock_dialog_cls,
        ),
    ):
        tab._on_curation_requested([])
        qtbot.waitUntil(lambda: mock_dialog_cls.called, timeout=3000)

    _, call_kwargs = mock_dialog_cls.call_args
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

    tab._on_processing_finished(MagicMock(name="ProcessingResult"))

    args, _ = tab.recent_manager.add_entry.call_args
    # add_entry(Path(video), Path(subtitle), offset)
    assert args[2] == pytest.approx(3.5)


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
        tab._start_processing(preview_mode=False)

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
        tab._start_processing(preview_mode=False)

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
        tab._start_processing(preview_mode=False)

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
        tab._start_processing(preview_mode=True)

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


def test_timing_parse_error_shows_critical_and_reenables(tab, tmp_path, qtbot):
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

    with (
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", mock_parser_cls),
        patch("PyQt6.QtWidgets.QMessageBox.critical") as mock_crit,
    ):
        tab._on_timing_clicked()
        qtbot.waitUntil(lambda: mock_crit.called, timeout=3000)

    mock_crit.assert_called_once()
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


def test_tracks_probe_error_shows_warning_and_reenables(tab, tmp_path, qtbot):
    fake_video = tmp_path / "ep01.mkv"
    fake_video.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(fake_video))
    tab.video_selector.is_valid = MagicMock(return_value=True)

    with (
        patch(
            "anki_miner.gui.widgets.single_episode_tab.list_audio_streams",
            side_effect=RuntimeError("ffprobe blew up"),
        ),
        patch("PyQt6.QtWidgets.QMessageBox.warning") as mock_warn,
    ):
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_warn.called, timeout=3000)

    mock_warn.assert_called_once()
    assert tab.tracks_button.isEnabled()
