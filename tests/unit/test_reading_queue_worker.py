"""Tests for :class:`ReadingQueueWorker`.

The queue worker drives a list of :class:`ReadingQueueItem` through per-item
``detector.load`` + ``EpisodeProcessor.process_reading`` sequentially — no
fetch stage, no retry (attempts is always 1). Tests exercise the worker body
synchronously by calling ``run()`` directly; Qt threading itself is not under
test. The worker owns the item lifecycle, so tests assert both the emitted
signals and the mutated item state.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.reading_queue_worker import ReadingQueueWorker
from anki_miner.models.mining_queue import ReadyItemStatus
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.models.reading_queue import ReadingQueueItem
from tests.unit._queue_worker_harness import (
    connect_all as _connect_all,
)
from tests.unit._queue_worker_harness import (
    make_mock_processor,
    make_queue_worker_factory,
)


def _make_item(stem: str = "vol01", kind: str = "epub") -> ReadingQueueItem:
    """Build a READY queue item for a synthetic reading source."""
    ref = ReadingSourceRef(
        kind=kind,
        path=Path(f"/books/{stem}.{kind}"),
        image_root=None,
        title=stem,
        volume=None,
    )
    return ReadingQueueItem(source=ref, title=stem, kind=kind)


def _result(cards: int) -> SimpleNamespace:
    """A stand-in ProcessingResult carrying a cards_created count."""
    return SimpleNamespace(cards_created=cards)


@pytest.fixture
def mock_processor():
    """MagicMock stand-in for EpisodeProcessor."""
    return make_mock_processor("process_reading", _result(3))


@pytest.fixture
def fake_load(monkeypatch):
    """Patch ``detector.load`` to return a per-source document without I/O."""
    load_mock = MagicMock(side_effect=lambda source: SimpleNamespace(doc_for=source.title))
    monkeypatch.setattr(
        "anki_miner.gui.workers.reading_queue_worker.detector.load",
        load_mock,
    )
    return load_mock


@pytest.fixture
def make_worker(qapp, mock_processor, test_config, fake_load):
    """Factory producing a ReadingQueueWorker with sensible defaults."""
    return make_queue_worker_factory(ReadingQueueWorker, mock_processor, test_config, _make_item)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_two_item_success_signal_sequence(make_worker, mock_processor, fake_load):
    items = [_make_item("vol01"), _make_item("vol02")]
    results = [_result(4), _result(7)]
    mock_processor.process_reading.side_effect = lambda *a, **kw: results.pop(0)

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert caps["started"].calls == [(0,), (1,)]
    assert [c[0] for c in caps["finished"].calls] == [0, 1]
    assert [c[2] for c in caps["finished"].calls] == [None, None]  # no error
    assert [c[3] for c in caps["finished"].calls] == [1, 1]  # attempts
    assert len(caps["queue_finished"].calls) == 1

    # Each source was loaded and mined once, in order.
    assert [c.args[0].title for c in fake_load.call_args_list] == ["vol01", "vol02"]
    assert mock_processor.process_reading.call_count == 2

    # Worker owns the item lifecycle: statuses COMPLETED, cards_created set.
    assert [i.status for i in items] == [
        ReadyItemStatus.COMPLETED,
        ReadyItemStatus.COMPLETED,
    ]
    assert [i.cards_created for i in items] == [4, 7]
    assert [i.error_message for i in items] == [None, None]


def test_failed_result_marks_item_error(make_worker, mock_processor, fake_load):
    """A non-raising failed ProcessingResult routes to ERROR, not COMPLETED."""
    from anki_miner.models import ProcessingResult

    failed = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=["ffmpeg exploded"])
    mock_processor.process_reading.side_effect = lambda *a, **kw: failed
    items = [_make_item("vol01")]

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert items[0].status == ReadyItemStatus.ERROR
    assert items[0].error_message == "ffmpeg exploded"
    # item_finished carries the error string (result=None), so the tab logs a failure.
    assert caps["finished"].calls[0][1] is None
    assert caps["finished"].calls[0][2] == "ffmpeg exploded"


def test_cancelled_result_marks_item_ready(make_worker, mock_processor, fake_load):
    """A Stop-mid-mine cancelled result leaves the item re-minable (READY)."""
    from anki_miner.models import ProcessingResult
    from anki_miner.models.processing import CANCELLED_ERROR

    cancelled = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=[CANCELLED_ERROR])
    mock_processor.process_reading.side_effect = lambda *a, **kw: cancelled
    items = [_make_item("vol01")]

    worker = make_worker(items=items)
    worker.run()

    assert items[0].status == ReadyItemStatus.READY
    assert items[0].error_message is None


def test_process_reading_receives_loaded_document(make_worker, mock_processor, fake_load):
    doc = SimpleNamespace(name="loaded-doc")
    fake_load.side_effect = lambda source: doc
    items = [_make_item("vol01")]

    worker = make_worker(items=items)
    worker.run()

    call = mock_processor.process_reading.call_args
    assert call.args == (doc,)


# ---------------------------------------------------------------------------
# Load failure ends only that item; the queue continues
# ---------------------------------------------------------------------------


def test_load_setuperror_on_first_item_continues_queue(make_worker, mock_processor, fake_load):
    items = [_make_item("vol01"), _make_item("vol02")]

    def _load(source):
        if source.title == "vol01":
            raise SetupError("This EPUB is DRM-protected and cannot be mined.")
        return SimpleNamespace(doc_for=source.title)

    fake_load.side_effect = _load
    mock_processor.process_reading.side_effect = lambda *a, **kw: _result(9)

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # Item 1 errored on load; item 2 still mined.
    assert items[0].status is ReadyItemStatus.ERROR
    assert items[0].error_message == "This EPUB is DRM-protected and cannot be mined."
    assert items[0].cards_created == 0
    assert items[1].status is ReadyItemStatus.COMPLETED
    assert items[1].cards_created == 9

    # SetupError message surfaced verbatim (no type prefix) via item_finished.
    assert caps["finished"].calls == [
        (0, None, "This EPUB is DRM-protected and cannot be mined.", 1),
        (1, results_result := caps["finished"].calls[1][1], None, 1),
    ]
    assert results_result.cards_created == 9
    # The failed source was never mined; only the surviving one was.
    assert mock_processor.process_reading.call_count == 1
    assert len(caps["queue_finished"].calls) == 1


def test_mining_exception_on_first_item_continues_queue(make_worker, mock_processor, fake_load):
    items = [_make_item("vol01"), _make_item("vol02")]

    def _side_effect(document, **kw):
        if document.doc_for == "vol01":
            raise ValueError("boom")
        return _result(2)

    mock_processor.process_reading.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    # Non-SetupError failures keep the type prefix.
    assert items[0].status is ReadyItemStatus.ERROR
    assert items[0].error_message == "ValueError: boom"
    assert items[1].status is ReadyItemStatus.COMPLETED
    assert caps["finished"].calls[0] == (0, None, "ValueError: boom", 1)
    assert caps["finished"].calls[1][2] is None
    assert len(caps["queue_finished"].calls) == 1
    assert mock_processor.process_reading.call_count == 2


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_mid_queue_stops_before_next_item(make_worker, mock_processor, fake_load):
    items = [_make_item("a"), _make_item("b"), _make_item("c")]
    worker_box: dict = {}

    def _cancel_mid_mine(document, **kw):
        # User pressed Stop mid-pipeline: the worker's _cancel_event (passed to
        # process_reading as cancel_event) makes the processor's next checkpoint
        # return a cancelled result (no raise) — modelled here by the mock's
        # return — and the loop-top check must then stop the queue.
        worker_box["worker"].cancel()
        return _result(1)

    mock_processor.process_reading.side_effect = _cancel_mid_mine

    worker = make_worker(items=items)
    worker_box["worker"] = worker
    caps = _connect_all(worker)
    worker.run()

    # Items 2 and 3 never started; no sticky processor.cancel().
    assert mock_processor.process_reading.call_count == 1
    mock_processor.cancel.assert_not_called()
    assert caps["started"].calls == [(0,)]
    assert [c[0] for c in caps["finished"].calls] == [0]
    # queue_finished still fires: the loop-top break exits the loop normally.
    assert len(caps["queue_finished"].calls) == 1


def test_curation_reject_stops_queue_before_next_item(make_worker, mock_processor, fake_load):
    """A curation reject stops the whole queue, not just the current volume.

    Models the fixed ``_show_curation_dialog`` reject branch: the callback cancels
    the running worker and returns ``None`` (the cancelled-curation contract). The
    loop-top check must then stop before the next item, so the curator is never
    requested again — the exact behaviour the bug lacked.
    """
    items = [_make_item("a"), _make_item("b"), _make_item("c")]
    worker_box: dict = {}
    curation_calls: list = []

    def _reject_curation(words):
        curation_calls.append(words)
        worker_box["worker"].cancel()  # what the fixed GUI slot now does on reject
        return None  # cancelled-curation contract

    def _mine(document, **kw):
        curated = kw["curation_callback"](["w"])
        return _result(0) if curated is None else _result(3)

    mock_processor.process_reading.side_effect = _mine

    worker = make_worker(items=items, curation_callback=_reject_curation)
    worker_box["worker"] = worker
    caps = _connect_all(worker)
    worker.run()

    # Curator requested for item 0 only; items 1 and 2 never started.
    assert curation_calls == [["w"]]
    assert caps["started"].calls == [(0,)]
    assert [c[0] for c in caps["finished"].calls] == [0]
    mock_processor.cancel.assert_not_called()  # event-only cancel, no sticky poison
    assert len(caps["queue_finished"].calls) == 1


def test_cancel_before_run_emits_queue_finished_only(make_worker, mock_processor, fake_load):
    items = [_make_item("a"), _make_item("b")]
    worker = make_worker(items=items)
    worker.cancel()
    caps = _connect_all(worker)

    worker.run()

    assert caps["started"].calls == []
    assert caps["finished"].calls == []
    assert fake_load.call_count == 0
    assert mock_processor.process_reading.call_count == 0
    assert len(caps["queue_finished"].calls) == 1


def test_worker_cancel_event_passed_to_process_reading(make_worker, mock_processor, fake_load):
    """Stop mid-mine must reach the processor's checkpoints: the worker's own
    _cancel_event is handed to process_reading as cancel_event (NOT the sticky
    processor.cancel(), which would poison the shared processor across runs)."""
    worker = make_worker(items=[_make_item()])
    worker.run()

    kwargs = mock_processor.process_reading.call_args.kwargs
    assert kwargs["cancel_event"] is worker._cancel_event
    mock_processor.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# process_reading kwargs
# ---------------------------------------------------------------------------


def test_process_reading_kwargs(make_worker, mock_processor, fake_load):
    def _curation(words):
        return words

    doc = SimpleNamespace(name="doc")
    fake_load.side_effect = lambda source: doc
    items = [_make_item("my_manga", kind="mokuro")]
    worker = make_worker(items=items, curation_callback=_curation)
    worker.run()

    call = mock_processor.process_reading.call_args
    assert call.args == (doc,)
    assert call.kwargs["curation_callback"] is _curation
    assert "progress_callback" in call.kwargs
    assert call.kwargs["cancel_event"] is worker._cancel_event
    # Reading path passes no video-only kwargs.
    assert "audio_only" not in call.kwargs
    assert "episode_name_override" not in call.kwargs
    assert "series_name_override" not in call.kwargs


def test_none_curation_callback_passed_through(make_worker, mock_processor, fake_load):
    worker = make_worker(items=[_make_item()], curation_callback=None)
    worker.run()

    kwargs = mock_processor.process_reading.call_args.kwargs
    assert kwargs["curation_callback"] is None


# ---------------------------------------------------------------------------
# Progress adapter wiring
# ---------------------------------------------------------------------------


def test_progress_callback_routes_to_item_progress_signal(make_worker, mock_processor, fake_load):
    items = [_make_item("a")]
    captured_cb = []

    def _capture(document, **kw):
        captured_cb.append(kw["progress_callback"])
        return _result(0)

    mock_processor.process_reading.side_effect = _capture

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    cb = captured_cb[0]
    cb.on_start(10, "Fetching definitions")
    cb.on_progress(5, "word-05")
    cb.on_complete()

    assert caps["progress"].calls == [
        (0, "Fetching definitions", 0),
        (0, "word-05", 50),
        (0, "Complete", 100),
    ]


# ---------------------------------------------------------------------------
# D8 (amended): no video curation attributes; manga publishes curation_document
# ---------------------------------------------------------------------------


def test_no_curation_media_attributes_after_run(make_worker, mock_processor, fake_load):
    worker = make_worker(items=[_make_item()])
    worker.run()

    assert not hasattr(worker, "_curation_video")
    assert not hasattr(worker, "_curation_subtitle")
    assert not hasattr(worker, "_curation_offset")


def test_curation_document_none_before_run(make_worker):
    # A constructed-but-not-run worker reads as "no document" (the manga tab's
    # context builder relies on this instead of getattr fallbacks).
    worker = make_worker(items=[_make_item()])
    assert worker.curation_document is None


def test_curation_document_is_loaded_document_at_curation_time(make_worker, mock_processor, fake_load):
    seen: list[object] = []

    def _capture(document, **kwargs):
        # process_reading is where the curation callback would park the worker;
        # by then the published document must already be the loaded one.
        seen.append(worker.curation_document)
        assert worker.curation_document is document
        return _result(1)

    mock_processor.process_reading.side_effect = _capture
    worker = make_worker(items=[_make_item("vol01")])
    worker.run()

    assert len(seen) == 1
    assert seen[0].doc_for == "vol01"  # type: ignore[union-attr]


def test_curation_document_updates_per_item(make_worker, mock_processor, fake_load):
    seen: list[str] = []

    def _capture(document, **kwargs):
        seen.append(document.doc_for)
        assert worker.curation_document is document
        return _result(1)

    mock_processor.process_reading.side_effect = _capture
    worker = make_worker(items=[_make_item("vol01"), _make_item("vol02")])
    worker.run()

    assert seen == ["vol01", "vol02"]


# ---------------------------------------------------------------------------
# curation_processor property
# ---------------------------------------------------------------------------


def test_curation_processor_returns_built_processor(make_worker, mock_processor):
    worker = make_worker(items=[])
    assert worker.curation_processor is mock_processor


# ---------------------------------------------------------------------------
# processor_factory path — construction deferred to the worker thread
# ---------------------------------------------------------------------------


def test_factory_path_builds_processor_inside_run(qapp, test_config, fake_load):
    """Given processor_factory and processor=None, run() builds the processor
    before mining, calls it, and curation_processor returns it."""
    built = MagicMock(name="EpisodeProcessor")
    built.process_reading = MagicMock(return_value=_result(1))
    calls: list[int] = []

    def factory():
        calls.append(1)
        return built

    worker = ReadingQueueWorker(
        processor=None,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        processor_factory=factory,
    )
    # Before run(), processor is None (not yet built).
    assert worker.curation_processor is None

    caps = _connect_all(worker)
    worker.run()

    assert calls == [1]
    assert worker.curation_processor is built
    built.process_reading.assert_called_once()
    assert len(caps["queue_finished"].calls) == 1


def test_factory_path_error_emits_error_and_queue_finished(qapp, test_config, fake_load):
    """A factory that raises emits error + queue_finished and mines nothing."""

    def bad_factory():
        raise RuntimeError("registry scan failed")

    worker = ReadingQueueWorker(
        processor=None,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        processor_factory=bad_factory,
    )
    errors: list[str] = []
    worker.error.connect(errors.append)
    caps = _connect_all(worker)

    worker.run()

    assert len(errors) == 1
    assert "registry scan failed" in errors[0]
    # No item ever started; queue_finished still fires so the tab recovers.
    assert caps["started"].calls == []
    assert fake_load.call_count == 0
    assert len(caps["queue_finished"].calls) == 1


def test_prebuilt_processor_path_does_not_use_factory(qapp, mock_processor, test_config, fake_load):
    """When a processor is supplied directly, no factory is invoked."""
    worker = ReadingQueueWorker(
        processor=mock_processor,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
    )
    worker.run()

    assert worker.curation_processor is mock_processor


def test_both_processor_and_factory_raises(qapp, mock_processor, test_config):
    """Supplying both processor and processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="not both"):
        ReadingQueueWorker(
            processor=mock_processor,
            config=test_config,
            items=[_make_item()],
            curation_callback=None,
            processor_factory=lambda: mock_processor,
        )


def test_neither_processor_nor_factory_raises(qapp, test_config):
    """Supplying neither processor nor processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="Either processor or processor_factory"):
        ReadingQueueWorker(
            processor=None,
            config=test_config,
            items=[_make_item()],
            curation_callback=None,
        )


# ---------------------------------------------------------------------------
# Schema-staleness pre-loop gate — abort once, no per-item rows
# ---------------------------------------------------------------------------


def test_stale_dict_aborts_queue_once(qapp, mock_processor, test_config, fake_load):
    """A stale enabled dict slot surfaces the error exactly once (no per-item
    failure rows) and still emits queue_finished so the tab recovers."""
    from unittest.mock import patch

    worker = ReadingQueueWorker(
        processor=mock_processor,
        config=test_config,
        items=[_make_item("vol01"), _make_item("vol02"), _make_item("vol03")],
        curation_callback=None,
    )
    errors: list[str] = []
    worker.error.connect(errors.append)
    caps = _connect_all(worker)

    with patch(
        "anki_miner.gui.workers.reading_queue_worker.stale_dict_reimport_error",
        return_value="Dictionary 'X' needs reimport (schema upgrade) — Settings → Dictionaries → Reimport All",
    ):
        worker.run()

    assert len(errors) == 1
    assert "Reimport All" in errors[0]
    # Abort-once: nothing loaded, started, or finished for any of the three items.
    assert fake_load.call_count == 0
    assert caps["started"].calls == []
    assert caps["finished"].calls == []
    assert len(caps["queue_finished"].calls) == 1
    mock_processor.process_reading.assert_not_called()
