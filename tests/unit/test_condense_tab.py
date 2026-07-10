"""Tests for CondenseTab (Audio Condenser GUI tab).

Covers:
- Construction (qtbot.addWidget contract).
- ffmpeg-availability guard: present → Condense enabled, notice hidden;
  absent → button disabled, notice visible.
- Mode toggle (single media+subtitle selectors + track rows vs folder selectors).
- Single-mode item collection: media set → [CondenseItem]; missing media → warning;
  audio-only media accepted; explicit-sub picked disables the subtitle-track row.
- Folder-mode item collection: with subtitle folder → episode-number pairing
  (patched matcher) + "Matched N of M" logged; without → per-file auto-detect scan.
- Worker kwargs assembled from widget/config state (monkeypatched CondenseWorker).
- Output-location toggling (Choose Folder / Reset), unwritable-output abort.
- Cancel flips buttons + calls worker.cancel; iter_close_workers; reentrancy guard.
- update_config refreshes option defaults when idle, NOT during a run.

No real ffmpeg/ffprobe runs: CondenseWorker and the availability check are patched.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.condense_tab import (
    CONDENSE_AUDIO_EXTENSIONS,
    CONDENSE_MEDIA_EXTENSIONS,
    CondenseTab,
)
from anki_miner.gui.workers.condense_worker import CondenseItem
from anki_miner.utils.file_pairing import FilePair

# ---------------------------------------------------------------------------
# Patch-target constants
# ---------------------------------------------------------------------------

_AVAILABLE = "anki_miner.gui.widgets.condense_tab.CondenseTab._ffmpeg_available"
_OS_ACCESS = "anki_miner.gui.widgets.condense_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.condense_tab.CondenseWorker"
_FIND_PAIRS = "anki_miner.gui.widgets.condense_tab.FilePairMatcher.find_pairs_by_episode_number"
_WARN = "anki_miner.gui.widgets.condense_tab.QMessageBox.warning"
_LIST_AUDIO = "anki_miner.gui.widgets.condense_tab.list_audio_streams"
_LIST_SUBS = "anki_miner.gui.widgets.condense_tab.list_subtitle_streams"
_AUDIO_DIALOG = "anki_miner.gui.widgets.condense_tab.AudioTracksDialog"
_SUB_DIALOG = "anki_miner.gui.widgets.condense_tab.SubtitleTracksDialog"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path) -> AnkiMinerConfig:
    """Return a minimal config with writable paths under tmp_path."""
    return AnkiMinerConfig(
        asr_models_root=tmp_path / "asr_models",
        media_temp_folder=tmp_path / "tmp",
    )


class _FakeWorker:
    """Minimal fake mimicking the CondenseWorker interface used by the tab."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.file_started = MagicMock()
        self.file_progress = MagicMock()
        self.file_finished = MagicMock()
        self.file_skipped = MagicMock()
        self.queue_finished = MagicMock()
        self.finished = MagicMock()  # native QThread.finished (lifecycle release)
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


def _make_tab(config, qtbot):
    """Construct a CondenseTab with ffmpeg patched available=True."""
    with patch(_AVAILABLE, return_value=True):
        tab = CondenseTab(config)
    qtbot.addWidget(tab)
    return tab


def _start_condense(tab, fake_worker):
    """Click Condense with availability + writability patched; return the worker mock."""
    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker) as worker_cls,
    ):
        tab.condense_button.click()
    return worker_cls


def _capture_signal_slots(signal_mock):
    """Capture slots connected to a _FakeWorker MagicMock signal; return the list."""
    slots: list = []
    original_connect = signal_mock.connect

    def _capture(slot):
        slots.append(slot)
        return original_connect(slot)

    signal_mock.connect = _capture
    return slots


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction(qtbot, tmp_path):
    """Tab constructs and registers with qtbot without error."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.condense_button is not None
    assert tab.worker_thread is None


def test_update_config_swaps_config(qtbot, tmp_path):
    """update_config adopts the new config."""
    import dataclasses

    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)

    new_config = dataclasses.replace(config, ffmpeg_location="/some/path")
    with patch(_AVAILABLE, return_value=True):
        tab.update_config(new_config)

    assert tab.config is new_config


def test_media_extensions_include_audio(qtbot, tmp_path):
    """Condenser media set includes audio-only containers (D12)."""
    assert ".mp3" in CONDENSE_MEDIA_EXTENSIONS
    assert ".flac" in CONDENSE_MEDIA_EXTENSIONS
    assert CONDENSE_AUDIO_EXTENSIONS <= CONDENSE_MEDIA_EXTENSIONS


# ---------------------------------------------------------------------------
# Availability guard
# ---------------------------------------------------------------------------


def test_ffmpeg_present_enables_condense(qtbot, tmp_path):
    """ffmpeg present → Condense enabled, notice hidden."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.condense_button.isEnabled()
    assert tab.engine_notice_label.isHidden()


def test_ffmpeg_absent_disables_condense(qtbot, tmp_path):
    """ffmpeg absent → Condense disabled, notice visible."""
    config = _make_config(tmp_path)
    with patch(_AVAILABLE, return_value=False):
        tab = CondenseTab(config)
    qtbot.addWidget(tab)
    assert not tab.condense_button.isEnabled()
    assert not tab.engine_notice_label.isHidden()


def test_ffmpeg_available_via_path_check(qtbot, tmp_path):
    """Both binaries resolve to PATH literals → shutil.which decides availability."""
    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.condense_tab.resolve_ffmpeg", return_value="ffmpeg"),
        patch("anki_miner.gui.widgets.condense_tab.resolve_ffprobe", return_value="ffprobe"),
        patch("anki_miner.gui.widgets.condense_tab.shutil.which", return_value="/usr/bin/x"),
    ):
        tab = CondenseTab(config)
    qtbot.addWidget(tab)
    assert tab.condense_button.isEnabled()


def test_ffmpeg_unavailable_via_path_check(qtbot, tmp_path):
    """A PATH literal that shutil.which cannot find → unavailable."""
    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.condense_tab.resolve_ffmpeg", return_value="ffmpeg"),
        patch("anki_miner.gui.widgets.condense_tab.resolve_ffprobe", return_value="ffprobe"),
        patch("anki_miner.gui.widgets.condense_tab.shutil.which", return_value=None),
    ):
        tab = CondenseTab(config)
    qtbot.addWidget(tab)
    assert not tab.condense_button.isEnabled()


# ---------------------------------------------------------------------------
# Mode toggle
# ---------------------------------------------------------------------------


def test_mode_toggle_single_by_default(qtbot, tmp_path):
    """Single-file mode is the default; folder selectors hidden, track rows shown."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert not tab.media_file_selector.isHidden()
    assert not tab.subtitle_file_selector.isHidden()
    assert not tab.audio_track_row_widget.isHidden()
    assert not tab.subtitle_track_row_widget.isHidden()
    assert tab.media_folder_selector.isHidden()
    assert tab.subtitle_folder_selector.isHidden()


def test_mode_toggle_switches_to_folder(qtbot, tmp_path):
    """Folder mode shows folder selectors, hides file selectors + track rows."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    assert tab.media_file_selector.isHidden()
    assert tab.subtitle_file_selector.isHidden()
    assert tab.audio_track_row_widget.isHidden()
    assert tab.subtitle_track_row_widget.isHidden()
    assert not tab.media_folder_selector.isHidden()
    assert not tab.subtitle_folder_selector.isHidden()


def test_mode_toggle_back_to_file(qtbot, tmp_path):
    """Toggling back to file mode re-shows file selectors + track rows."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    tab.file_mode_button.click()
    assert not tab.media_file_selector.isHidden()
    assert not tab.audio_track_row_widget.isHidden()
    assert not tab.subtitle_track_row_widget.isHidden()
    assert tab.media_folder_selector.isHidden()


def test_subtitle_track_row_disabled_when_explicit_sub_picked(qtbot, tmp_path):
    """Picking an explicit subtitle file disables the embedded-track row."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.subtitle_track_row_widget.isEnabled()

    tab.subtitle_file_selector.set_path(str(tmp_path / "episode.srt"))
    assert not tab.subtitle_track_row_widget.isEnabled()

    tab.subtitle_file_selector.set_path("")
    assert tab.subtitle_track_row_widget.isEnabled()


# ---------------------------------------------------------------------------
# Single-mode item collection
# ---------------------------------------------------------------------------


def test_single_mode_collects_item_without_sub(qtbot, tmp_path):
    """Media set, no subtitle → [CondenseItem(media, None)]."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    items = tab._collect_items()
    assert items == [CondenseItem(media, None)]


def test_single_mode_collects_item_with_sub(qtbot, tmp_path):
    """Media + subtitle set → [CondenseItem(media, sub)]."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    sub = tmp_path / "episode.srt"
    media.write_bytes(b"fake")
    sub.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))
    tab.subtitle_file_selector.set_path(str(sub))

    items = tab._collect_items()
    assert items == [CondenseItem(media, sub)]


def test_single_mode_accepts_audio_only_file(qtbot, tmp_path):
    """An audio-only .mp3 is accepted as media (D12)."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mp3"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    items = tab._collect_items()
    assert items == [CondenseItem(media, None)]


def test_single_mode_missing_media_warns(qtbot, tmp_path):
    """No media selected → warning, returns []."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    with patch(_WARN) as warn:
        items = tab._collect_items()
    warn.assert_called_once()
    assert items == []


def test_single_mode_nonexistent_media_warns(qtbot, tmp_path):
    """Media path that is not a file → warning, returns []."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.media_file_selector.set_path(str(tmp_path / "nope.mkv"))
    with patch(_WARN) as warn:
        items = tab._collect_items()
    warn.assert_called_once()
    assert items == []


def test_single_mode_nonexistent_sub_warns(qtbot, tmp_path):
    """An explicit subtitle path that is not a file → warning, returns []."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))
    tab.subtitle_file_selector.set_path(str(tmp_path / "nope.srt"))

    with patch(_WARN) as warn:
        items = tab._collect_items()
    warn.assert_called_once()
    assert items == []


# ---------------------------------------------------------------------------
# Folder-mode item collection
# ---------------------------------------------------------------------------


def test_folder_mode_auto_scans_media(qtbot, tmp_path):
    """Folder mode, no subtitle folder → sorted media scan, non-media excluded."""
    config = _make_config(tmp_path)
    media_folder = tmp_path / "media"
    media_folder.mkdir()
    m1 = media_folder / "ep01.mkv"
    m2 = media_folder / "ep02.mp3"  # audio-only accepted
    junk = media_folder / "notes.txt"
    for p in (m1, m2, junk):
        p.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.media_folder_selector.set_path(str(media_folder))

    items = tab._collect_items()
    assert items == [CondenseItem(m1, None), CondenseItem(m2, None)]


def test_folder_mode_empty_folder_warns(qtbot, tmp_path):
    """Folder mode with no media files → warning, returns []."""
    config = _make_config(tmp_path)
    media_folder = tmp_path / "media"
    media_folder.mkdir()
    (media_folder / "notes.txt").write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.media_folder_selector.set_path(str(media_folder))

    with patch(_WARN) as warn:
        items = tab._collect_items()
    warn.assert_called_once()
    assert items == []


def test_folder_mode_missing_media_folder_warns(qtbot, tmp_path):
    """No media folder selected → warning, returns []."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    with patch(_WARN) as warn:
        items = tab._collect_items()
    warn.assert_called_once()
    assert items == []


def test_folder_mode_with_subfolder_pairs_and_logs(qtbot, tmp_path):
    """Folder mode + subtitle folder → episode pairing + 'Matched N of M' logged."""
    config = _make_config(tmp_path)
    media_folder = tmp_path / "media"
    sub_folder = tmp_path / "subs"
    media_folder.mkdir()
    sub_folder.mkdir()
    m1 = media_folder / "ep01.mkv"
    m2 = media_folder / "ep02.mkv"
    m1.write_bytes(b"fake")
    m2.write_bytes(b"fake")
    s1 = sub_folder / "ep01.srt"
    s2 = sub_folder / "ep02.srt"
    s1.write_text("1\n")
    s2.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.media_folder_selector.set_path(str(media_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    fake_pairs = [FilePair(m1, s1), FilePair(m2, s2)]
    with patch(_FIND_PAIRS, return_value=fake_pairs) as find_pairs:
        items = tab._collect_items()

    assert items == [CondenseItem(m1, s1), CondenseItem(m2, s2)]
    # Condenser extension sets are forwarded to the matcher.
    assert find_pairs.call_args.kwargs["video_extensions"] is CONDENSE_MEDIA_EXTENSIONS
    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Matched 2 of 2" in log_text


def test_folder_mode_subfolder_unmatched_logs_warning(qtbot, tmp_path):
    """Fewer matches than media files logs a warning line."""
    config = _make_config(tmp_path)
    media_folder = tmp_path / "media"
    sub_folder = tmp_path / "subs"
    media_folder.mkdir()
    sub_folder.mkdir()
    m1 = media_folder / "ep01.mkv"
    m2 = media_folder / "ep02.mkv"
    m1.write_bytes(b"fake")
    m2.write_bytes(b"fake")
    s1 = sub_folder / "ep01.srt"
    s1.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.media_folder_selector.set_path(str(media_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    with patch(_FIND_PAIRS, return_value=[FilePair(m1, s1)]):
        items = tab._collect_items()

    assert items == [CondenseItem(m1, s1)]
    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Matched 1 of 2" in log_text
    assert "could not be matched" in log_text


def test_folder_mode_subfolder_no_pairs_warns(qtbot, tmp_path):
    """Subtitle folder with no matched pairs → warning, returns []."""
    config = _make_config(tmp_path)
    media_folder = tmp_path / "media"
    sub_folder = tmp_path / "subs"
    media_folder.mkdir()
    sub_folder.mkdir()
    (media_folder / "ep01.mkv").write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.media_folder_selector.set_path(str(media_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    with patch(_FIND_PAIRS, return_value=[]), patch(_WARN) as warn:
        items = tab._collect_items()

    warn.assert_called_once()
    assert items == []


# ---------------------------------------------------------------------------
# Worker kwargs assembly
# ---------------------------------------------------------------------------


def test_worker_kwargs_from_widget_state_single(qtbot, tmp_path):
    """Single-mode Condense forwards widget/config state as worker kwargs."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    sub = tmp_path / "episode.srt"
    media.write_bytes(b"fake")
    sub.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))
    tab.subtitle_file_selector.set_path(str(sub))
    tab.padding_spinbox.setValue(700)
    tab.offset_spinbox.setValue(-250)
    tab.format_combo.setCurrentIndex(tab.format_combo.findData("opus"))
    tab.write_subs_checkbox.setChecked(True)
    tab.overwrite_checkbox.setChecked(True)

    fake_worker = _FakeWorker()
    worker_cls = _start_condense(tab, fake_worker)

    assert worker_cls.call_count == 1
    args, kwargs = worker_cls.call_args
    assert args[0] is config
    assert args[1] == [CondenseItem(media, sub)]
    assert kwargs["output_dir"] is None
    assert kwargs["overwrite"] is True
    assert kwargs["padding_ms"] == 700
    assert kwargs["offset_ms"] == -250
    assert kwargs["output_format"] == "opus"
    assert kwargs["bitrate_kbps"] == 96
    assert kwargs["filtered_chars"] == "♪♫♬♩〜～"
    assert kwargs["write_subs"] is True
    assert kwargs["audio_track_override"] is None
    assert kwargs["subtitle_track_override"] is None
    assert fake_worker._started
    assert not tab.condense_button.isEnabled()


def test_worker_kwargs_single_track_overrides_forwarded(qtbot, tmp_path):
    """Single-mode track overrides are forwarded to the worker."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))
    tab._audio_track_override = 2
    tab._subtitle_track_override = 1

    fake_worker = _FakeWorker()
    worker_cls = _start_condense(tab, fake_worker)

    kwargs = worker_cls.call_args.kwargs
    assert kwargs["audio_track_override"] == 2
    assert kwargs["subtitle_track_override"] == 1


def test_worker_track_overrides_none_in_folder_mode(qtbot, tmp_path):
    """Folder mode ignores single-mode track overrides (auto-detect per file)."""
    config = _make_config(tmp_path)
    media_folder = tmp_path / "media"
    media_folder.mkdir()
    m1 = media_folder / "ep01.mkv"
    m1.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    # Leave a stale single-mode override, then switch to folder mode.
    tab._audio_track_override = 3
    tab._subtitle_track_override = 4
    tab.folder_mode_button.click()
    tab.media_folder_selector.set_path(str(media_folder))

    fake_worker = _FakeWorker()
    worker_cls = _start_condense(tab, fake_worker)

    args, kwargs = worker_cls.call_args
    assert args[1] == [CondenseItem(m1, None)]
    assert kwargs["audio_track_override"] is None
    assert kwargs["subtitle_track_override"] is None


def test_custom_output_dir_forwarded(qtbot, tmp_path):
    """A chosen output folder is forwarded as output_dir."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")
    out = tmp_path / "out"
    out.mkdir()

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))
    tab._custom_output_dir = out

    fake_worker = _FakeWorker()
    worker_cls = _start_condense(tab, fake_worker)

    assert worker_cls.call_args.kwargs["output_dir"] == out


def test_unwritable_output_aborts_no_worker(qtbot, tmp_path):
    """Unwritable output dir aborts before a worker is created."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=False),
        patch(_WORKER_CLS, side_effect=AssertionError("worker must not be created")),
    ):
        tab.condense_button.click()

    assert tab.worker_thread is None
    assert "not writable" in tab.log_widget.text_edit.toPlainText()


def test_second_condense_refused_while_running(qtbot, tmp_path):
    """A second Condense while the worker runs must not start a new one."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker) as worker_cls,
    ):
        tab.condense_button.click()  # starts worker (isRunning → True)
        tab.condense_button.setEnabled(True)  # simulate premature re-enable
        tab.condense_button.click()

    assert worker_cls.call_count == 1
    assert tab.worker_thread is fake_worker


# ---------------------------------------------------------------------------
# Output-location toggle
# ---------------------------------------------------------------------------


def test_choose_output_sets_label_and_shows_reset(qtbot, tmp_path):
    """Choosing a folder updates the label and reveals the Reset button."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    out = tmp_path / "out"
    out.mkdir()

    with patch(
        "anki_miner.gui.widgets.condense_tab.QFileDialog.getExistingDirectory",
        return_value=str(out),
    ):
        tab._on_choose_output()

    assert tab._custom_output_dir == out
    assert str(out) in tab.output_location_label.text()
    assert not tab.clear_output_button.isHidden()


def test_clear_output_resets_label(qtbot, tmp_path):
    """Reset clears the custom dir and restores the default label."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab._custom_output_dir = tmp_path / "out"
    tab.clear_output_button.show()

    tab._on_clear_output()
    assert tab._custom_output_dir is None
    assert "Next to source" in tab.output_location_label.text()
    assert tab.clear_output_button.isHidden()


# ---------------------------------------------------------------------------
# Cancel + lifecycle
# ---------------------------------------------------------------------------


def test_cancel_flips_buttons_and_calls_worker_cancel(qtbot, tmp_path):
    """Cancel disables itself, relabels, and calls worker.cancel()."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    _start_condense(tab, fake_worker)
    assert not tab.cancel_button.isHidden()

    tab._on_cancel()
    assert fake_worker._cancelled
    assert not tab.cancel_button.isEnabled()
    assert tab._cancelled is True


def test_queue_finished_re_enables_condense(qtbot, tmp_path):
    """queue_finished re-enables the Condense button and hides Cancel."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    slots = _capture_signal_slots(fake_worker.queue_finished)
    _start_condense(tab, fake_worker)

    for slot in slots:
        slot()

    assert tab.condense_button.isEnabled()
    assert tab.cancel_button.isHidden()


def test_worker_released_on_thread_finished(qtbot, tmp_path):
    """Native QThread.finished clears the handle and schedules deleteLater."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    slots = _capture_signal_slots(fake_worker.finished)
    _start_condense(tab, fake_worker)

    assert tab.worker_thread is fake_worker
    for slot in slots:
        slot()

    assert tab.worker_thread is None
    fake_worker.deleteLater.assert_called_once()


# ---------------------------------------------------------------------------
# iter_close_workers
# ---------------------------------------------------------------------------


def test_iter_close_workers_empty_when_no_worker(qtbot, tmp_path):
    """iter_close_workers() yields nothing when no worker has been started."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert list(tab.iter_close_workers()) == []


def test_iter_close_workers_returns_active_worker(qtbot, tmp_path):
    """iter_close_workers() yields the active worker when one is running."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    _start_condense(tab, fake_worker)

    assert fake_worker in list(tab.iter_close_workers())


# ---------------------------------------------------------------------------
# Worker signal slots
# ---------------------------------------------------------------------------


def test_file_finished_error_logs_error(qtbot, tmp_path):
    """file_finished(idx, None, error) logs the error text."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    slots = _capture_signal_slots(fake_worker.file_finished)
    _start_condense(tab, fake_worker)

    for slot in slots:
        slot(0, None, "boom")

    assert "boom" in tab.log_widget.text_edit.toPlainText()


def test_file_skipped_logs_skipped_not_done(qtbot, tmp_path):
    """file_skipped(idx, out_path) logs 'Skipped: <name>', not 'Done'."""
    config = _make_config(tmp_path)
    media = tmp_path / "episode.mkv"
    media.write_bytes(b"fake")
    out_audio = tmp_path / "episode_condensed.mp3"

    tab = _make_tab(config, qtbot)
    tab.media_file_selector.set_path(str(media))

    fake_worker = _FakeWorker()
    slots = _capture_signal_slots(fake_worker.file_skipped)
    _start_condense(tab, fake_worker)

    for slot in slots:
        slot(0, out_audio)

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Skipped" in log_text
    assert "episode_condensed.mp3" in log_text
    assert "Done" not in log_text


# ---------------------------------------------------------------------------
# update_config option-default refresh (idle-only)
# ---------------------------------------------------------------------------


def _config_with_defaults(tmp_path: Path, **overrides):
    """A stand-in config carrying the condenser_* defaults (real fields land in Task 8)."""
    base = {
        "ffmpeg_location": None,
        "ffprobe_location": None,
        "condenser_padding_ms": 1234,
        "condenser_offset_ms": -321,
        "condenser_output_format": "flac",
        "condenser_write_subtitles": True,
        "condenser_bitrate_kbps": 128,
        "condenser_filtered_chars": "XYZ",
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def test_update_config_refreshes_defaults_when_idle(qtbot, tmp_path):
    """update_config re-seeds option widgets from the new config while idle."""
    tab = _make_tab(_make_config(tmp_path), qtbot)

    with patch(_AVAILABLE, return_value=True):
        tab.update_config(_config_with_defaults(tmp_path))

    assert tab.padding_spinbox.value() == 1234
    assert tab.offset_spinbox.value() == -321
    assert tab.format_combo.currentData() == "flac"
    assert tab.write_subs_checkbox.isChecked() is True


def test_update_config_does_not_refresh_defaults_during_run(qtbot, tmp_path):
    """A run in flight keeps its option widgets; update_config must not re-seed."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.padding_spinbox.setValue(500)
    tab.offset_spinbox.setValue(0)

    running = _FakeWorker()
    running._started = True  # isRunning() → True
    tab.worker_thread = running

    with patch(_AVAILABLE, return_value=True):
        tab.update_config(_config_with_defaults(tmp_path))

    # Widgets untouched during a run.
    assert tab.padding_spinbox.value() == 500
    assert tab.offset_spinbox.value() == 0
    # But the config handle itself is swapped.
    assert tab.config.condenser_padding_ms == 1234


# ---------------------------------------------------------------------------
# Track probing runs off the GUI thread
# ---------------------------------------------------------------------------


def _audio_stream():
    from anki_miner.utils.audio_track_detector import AudioStream

    return AudioStream(
        global_index=1,
        audio_index=0,
        language_tag="jpn",
        title_tag=None,
        codec="aac",
        channels=2,
        is_default=True,
    )


def _sub_stream():
    from anki_miner.utils.audio_track_detector import SubtitleStream

    return SubtitleStream(
        index=2,
        sub_index=0,
        codec_name="ass",
        language_tag="jpn",
        title=None,
        is_text=True,
    )


def test_audio_tracks_clicked_warns_when_no_media(qtbot, tmp_path):
    """No media selected → warning, ffprobe never runs."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.media_file_selector.set_path("")
    with patch(_LIST_AUDIO) as mock_list, patch(_WARN) as mock_warn:
        tab._on_audio_tracks_clicked()
    mock_warn.assert_called_once()
    mock_list.assert_not_called()


def test_audio_tracks_probe_applies_override(qtbot, tmp_path):
    """Accepting the audio dialog applies the override and updates the label."""
    from PyQt6.QtWidgets import QDialog

    tab = _make_tab(_make_config(tmp_path), qtbot)
    media = tmp_path / "ep01.mkv"
    media.touch()
    tab.media_file_selector.set_path(str(media))

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog.selected_override.return_value = 2
    mock_class = MagicMock(return_value=mock_dialog)
    mock_class.DialogCode = QDialog.DialogCode

    with patch(_LIST_AUDIO, return_value=[_audio_stream()]), patch(_AUDIO_DIALOG, mock_class):
        tab._on_audio_tracks_clicked()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    assert tab._audio_track_override == 2
    assert "3" in tab.audio_track_label.text()
    assert tab.audio_tracks_button.isEnabled()


def test_subtitle_tracks_probe_applies_override(qtbot, tmp_path):
    """Accepting the subtitle dialog applies the sub_index override."""
    from PyQt6.QtWidgets import QDialog

    tab = _make_tab(_make_config(tmp_path), qtbot)
    media = tmp_path / "ep01.mkv"
    media.touch()
    tab.media_file_selector.set_path(str(media))

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog.selected_override.return_value = 0
    mock_class = MagicMock(return_value=mock_dialog)
    mock_class.DialogCode = QDialog.DialogCode

    with patch(_LIST_SUBS, return_value=[_sub_stream()]), patch(_SUB_DIALOG, mock_class):
        tab._on_subtitle_tracks_clicked()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    assert tab._subtitle_track_override == 0
    assert "1" in tab.subtitle_track_label.text()
    assert tab.subtitle_tracks_button.isEnabled()
