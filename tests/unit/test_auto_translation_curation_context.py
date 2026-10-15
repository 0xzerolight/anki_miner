"""F-04: the curator's Translation column shows the track the run picked itself."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.dialogs.word_curation_dialog import (
    CurationMediaContext,
    auto_translation_of,
    with_auto_translation,
)
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.workers._queue_worker_base import CurationEpisode

CUES = [(1.0, 2.0, "Hello.")]
CHOSEN = [(9.0, 9.5, "chosen")]


def _context(video: Path = Path("/v/ep01.mkv"), **kwargs) -> CurationMediaContext:
    return CurationMediaContext(video_file=video, subtitle_entries=[(1.0, 2.0, "x")], offset=0.0, **kwargs)


class _Proc:
    """The two processor attributes the curation context builders read."""

    offline_lookup_fn = None

    def __init__(self, picks: dict[Path, list]) -> None:
        self._picks = picks

    def auto_translation_entries(self, video: Path) -> list:
        return list(self._picks.get(video, []))


def _fake_make(
    config, video, subtitle, offset, audio_track_override=None, *, secondary_subtitle=None, secondary_offset=0.0
):
    entries = list(CHOSEN) if secondary_subtitle is not None else []
    return CurationMediaContext(
        video_file=video,
        subtitle_entries=[(1.0, 2.0, "x")],
        offset=offset,
        secondary_entries=entries,
        secondary_offset=secondary_offset,
    )


def test_the_picked_cues_fill_an_empty_translation():
    ctx = with_auto_translation(_context(), list(CUES), None)
    assert ctx is not None and ctx.secondary_entries == CUES and ctx.secondary_offset == 0.0


def test_a_chosen_file_wins():
    original = _context(secondary_entries=list(CHOSEN), secondary_offset=0.5)
    assert with_auto_translation(original, list(CUES), Path("/v/mine.srt")) is original


def test_no_pick_and_no_context_change_nothing():
    original = _context()
    assert with_auto_translation(original, [], None) is original
    assert with_auto_translation(None, list(CUES), None) is None


@pytest.mark.parametrize("processor", [None, MagicMock(name="processor"), object()])
def test_a_missing_or_stand_in_processor_has_no_pick(processor):
    assert auto_translation_of(processor, Path("/v/ep01.mkv")) == []


def test_a_real_pick_is_read_for_its_video():
    proc = _Proc({Path("/v/ep01.mkv"): list(CUES)})
    assert auto_translation_of(proc, Path("/v/ep01.mkv")) == CUES
    assert auto_translation_of(proc, Path("/v/ep02.mkv")) == []
    assert auto_translation_of(proc, None) == []


def test_single_tab_context_carries_the_auto_pick(qtbot, test_config, tmp_path, monkeypatch):
    tab = SingleEpisodeTab(
        config=replace(test_config, secondary_subtitle_enabled=True),
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)
    monkeypatch.setattr(tab, "_make_curation_media_context", _fake_make)
    video = tmp_path / "ep01.mkv"
    tab._curation_video, tab._curation_subtitle, tab._curation_secondary = video, tmp_path / "ep01.ass", None
    worker = MagicMock(name="worker")
    worker.curation_processor = _Proc({video: list(CUES)})
    tab.worker_thread = worker
    try:
        ctx, _lookup = tab._build_curation_context()
    finally:
        tab.worker_thread = None
    assert ctx is not None and ctx.secondary_entries == CUES


def test_season_resolver_carries_each_episodes_auto_pick(qtbot, test_config, tmp_path, monkeypatch):
    monkeypatch.setattr(MiningTabBase, "_make_curation_media_context", staticmethod(_fake_make))
    tab = BatchProcessingTab(
        config=replace(test_config, secondary_subtitle_enabled=True),
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)
    ep1, ep2 = tmp_path / "ep01.mkv", tmp_path / "ep02.mkv"
    worker = MagicMock(name="worker")
    worker._curation_video, worker._curation_subtitle, worker._curation_offset = ep1, tmp_path / "ep01.ass", 0.0
    worker._curation_secondary, worker._curation_secondary_offset = None, 0.0
    worker._curation_media_map = {
        ep1: CurationEpisode(tmp_path / "ep01.ass", 0.0, None, 0.0),
        ep2: CurationEpisode(tmp_path / "ep02.ass", 0.0, tmp_path / "ep02.en.srt", 0.0),
    }
    worker.curation_processor = _Proc({ep1: list(CUES), ep2: [(3.0, 4.0, "auto, not used")]})
    tab.worker_thread = worker
    try:
        ctx, _lookup = tab._build_curation_context()
    finally:
        tab.worker_thread = None
    assert ctx is not None and ctx.secondary_entries == CUES
    assert ctx.context_resolver is not None
    assert ctx.context_resolver(ep1).secondary_entries == CUES
    # The row's translation folder had ep02: the chosen file wins over the auto-pick.
    assert ctx.context_resolver(ep2).secondary_entries == CHOSEN
