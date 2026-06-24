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
        "anki_miner.gui.widgets.subtitle_retime_tab.QFileDialog.getExistingDirectory",
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
        "anki_miner.gui.widgets.subtitle_retime_tab.QFileDialog.getExistingDirectory",
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
