"""Tests for StageWeightedProgress.

The mining pipeline reports progress per stage (extract, definitions, optional
glossaries, cards), each a fresh on_start->on_progress->on_complete cycle. The
wrapper folds those cycles into one monotonic 0->100 sweep so the bar no longer
hits 100% at the end of media extraction (stage 1 of 4).
"""

from __future__ import annotations

from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.orchestration.stage_weighted_progress import StageWeightedProgress
from tests.conftest import RecordingProgress


def _drive_stage(wrapper: StageWeightedProgress, total: int) -> None:
    """Simulate one service stage: on_start, per-item on_progress, on_complete."""
    wrapper.on_start(total, "stage")
    for i in range(1, total + 1):
        wrapper.on_progress(i, f"item {i}")
    wrapper.on_complete()


def test_inner_on_start_emitted_once_with_max_100():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.25, 0.25])
    w.on_start(10, "extract")
    w.on_start(5, "definitions")  # second stage must NOT reset the bar
    assert inner.starts == [(100, "extract")]


def test_first_stage_caps_at_its_band_not_100():
    """Regression: media extraction must top out at its weight (40%), not 100%."""
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.25, 0.10, 0.25])
    w.on_start(8, "extract")
    w.on_progress(8, "last")  # extraction fully done
    assert inner.progresses[-1][0] == 40


def test_progress_is_monotonic_across_stages():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.25, 0.10, 0.25])
    for total in (8, 4, 2, 6):  # extract, defs, gloss, cards
        _drive_stage(w, total)
    w.finish()
    values = [pct for pct, _ in inner.progresses]
    assert values == sorted(values)
    assert values[-1] == 100


def test_finish_snaps_to_100_and_completes_once():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.5, 0.5])
    _drive_stage(w, 3)
    w.finish()
    assert inner.progresses[-1] == (100, "")
    assert inner.completes == 1


def test_finish_is_idempotent():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [1.0])
    _drive_stage(w, 2)
    w.finish()
    w.finish()
    assert inner.completes == 1


def test_finish_is_noop_when_no_stage_started():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.5, 0.5])
    w.finish()
    assert inner.starts == []
    assert inner.completes == 0


def test_final_stage_completes_even_if_it_never_started():
    """Empty card batch: cards stage skips on_start/on_complete entirely.

    finish() must still drive the bar to 100% rather than stalling at the end
    of the definitions band.
    """
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.5, 0.25, 0.25])  # extract, defs, cards
    _drive_stage(w, 4)  # extract
    _drive_stage(w, 4)  # definitions
    # cards stage produced no payloads -> no on_start, no on_complete
    w.finish()
    assert inner.progresses[-1] == (100, "")
    assert inner.completes == 1


def test_weights_are_normalized():
    """Weights need not sum to 1.0; bands scale proportionally."""
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [2, 2])  # -> 0.5, 0.5
    w.on_start(4, "a")
    w.on_progress(4, "done a")
    assert inner.progresses[-1][0] == 50


def test_partial_progress_within_band():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.6])
    w.on_start(4, "extract")
    w.on_progress(2, "halfway")  # 50% of a 40% band -> 20%
    assert inner.progresses[-1][0] == 20


def test_zero_total_stage_does_not_crash():
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.5, 0.5])
    w.on_start(0, "empty")
    w.on_progress(0, "noop")  # frac falls back to 1.0 -> top of band
    w.on_complete()
    w.finish()
    assert inner.progresses[-1] == (100, "")


# ---------------------------------------------------------------------------
# Stage-boundary label forwarding (issue-1b: later stages refresh the row
# label at the banked cursor via on_progress, never a second on_start).
# ---------------------------------------------------------------------------


def test_later_stage_on_start_forwards_label_at_banked_cursor():
    """A later stage's on_start refreshes the label at the banked cursor
    without ever forwarding a second inner.on_start (that would reset the bar)."""
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.6])
    _drive_stage(w, 4)  # stage 1 banks 40%
    w.on_start(3, "definitions")
    assert inner.starts == [(100, "stage")]  # no second on_start
    assert inner.progresses[-1] == (40, "definitions")  # refresh at banked cursor


def test_later_stage_boundary_is_monotone():
    """The boundary refresh sits at the banked cursor, never below the prior pct."""
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.6])
    _drive_stage(w, 4)
    prev = inner.progresses[-1][0]  # 40 (top of stage-1 band)
    w.on_start(3, "definitions")
    assert inner.progresses[-1][0] >= prev


def test_later_stage_on_start_empty_desc_does_not_forward():
    """An empty later-stage description emits nothing (no blank label refresh)."""
    inner = RecordingProgress()
    w = StageWeightedProgress(inner, [0.4, 0.6])
    _drive_stage(w, 4)
    n = len(inner.progresses)
    w.on_start(3, "")  # empty desc -> no refresh
    assert len(inner.progresses) == n
    assert inner.starts == [(100, "stage")]


def test_composition_real_adapter_refreshes_label_no_double_prefix():
    """Issue #1 guard: StageWeightedProgress over a REAL QueueMiningProgressAdapter.

    A later stage's on_start refreshes the emitted row label to the new stage
    description with no stage-1 prefix glued in front (the "Preparing page
    images: Expression audio: 語" bug), and the accepted finish() residue falls
    back to the stage-1 description for its final emission.
    """
    emitted: list[tuple[int, str, int]] = []
    adapter = QueueMiningProgressAdapter(idx=0, emit=lambda idx, label, pct: emitted.append((idx, label, pct)))
    w = StageWeightedProgress(adapter, [0.5, 0.5])

    # Stage 1: media extraction (self-prefixed item strings).
    w.on_start(2, "Extracting media")
    w.on_progress(1, "Extracting media: word-01")
    w.on_progress(2, "Extracting media: word-02")
    w.on_complete()

    # Stage 2: definitions — its on_start refreshes the row label.
    w.on_start(2, "Fetching definitions")
    w.on_progress(1, "Definition found: word-01")
    w.on_complete()

    w.finish()

    labels = [label for _, label, _ in emitted]
    # The stage-2 boundary refreshed the label cleanly to the new stage.
    assert "Fetching definitions" in labels
    # Self-prefixed later-stage item strings pass through un-glued.
    assert "Definition found: word-01" in labels
    # No stage-1 desc glued in front of any later-stage label (the #1 bug).
    assert "Extracting media: Fetching definitions" not in labels
    assert "Extracting media: Definition found: word-01" not in labels
    # Accepted cosmetic residue: finish()'s empty-desc emit falls back to the
    # stage-1 description (momentary, immediately superseded — pinned here).
    assert emitted[-1] == (0, "Extracting media", 100)
