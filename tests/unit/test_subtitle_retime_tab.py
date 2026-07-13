"""Tests for SubtitleRetimeTab.

Covers:
- Construction (qtbot.addWidget contract)
- alass-availability guard: present → Retime enabled, notice hidden;
  absent → button disabled, notice visible.
- Mode toggle (single video+subtitle selectors vs folder selectors)
- Single-mode pair collection: both set → [(video, sub)]; missing one → warning, [].
- Folder-mode pair collection: patched matcher → tuples + "Matched N of M" logged;
  unmatched case logs a warning.
- Output-location label toggling (Choose Folder / Reset)
- iter_close_workers returns the active worker
- split-penalty spinbox default is 7

No real alass runs: SubtitleRetimeWorker and the availability check are patched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.utils.file_pairing import FilePair

# ---------------------------------------------------------------------------
# Common patch target constants
# ---------------------------------------------------------------------------

_AVAILABLE = "anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab._alass_available"
_OS_ACCESS = "anki_miner.gui.widgets.subtitle_retime_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeWorker"
_FIND_PAIRS = "anki_miner.gui.widgets.subtitle_retime_tab.FilePairMatcher.find_pairs_by_episode_number"


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
    """Minimal fake that mimics the SubtitleRetimeWorker interface used by the tab."""

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
    """Construct a SubtitleRetimeTab with alass patched available=True."""
    with patch(_AVAILABLE, return_value=True):
        tab = SubtitleRetimeTab(config)
    qtbot.addWidget(tab)
    return tab


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction(qtbot, tmp_path):
    """Tab constructs and registers with qtbot without error."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab is not None
    assert tab.retime_button is not None


def test_update_config_swaps_config(qtbot, tmp_path):
    """update_config adopts the new config (re-evaluates the availability guard)."""
    import dataclasses

    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)

    new_config = dataclasses.replace(config, alass_location="/some/path")
    with patch(_AVAILABLE, return_value=True):
        tab.update_config(new_config)

    assert tab.config is new_config


# ---------------------------------------------------------------------------
# Availability guard
# ---------------------------------------------------------------------------


def test_alass_present_enables_retime(qtbot, tmp_path):
    """alass present → Retime enabled, notice hidden."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.retime_button.isEnabled()
    assert tab.engine_notice_label.isHidden()


def test_alass_absent_disables_retime(qtbot, tmp_path):
    """alass absent → Retime disabled, notice visible."""
    config = _make_config(tmp_path)
    with patch(_AVAILABLE, return_value=False):
        tab = SubtitleRetimeTab(config)
    qtbot.addWidget(tab)
    assert not tab.retime_button.isEnabled()
    assert not tab.engine_notice_label.isHidden()


def test_alass_available_via_path_check(qtbot, tmp_path):
    """When resolve_alass returns 'alass', shutil.which determines availability."""
    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value="alass"),
        patch("anki_miner.gui.widgets.subtitle_retime_tab.shutil.which", return_value="/usr/bin/alass"),
    ):
        tab = SubtitleRetimeTab(config)
    qtbot.addWidget(tab)
    assert tab.retime_button.isEnabled()


def test_alass_unavailable_via_path_check(qtbot, tmp_path):
    """resolve_alass returns 'alass' but shutil.which → None → unavailable."""
    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value="alass"),
        patch("anki_miner.gui.widgets.subtitle_retime_tab.shutil.which", return_value=None),
    ):
        tab = SubtitleRetimeTab(config)
    qtbot.addWidget(tab)
    assert not tab.retime_button.isEnabled()


def test_alass_resolved_path_missing_unavailable(qtbot, tmp_path):
    """resolve_alass returns an explicit path that does not exist → unavailable."""
    config = _make_config(tmp_path)
    missing = str(tmp_path / "nope" / "alass")
    with patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value=missing):
        tab = SubtitleRetimeTab(config)
    qtbot.addWidget(tab)
    assert not tab.retime_button.isEnabled()


def test_alass_resolved_path_exists_available(qtbot, tmp_path):
    """resolve_alass returns an explicit path that exists → available."""
    config = _make_config(tmp_path)
    binary = tmp_path / "alass"
    binary.write_bytes(b"fake")
    with patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value=str(binary)):
        tab = SubtitleRetimeTab(config)
    qtbot.addWidget(tab)
    assert tab.retime_button.isEnabled()


# ---------------------------------------------------------------------------
# Mode toggle
# ---------------------------------------------------------------------------


def test_mode_toggle_single_by_default(qtbot, tmp_path):
    """Single-file mode is the default; folder selectors hidden."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert not tab.video_file_selector.isHidden()
    assert not tab.subtitle_file_selector.isHidden()
    assert tab.video_folder_selector.isHidden()
    assert tab.subtitle_folder_selector.isHidden()


def test_mode_toggle_switches_to_folder(qtbot, tmp_path):
    """Clicking folder mode shows folder selectors, hides file selectors."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    assert tab.video_file_selector.isHidden()
    assert tab.subtitle_file_selector.isHidden()
    assert not tab.video_folder_selector.isHidden()
    assert not tab.subtitle_folder_selector.isHidden()


def test_mode_toggle_back_to_file(qtbot, tmp_path):
    """Toggling back to file mode re-shows file selectors."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.folder_mode_button.click()
    tab.file_mode_button.click()
    assert not tab.video_file_selector.isHidden()
    assert not tab.subtitle_file_selector.isHidden()
    assert tab.video_folder_selector.isHidden()
    assert tab.subtitle_folder_selector.isHidden()


# ---------------------------------------------------------------------------
# Single-mode pair collection
# ---------------------------------------------------------------------------


def test_single_mode_collects_pair(qtbot, tmp_path):
    """Both video + subtitle set → returns [(video, sub)]."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    pairs = tab._collect_pairs()
    assert pairs == [(video, sub)]


def test_single_mode_missing_subtitle_warns(qtbot, tmp_path):
    """Missing subtitle → warning, returns []."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))

    with patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as warn:
        pairs = tab._collect_pairs()

    warn.assert_called_once()
    assert pairs == []


def test_single_mode_missing_video_warns(qtbot, tmp_path):
    """Missing video → warning, returns []."""
    config = _make_config(tmp_path)
    sub = tmp_path / "episode.srt"
    sub.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.subtitle_file_selector.set_path(str(sub))

    with patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as warn:
        pairs = tab._collect_pairs()

    warn.assert_called_once()
    assert pairs == []


# ---------------------------------------------------------------------------
# Folder-mode pair collection
# ---------------------------------------------------------------------------


def test_folder_mode_collects_pairs_and_logs_matched(qtbot, tmp_path):
    """Folder mode: patched matcher → tuples returned + 'Matched N of M' logged."""
    config = _make_config(tmp_path)
    video_folder = tmp_path / "videos"
    sub_folder = tmp_path / "subs"
    video_folder.mkdir()
    sub_folder.mkdir()

    v1 = video_folder / "ep01.mp4"
    v2 = video_folder / "ep02.mp4"
    v1.write_bytes(b"fake")
    v2.write_bytes(b"fake")
    s1 = sub_folder / "ep01.srt"
    s2 = sub_folder / "ep02.srt"
    s1.write_text("1\n")
    s2.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.video_folder_selector.set_path(str(video_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    fake_pairs = [FilePair(v1, s1), FilePair(v2, s2)]
    with patch(_FIND_PAIRS, return_value=fake_pairs):
        pairs = tab._collect_pairs()

    assert pairs == [(v1, s1), (v2, s2)]
    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Matched 2 of 2" in log_text


def test_pairing_summary_survives_log_clear_on_start(qtbot, tmp_path):
    """The 'Matched N of M' line logged during collection survives the pre-run
    log clear (clear happens before _collect_pairs, not after)."""
    config = _make_config(tmp_path)
    video_folder = tmp_path / "videos"
    sub_folder = tmp_path / "subs"
    video_folder.mkdir()
    sub_folder.mkdir()

    v1 = video_folder / "ep01.mp4"
    v2 = video_folder / "ep02.mp4"
    v1.write_bytes(b"fake")
    v2.write_bytes(b"fake")
    s1 = sub_folder / "ep01.srt"
    s2 = sub_folder / "ep02.srt"
    s1.write_text("1\n")
    s2.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.video_folder_selector.set_path(str(video_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    fake_pairs = [FilePair(v1, s1), FilePair(v2, s2)]
    fake_worker = _FakeWorker()
    with (
        patch(_FIND_PAIRS, return_value=fake_pairs),
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    assert fake_worker._started
    assert "Matched 2 of 2" in tab.log_widget.text_edit.toPlainText()


def test_folder_mode_unmatched_logs_warning(qtbot, tmp_path):
    """Folder mode with fewer matches than videos logs a warning."""
    config = _make_config(tmp_path)
    video_folder = tmp_path / "videos"
    sub_folder = tmp_path / "subs"
    video_folder.mkdir()
    sub_folder.mkdir()

    v1 = video_folder / "ep01.mp4"
    v2 = video_folder / "ep02.mp4"
    v1.write_bytes(b"fake")
    v2.write_bytes(b"fake")
    s1 = sub_folder / "ep01.srt"
    s1.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.video_folder_selector.set_path(str(video_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    fake_pairs = [FilePair(v1, s1)]
    with patch(_FIND_PAIRS, return_value=fake_pairs):
        pairs = tab._collect_pairs()

    assert pairs == [(v1, s1)]
    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Matched 1 of 2" in log_text
    # Independent assertion against the unmatched-warning message text.
    assert "could not be matched" in log_text


def test_folder_mode_no_pairs_warns(qtbot, tmp_path):
    """Folder mode with no matched pairs → QMessageBox warning, returns []."""
    config = _make_config(tmp_path)
    video_folder = tmp_path / "videos"
    sub_folder = tmp_path / "subs"
    video_folder.mkdir()
    sub_folder.mkdir()
    (video_folder / "ep01.mp4").write_bytes(b"fake")

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    tab.video_folder_selector.set_path(str(video_folder))
    tab.subtitle_folder_selector.set_path(str(sub_folder))

    with (
        patch(_FIND_PAIRS, return_value=[]),
        patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as warn,
    ):
        pairs = tab._collect_pairs()

    warn.assert_called_once()
    assert pairs == []


def test_folder_mode_missing_video_folder_warns(qtbot, tmp_path):
    """No video folder selected → warning, returns []."""
    config = _make_config(tmp_path)
    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()

    with patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as warn:
        pairs = tab._collect_pairs()

    warn.assert_called_once()
    assert pairs == []


# ---------------------------------------------------------------------------
# Output location toggle
# ---------------------------------------------------------------------------


def test_choose_output_sets_label_and_shows_reset(qtbot, tmp_path):
    """Choosing a folder updates the label and reveals the Reset button."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    out = tmp_path / "out"
    out.mkdir()

    with patch(
        "anki_miner.gui.widgets._tool_tab_base.QFileDialog.getExistingDirectory",
        return_value=str(out),
    ):
        tab._on_choose_output()

    assert tab._custom_output_dir == out
    assert str(out) in tab.output_location_label.text()
    assert not tab.clear_output_button.isHidden()


def test_clear_output_resets_label(qtbot, tmp_path):
    """Reset clears the custom dir and restores the default label."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    out = tmp_path / "out"
    out.mkdir()

    with patch(
        "anki_miner.gui.widgets._tool_tab_base.QFileDialog.getExistingDirectory",
        return_value=str(out),
    ):
        tab._on_choose_output()

    tab._on_clear_output()
    assert tab._custom_output_dir is None
    assert "Next to source video" in tab.output_location_label.text()
    assert tab.clear_output_button.isHidden()


# ---------------------------------------------------------------------------
# Split penalty
# ---------------------------------------------------------------------------


def test_split_penalty_default_is_seven(qtbot, tmp_path):
    """Split penalty spinbox defaults to 7."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.split_penalty_spinbox.value() == 7.0


def test_split_penalty_has_visible_helper(qtbot, tmp_path):
    """An inline helper label explains the split-penalty control (not tooltip-only)."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.split_penalty_helper.text().strip()
    assert tab.split_penalty_helper.objectName() == "helper-text"


# ---------------------------------------------------------------------------
# Control explanations / styling
# ---------------------------------------------------------------------------


def test_output_location_label_objectname_not_helper_text(qtbot, tmp_path):
    """Output-location label uses a non-italic objectName, not 'helper-text'."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.output_location_label.objectName() == "output-location-value"


def test_mode_buttons_have_tooltips(qtbot, tmp_path):
    """Single-file / folder mode buttons carry explanatory tooltips."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.file_mode_button.toolTip().strip()
    assert tab.folder_mode_button.toolTip().strip()


def test_overwrite_checkbox_has_tooltip(qtbot, tmp_path):
    """Overwrite checkbox explains skip-vs-overwrite via tooltip."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.overwrite_checkbox.toolTip().strip()


def test_split_penalty_passed_to_worker(qtbot, tmp_path):
    """The spinbox value is forwarded to the worker as split_penalty."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    fake_worker = _FakeWorker()
    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))
    tab.split_penalty_spinbox.setValue(12.0)

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker) as worker_cls,
    ):
        tab.retime_button.click()

    assert worker_cls.call_count == 1
    assert worker_cls.call_args.kwargs["split_penalty"] == 12.0


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


def test_retime_starts_worker_and_disables_button(qtbot, tmp_path):
    """Clicking Retime with a valid pair starts the worker and disables Retime."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    fake_worker = _FakeWorker()
    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    assert tab.worker_thread is fake_worker
    assert fake_worker._started
    assert not tab.retime_button.isEnabled()


def test_unwritable_output_aborts_no_worker(qtbot, tmp_path):
    """Unwritable output dir aborts before a worker is created."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=False),
        patch(_WORKER_CLS, side_effect=AssertionError("worker must not be created")),
    ):
        tab.retime_button.click()

    assert tab.worker_thread is None
    log_text = tab.log_widget.text_edit.toPlainText()
    assert "not writable" in log_text


def test_queue_finished_re_enables_retime(qtbot, tmp_path):
    """queue_finished re-enables the Retime button."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    fake_worker = _FakeWorker()
    slots: list = []
    orig = fake_worker.queue_finished.connect

    def _capture(slot):
        slots.append(slot)
        return orig(slot)

    fake_worker.queue_finished.connect = _capture

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    for slot in slots:
        slot()

    assert tab.retime_button.isEnabled()


def test_worker_released_on_thread_finished(qtbot, tmp_path):
    """Native QThread.finished clears the handle and schedules deleteLater."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    fake_worker = _FakeWorker()
    slots: list = []
    orig = fake_worker.finished.connect

    def _capture(slot):
        slots.append(slot)
        return orig(slot)

    fake_worker.finished.connect = _capture

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    assert tab.worker_thread is fake_worker
    for slot in slots:
        slot()

    assert tab.worker_thread is None
    fake_worker.deleteLater.assert_called_once()


def test_second_retime_refused_while_running(qtbot, tmp_path):
    """A second Retime while the worker is running must not start a new one."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    fake_worker = _FakeWorker()
    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker) as worker_cls,
    ):
        tab.retime_button.click()  # starts worker (isRunning → True)
        # Re-enable the button to simulate a premature queue_finished, then click again.
        tab.retime_button.setEnabled(True)
        tab.retime_button.click()

    assert worker_cls.call_count == 1
    assert tab.worker_thread is fake_worker


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
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")

    fake_worker = _FakeWorker()
    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    assert fake_worker in list(tab.iter_close_workers())


# ---------------------------------------------------------------------------
# file_skipped slot: logs "Skipped:", advances progress once
# ---------------------------------------------------------------------------


def _capture_signal_slots(signal_mock):
    """Capture slots connected to a _FakeWorker MagicMock signal; returns the list."""
    slots: list = []
    original_connect = signal_mock.connect

    def _capture(slot):
        slots.append(slot)
        return original_connect(slot)

    signal_mock.connect = _capture
    return slots


def test_file_skipped_logs_skipped_not_done(qtbot, tmp_path):
    """file_skipped(idx, out_path) logs 'Skipped: <name>', not 'Done:' (T1)."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")
    out_srt = tmp_path / "episode.srt"

    fake_worker = _FakeWorker()
    skipped_slots = _capture_signal_slots(fake_worker.file_skipped)

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    for slot in skipped_slots:
        slot(0, out_srt)

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Skipped" in log_text
    assert "episode.srt" in log_text
    assert "Done" not in log_text


def test_file_skipped_advances_progress(qtbot, tmp_path):
    """file_skipped(idx, out_path) advances the progress bar exactly once (T2)."""
    config = _make_config(tmp_path)
    video1 = tmp_path / "ep01.mp4"
    video2 = tmp_path / "ep02.mp4"
    sub1 = tmp_path / "ep01.srt"
    sub2 = tmp_path / "ep02.srt"
    for p in (video1, video2, sub1, sub2):
        p.write_bytes(b"fake")

    fake_worker = _FakeWorker()
    skipped_slots = _capture_signal_slots(fake_worker.file_skipped)

    tab = _make_tab(config, qtbot)
    tab.folder_mode_button.click()
    from anki_miner.utils.file_pairing import FilePair

    tab.video_folder_selector.set_path(str(tmp_path))
    tab.subtitle_folder_selector.set_path(str(tmp_path))

    fake_pairs = [FilePair(video1, sub1), FilePair(video2, sub2)]
    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
        patch(_FIND_PAIRS, return_value=fake_pairs),
    ):
        tab.retime_button.click()

    assert tab.progress_widget.progress_bar.value() == 0

    for slot in skipped_slots:
        slot(0, video1.with_suffix(".srt"))
    assert tab.progress_widget.progress_bar.value() == 50

    for slot in skipped_slots:
        slot(1, video2.with_suffix(".srt"))
    assert tab.progress_widget.progress_bar.value() == 100


def test_file_finished_still_logs_done_for_success(qtbot, tmp_path):
    """file_finished(idx, out_path, None) still logs 'Done: <name>' (success path unchanged)."""
    config = _make_config(tmp_path)
    video = tmp_path / "episode.mp4"
    sub = tmp_path / "episode.srt"
    video.write_bytes(b"fake")
    sub.write_text("1\n")
    out_srt = tmp_path / "episode.srt"

    fake_worker = _FakeWorker()
    finished_slots = _capture_signal_slots(fake_worker.file_finished)

    tab = _make_tab(config, qtbot)
    tab.video_file_selector.set_path(str(video))
    tab.subtitle_file_selector.set_path(str(sub))

    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake_worker),
    ):
        tab.retime_button.click()

    for slot in finished_slots:
        slot(0, out_srt, None)

    log_text = tab.log_widget.text_edit.toPlainText()
    assert "Done" in log_text
    assert "episode.srt" in log_text


# ---------------------------------------------------------------------------
# alass availability caching (probe runs once per config, not per call)
# ---------------------------------------------------------------------------


def test_alass_probe_cached_not_rerun_per_call(qtbot, tmp_path):
    """The alass PATH probe runs ONCE at construction, not on every _alass_available()."""
    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value="alass"),
        patch(
            "anki_miner.gui.widgets.subtitle_retime_tab.shutil.which",
            return_value="/usr/bin/alass",
        ) as which,
    ):
        tab = SubtitleRetimeTab(config)
        qtbot.addWidget(tab)
        # Construction probed exactly once.
        assert which.call_count == 1
        # Repeated availability reads must NOT re-probe.
        assert tab._alass_available() is True
        assert tab._alass_available() is True
        tab._refresh_engine_state()
        assert which.call_count == 1


def test_update_config_recomputes_alass_cache(qtbot, tmp_path):
    """update_config recomputes the cached bool: a newly available alass re-enables Retime."""
    import dataclasses

    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value="alass"),
        patch("anki_miner.gui.widgets.subtitle_retime_tab.shutil.which", return_value=None) as which,
    ):
        tab = SubtitleRetimeTab(config)
        qtbot.addWidget(tab)
        assert not tab.retime_button.isEnabled()
        assert which.call_count == 1

        # alass now appears on PATH; a config refresh must flip the cached bool.
        which.return_value = "/usr/bin/alass"
        tab.update_config(dataclasses.replace(config, alass_location="/x"))
        assert which.call_count == 2
        assert tab.retime_button.isEnabled()
        assert tab._alass_is_available is True


def test_on_retime_uses_cached_availability_without_reprobe(qtbot, tmp_path):
    """The retime-button guard reads the cached bool; it does not re-probe PATH."""
    config = _make_config(tmp_path)
    with (
        patch("anki_miner.gui.widgets.subtitle_retime_tab.resolve_alass", return_value="alass"),
        patch(
            "anki_miner.gui.widgets.subtitle_retime_tab.shutil.which",
            return_value="/usr/bin/alass",
        ) as which,
    ):
        tab = SubtitleRetimeTab(config)
        qtbot.addWidget(tab)
        assert which.call_count == 1
        # No files selected -> _on_retime bails after the (cached) guard, no re-probe.
        # Patch the no-files warning modal so it does not block under offscreen Qt.
        with patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning"):
            tab._on_retime()
        assert which.call_count == 1


# ---------------------------------------------------------------------------
# Audio-track probing (Tracks button) — must run OFF the GUI thread
# ---------------------------------------------------------------------------

_LIST_STREAMS = "anki_miner.gui.widgets.subtitle_retime_tab.list_audio_streams"
_TRACKS_DIALOG = "anki_miner.gui.widgets.subtitle_retime_tab.AudioTracksDialog"


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


def test_tracks_clicked_warns_when_no_video(qtbot, tmp_path):
    """No video selected -> warning, ffprobe never runs."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.video_file_selector.set_path("")
    with (
        patch(_LIST_STREAMS) as mock_list,
        patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as mock_warn,
    ):
        tab._on_tracks_clicked()
    mock_warn.assert_called_once()
    mock_list.assert_not_called()


def test_tracks_clicked_warns_when_video_missing(qtbot, tmp_path):
    """Selected video path that is not a file -> warning, ffprobe never runs."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.video_file_selector.set_path(str(tmp_path / "nope.mkv"))
    with (
        patch(_LIST_STREAMS) as mock_list,
        patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as mock_warn,
    ):
        tab._on_tracks_clicked()
    mock_warn.assert_called_once()
    mock_list.assert_not_called()


def test_tracks_probe_runs_off_gui_thread(qtbot, tmp_path):
    """list_audio_streams must run on a worker thread, not the GUI thread."""
    import threading

    from PyQt6.QtWidgets import QDialog

    tab = _make_tab(_make_config(tmp_path), qtbot)
    video = tmp_path / "ep01.mkv"
    video.touch()
    tab.video_file_selector.set_path(str(video))

    probe_thread: dict = {}

    def _record(*_a, **_k):
        probe_thread["id"] = threading.get_ident()
        return [_audio_stream()]

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = QDialog.DialogCode.Rejected
    mock_class = MagicMock(return_value=mock_dialog)
    mock_class.DialogCode = QDialog.DialogCode

    with (
        patch(_LIST_STREAMS, side_effect=_record),
        patch(_TRACKS_DIALOG, mock_class),
    ):
        tab._on_tracks_clicked()
        # Button disabled while the probe runs off-thread.
        assert not tab.tracks_button.isEnabled()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    assert probe_thread["id"] != threading.get_ident()  # probed off the GUI thread
    assert tab.tracks_button.isEnabled()  # re-enabled after success


def test_tracks_probe_empty_shows_info(qtbot, tmp_path):
    """No audio tracks detected -> info box, no dialog, button re-enabled."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    video = tmp_path / "ep01.mkv"
    video.touch()
    tab.video_file_selector.set_path(str(video))

    mock_class = MagicMock()
    with (
        patch(_LIST_STREAMS, return_value=[]),
        patch(_TRACKS_DIALOG, mock_class),
        patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.information") as mock_info,
    ):
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_info.called, timeout=3000)

    mock_class.assert_not_called()
    assert tab.tracks_button.isEnabled()


def test_tracks_probe_applies_override_on_accept(qtbot, tmp_path):
    """Accepting the dialog applies the override and updates the label."""
    from PyQt6.QtWidgets import QDialog

    tab = _make_tab(_make_config(tmp_path), qtbot)
    video = tmp_path / "ep01.mkv"
    video.touch()
    tab.video_file_selector.set_path(str(video))

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog.selected_override.return_value = 2
    mock_class = MagicMock(return_value=mock_dialog)
    mock_class.DialogCode = QDialog.DialogCode

    with (
        patch(_LIST_STREAMS, return_value=[_audio_stream()]),
        patch(_TRACKS_DIALOG, mock_class),
    ):
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_class.called, timeout=3000)

    assert tab._audio_track_override == 2
    assert "3" in tab.audio_track_label.text()  # Track index + 1
    assert tab.tracks_button.isEnabled()


def test_tracks_probe_error_is_handled(qtbot, tmp_path):
    """A probe failure shows a warning and re-enables the button without crashing."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    video = tmp_path / "ep01.mkv"
    video.touch()
    tab.video_file_selector.set_path(str(video))

    with (
        patch(_LIST_STREAMS, side_effect=RuntimeError("ffprobe boom")),
        patch(_TRACKS_DIALOG) as mock_class,
        patch("anki_miner.gui.widgets.subtitle_retime_tab.QMessageBox.warning") as mock_warn,
    ):
        tab._on_tracks_clicked()
        qtbot.waitUntil(lambda: mock_warn.called, timeout=3000)

    mock_class.assert_not_called()
    assert tab.tracks_button.isEnabled()
