"""ManualPairWorkerThread curation wiring (Issue #60)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from anki_miner.gui.workers.manual_pair_worker import ManualPairWorkerThread


def _pair(tmp_path, n):
    v = tmp_path / f"ep{n}.mkv"
    s = tmp_path / f"ep{n}.ass"
    v.touch()
    s.touch()
    return SimpleNamespace(video=v, subtitle=s)


def test_curation_attrs_and_callback_forwarded(tmp_path):
    captured = {}
    cb = MagicMock(name="curation_callback")

    proc = MagicMock()

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        captured["video"] = worker._curation_video
        captured["subtitle"] = worker._curation_subtitle
        captured["offset"] = worker._curation_offset
        captured["processor"] = worker.curation_processor
        captured["callback"] = curation_callback
        return SimpleNamespace(cards_created=0)

    proc.process_episode.side_effect = fake_process
    proc.config = SimpleNamespace(subtitle_offset=2.5)

    pair = _pair(tmp_path, 1)
    worker = ManualPairWorkerThread(proc, [pair], progress_callback=None, curation_callback=cb)
    worker.run()

    assert captured["video"] == pair.video
    assert captured["subtitle"] == pair.subtitle
    assert captured["offset"] == 2.5
    assert captured["processor"] is proc
    assert captured["callback"] is cb


def test_curation_attrs_advance_per_pair(tmp_path):
    seen = []
    proc = MagicMock()
    proc.config = SimpleNamespace(subtitle_offset=0.0)

    def fake_process(video, subtitle, preview_mode, progress_callback, curation_callback=None):
        seen.append((worker._curation_video, worker._curation_subtitle))
        return SimpleNamespace(cards_created=0)

    proc.process_episode.side_effect = fake_process
    p1 = _pair(tmp_path, 1)
    p2 = _pair(tmp_path, 2)
    worker = ManualPairWorkerThread(proc, [p1, p2], progress_callback=None)
    worker.run()

    assert seen == [(p1.video, p1.subtitle), (p2.video, p2.subtitle)]
