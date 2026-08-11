"""The honest stage protocol that replaced the weighted 0->100 sweep (D18).

``StageWeightedProgress`` blended five pipeline stages into one number using
hard-coded weights. Those weights were guesses, so the bar raced through short
stages and then sat still on the long one -- the "frozen progress bar" report.

What replaces it carries only what is actually known: which stage of how many,
and the true local count inside that stage. Nothing here composes a
whole-episode percentage, because no honest denominator for one exists.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.widgets._queue_mining_tab_base import _QueueMiningTabBase
from anki_miner.gui.workers._queue_progress import QueueMiningProgressAdapter
from anki_miner.gui.workers._queue_worker_base import SequentialQueueWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.presenters.null_presenter import NullPresenter, NullProgressCallback

# ---------------------------------------------------------------------------
# Presenter carries stage identity
# ---------------------------------------------------------------------------


def test_gui_presenter_still_satisfies_the_protocol():
    assert isinstance(GUIPresenter(), PresenterProtocol)


def test_null_presenter_still_satisfies_the_protocol():
    assert isinstance(NullPresenter(), PresenterProtocol)


def test_show_stage_names_the_stage_and_the_total():
    presenter = GUIPresenter()
    seen: list[str] = []
    presenter.info_signal.connect(seen.append)

    presenter.show_stage(3, 5, "Extracting media")

    assert seen == ["Step 3 of 5 — Extracting media"]


def test_null_presenter_show_stage_is_silent():
    NullPresenter().show_stage(1, 5, "Parsing subtitles")


# ---------------------------------------------------------------------------
# Progress callback carries stage identity
# ---------------------------------------------------------------------------


def test_gui_progress_callback_emits_the_stage_verbatim():
    callback = GUIProgressCallback()
    seen: list[tuple[int, int, str]] = []
    callback.stage_signal.connect(lambda i, n, name: seen.append((i, n, name)))

    callback.on_stage(3, 5, "Extracting media")

    assert seen == [(3, 5, "Extracting media")]


def test_null_progress_callback_accepts_a_stage():
    NullProgressCallback().on_stage(2, 5, "Filtering")


# ---------------------------------------------------------------------------
# Queue adapter: stage + true within-stage counts, never a blended percent
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter_emissions():
    seen: list[tuple[int, str]] = []
    return seen, QueueMiningProgressAdapter(idx=4, emit=lambda idx, label: seen.append((idx, label)))


def test_adapter_reports_the_stage_with_its_position(adapter_emissions):
    seen, adapter = adapter_emissions

    adapter.on_stage(3, 5, "Extracting media")

    assert seen == [(4, "Stage 3 of 5 · Extracting media")]


def test_adapter_reports_true_within_stage_counts(adapter_emissions):
    seen, adapter = adapter_emissions

    adapter.on_stage(3, 5, "Extracting media")
    adapter.on_start(40, "Extracting media")
    adapter.on_progress(12, "Extracting media: 語")

    assert seen[-1] == (4, "Stage 3 of 5 · Extracting media: 語 (12 of 40)")


def test_adapter_omits_the_count_when_the_stage_declared_no_total(adapter_emissions):
    seen, adapter = adapter_emissions

    adapter.on_start(0, "Preparing page images")
    adapter.on_progress(3, "Page image: 3")

    assert seen[-1] == (4, "Page image: 3")


def test_adapter_keeps_the_last_label_when_an_item_has_none(adapter_emissions):
    seen, adapter = adapter_emissions

    adapter.on_start(2, "Fetching definitions")
    adapter.on_progress(1, "Definition found: 語")
    adapter.on_progress(2, "")

    assert seen[-1] == (4, "Definition found: 語 (2 of 2)")


def test_adapter_never_claims_the_item_is_complete_at_a_stage_end(adapter_emissions):
    """A stage ending is not the item ending; the queue owns that verdict."""
    seen, adapter = adapter_emissions

    adapter.on_stage(1, 5, "Parsing subtitles")
    before = len(seen)
    adapter.on_complete()

    assert len(seen) == before


def test_adapter_carries_the_stage_across_a_later_stages_items(adapter_emissions):
    """Regression (issue #1): no stage-1 prefix glued onto a later stage's items."""
    seen, adapter = adapter_emissions

    adapter.on_stage(3, 5, "Extracting media")
    adapter.on_start(2, "Extracting media")
    adapter.on_progress(1, "Extracting media: word-01")
    adapter.on_stage(4, 5, "Fetching definitions")
    adapter.on_start(2, "Fetching definitions")
    adapter.on_progress(1, "Definition found: word-01")

    assert seen[-1] == (4, "Stage 4 of 5 · Fetching definitions · Definition found: word-01 (1 of 2)")
    assert not any("Extracting media: Fetching definitions" in label for _, label in seen)


def test_adapter_emits_nonfatal_errors_without_advancing_progress(adapter_emissions):
    """A recoverable media loss is a warning, not ordinary progress."""
    seen, _adapter = adapter_emissions
    warnings: list[tuple[int, str, str]] = []
    adapter = QueueMiningProgressAdapter(
        idx=4,
        emit=lambda idx, label: seen.append((idx, label)),
        warning=lambda idx, item, error: warnings.append((idx, item, error)),
    )

    adapter.on_error("word-01", "boom")

    assert seen == []
    assert warnings == [(4, "word-01", "boom")]


def test_queue_routes_nonfatal_adapter_errors_to_activity():
    warnings: list[str] = []
    tab = SimpleNamespace(
        log_widget=SimpleNamespace(append_warning=warnings.append),
    )
    adapter = QueueMiningProgressAdapter(
        idx=2,
        emit=lambda _idx, _label: pytest.fail("warning advanced progress"),
        warning=lambda idx, item, error: _QueueMiningTabBase._on_item_warning(tab, idx, item, error),
    )

    adapter.on_error("word-01", "audio extraction failed")
    adapter.on_complete()

    assert warnings == ["word-01: audio extraction failed"]


def test_running_queue_worker_supplies_the_adapter_warning_channel(qapp):
    class WarningWorker(SequentialQueueWorker[object]):
        def _run_queue(self) -> None:
            adapter = QueueMiningProgressAdapter(idx=3, emit=self.item_progress.emit)
            adapter.on_error("word-02", "screenshot extraction failed")
            self.item_finished.emit(3, "completed-result", None, 1)

    worker = WarningWorker(
        processor=object(),  # type: ignore[arg-type]
        config=object(),  # type: ignore[arg-type]
        items=[],
        curation_callback=None,
    )
    warnings: list[tuple[int, str, str]] = []
    finished: list[tuple[int, object, object, int]] = []
    worker.item_warning.connect(lambda idx, item, error: warnings.append((idx, item, error)))
    worker.item_finished.connect(lambda idx, result, error, attempts: finished.append((idx, result, error, attempts)))

    worker.run()

    assert warnings == [(3, "word-02", "screenshot extraction failed")]
    assert finished == [(3, "completed-result", None, 1)]
