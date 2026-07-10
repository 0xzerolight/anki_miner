"""FIX G5: SingleEpisodeTab snapshots selector values for off-thread curation.

``_build_curation_context`` is dispatched via ``run_off_thread`` and previously
read live QWidgets (``video_selector.get_path()``, ``offset_spinbox.value()``)
off the worker thread — cross-thread QWidget access (UB). It must read plain
attributes snapshotted on the GUI thread at ``_start_processing`` instead, like
the Batch worker's ``_curation_*`` snapshot.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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


def test_build_curation_context_reads_snapshots_not_live_widgets(tab, monkeypatch):
    """The built context uses the snapshot, not values changed after start."""
    captured: dict = {}

    def _fake_make(config, video, subtitle, offset, audio_track_override=None):
        captured["video"] = video
        captured["subtitle"] = subtitle
        captured["offset"] = offset
        captured["audio_track_override"] = audio_track_override
        return None

    monkeypatch.setattr(tab, "_make_curation_media_context", _fake_make)

    # Simulate what _start_processing captures on the GUI thread.
    tab._curation_video = Path("/snap/video.mkv")
    tab._curation_subtitle = Path("/snap/subs.srt")
    tab._curation_offset = 1.5
    tab._curation_audio_track_override = 3

    # Mutate the live widgets AFTER the snapshot — these must be ignored.
    tab.video_selector.set_path("/live/other.mkv")
    tab.subtitle_selector.set_path("/live/other.srt")
    tab.offset_spinbox.setValue(-9.0)
    tab._audio_track_override = 99

    tab._build_curation_context()

    assert captured["video"] == Path("/snap/video.mkv")
    assert captured["subtitle"] == Path("/snap/subs.srt")
    assert captured["offset"] == 1.5
    assert captured["audio_track_override"] == 3


def test_snapshot_attrs_initialized(tab):
    """Snapshot attributes exist before any run so an early build is safe."""
    assert tab._curation_video is None
    assert tab._curation_subtitle is None
    assert tab._curation_offset == 0.0
    assert tab._curation_audio_track_override is None
