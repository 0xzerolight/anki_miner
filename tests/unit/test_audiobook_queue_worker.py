"""Tests for :class:`AudiobookQueueWorker`.

The queue worker drives a list of :class:`AudiobookQueueItem` through mining
sequentially — no fetch stage, no retry (attempts is always 1). Tests
exercise the worker body synchronously by calling ``run()`` directly; Qt
threading itself is not under test.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.workers.audiobook_queue_worker import AudiobookQueueWorker
from anki_miner.models.audiobook_queue import AudiobookQueueItem


class _SignalCapture:
    """Collect emissions from a Qt signal for later inspection."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args) -> None:
        self.calls.append(args)


def _make_item(stem: str = "book01") -> AudiobookQueueItem:
    """Build a READY queue item for a synthetic audio/subtitle pair."""
    return AudiobookQueueItem(
        audio_file=Path(f"/audio/{stem}.mp3"),
        subtitle_file=Path(f"/audio/{stem}.srt"),
    )


@pytest.fixture
def mock_processor():
    """MagicMock stand-in for EpisodeProcessor."""
    processor = MagicMock()
    processor.process_episode = MagicMock(return_value=MagicMock(name="ProcessingResult"))
    return processor


@pytest.fixture
def make_worker(qapp, mock_processor, test_config):
    """Factory producing an AudiobookQueueWorker with sensible defaults."""

    def _make(
        items: list[AudiobookQueueItem] | None = None,
        curation_callback=None,
        preview_mode: bool = False,
        config=None,
    ) -> AudiobookQueueWorker:
        if items is None:
            items = [_make_item()]
        return AudiobookQueueWorker(
            processor=mock_processor,
            config=config if config is not None else test_config,
            items=items,
            curation_callback=curation_callback,
            preview_mode=preview_mode,
        )

    return _make


def _connect_all(worker: AudiobookQueueWorker):
    """Wire capture objects to all queue worker signals; return them as a dict."""
    captures = {
        "started": _SignalCapture(),
        "progress": _SignalCapture(),
        "finished": _SignalCapture(),
        "queue_finished": _SignalCapture(),
    }
    worker.item_started.connect(captures["started"])
    worker.item_progress.connect(captures["progress"])
    worker.item_finished.connect(captures["finished"])
    worker.queue_finished.connect(captures["queue_finished"])
    return captures


# ---------------------------------------------------------------------------
# All success
# ---------------------------------------------------------------------------


def test_two_item_success_signal_sequence(make_worker, mock_processor):
    items = [_make_item("book01"), _make_item("book02")]
    results = ["R_1", "R_2"]
    mock_processor.process_episode.side_effect = lambda *a, **kw: results.pop(0)

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert caps["started"].calls == [(0,), (1,)]
    assert caps["finished"].calls == [
        (0, "R_1", None, 1),
        (1, "R_2", None, 1),
    ]
    assert len(caps["queue_finished"].calls) == 1
    assert mock_processor.process_episode.call_count == 2


# ---------------------------------------------------------------------------
# Error on one item does not halt the queue, and there is NO retry
# ---------------------------------------------------------------------------


def test_error_on_first_item_continues_queue(make_worker, mock_processor):
    items = [_make_item("book01"), _make_item("book02")]

    def _side_effect(audio, sub, **kw):
        if audio.stem == "book01":
            raise ValueError("boom")
        return "R_2"

    mock_processor.process_episode.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert caps["finished"].calls == [
        (0, None, "ValueError: boom", 1),
        (1, "R_2", None, 1),
    ]
    assert len(caps["queue_finished"].calls) == 1
    # No retry: the failing item is attempted exactly once.
    assert mock_processor.process_episode.call_count == 2


def test_attempts_always_one_in_item_finished(make_worker, mock_processor):
    items = [_make_item("a"), _make_item("b"), _make_item("c")]

    def _side_effect(audio, sub, **kw):
        if audio.stem == "b":
            raise RuntimeError("mid-queue failure")
        return f"R_{audio.stem}"

    mock_processor.process_episode.side_effect = _side_effect

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    assert [c[3] for c in caps["finished"].calls] == [1, 1, 1]


# ---------------------------------------------------------------------------
# Skip channel: GUI-removed items must not be mined
# ---------------------------------------------------------------------------


def test_skip_item_mid_run_emits_no_signals_for_skipped(make_worker, mock_processor):
    items = [_make_item("a"), _make_item("b"), _make_item("c")]
    worker_box: dict = {}

    def _skip_rest_while_mining_first(audio, sub, **kw):
        # Simulate the user removing the queued tail while item 1 is mining.
        worker_box["worker"].skip_item(items[1])
        worker_box["worker"].skip_item(items[2])
        return "R_a"

    mock_processor.process_episode.side_effect = _skip_rest_while_mining_first

    worker = make_worker(items=items)
    worker_box["worker"] = worker
    caps = _connect_all(worker)
    worker.run()

    assert mock_processor.process_episode.call_count == 1
    assert caps["started"].calls == [(0,)]
    assert caps["finished"].calls == [(0, "R_a", None, 1)]
    assert len(caps["queue_finished"].calls) == 1


def test_skip_item_before_run_skips_only_that_item(make_worker, mock_processor):
    items = [_make_item("a"), _make_item("b"), _make_item("c")]
    mock_processor.process_episode.side_effect = lambda audio, sub, **kw: f"R_{audio.stem}"

    worker = make_worker(items=items)
    worker.skip_item(items[1])
    caps = _connect_all(worker)
    worker.run()

    mined = [c.args[0].stem for c in mock_processor.process_episode.call_args_list]
    assert mined == ["a", "c"]
    # idx values still match the frozen snapshot positions (0 and 2).
    assert caps["started"].calls == [(0,), (2,)]
    assert caps["finished"].calls == [(0, "R_a", None, 1), (2, "R_c", None, 1)]
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_mid_queue_stops_before_next_item(make_worker, mock_processor):
    items = [_make_item("a"), _make_item("b"), _make_item("c")]
    worker_box: dict = {}

    def _cancel_mid_mine(audio, sub, **kw):
        # User pressed Stop mid-pipeline: the worker's _cancel_event (passed
        # to process_episode as cancel_event) makes the processor's next
        # phase checkpoint return a cancelled result (no raise) — modelled
        # here by the mock's return — and the loop-top check must then stop
        # the queue.
        worker_box["worker"].cancel()
        return "R_CANCELLED"

    mock_processor.process_episode.side_effect = _cancel_mid_mine

    worker = make_worker(items=items)
    worker_box["worker"] = worker
    caps = _connect_all(worker)
    worker.run()

    # Items 2 and 3 never started.
    assert mock_processor.process_episode.call_count == 1
    assert caps["started"].calls == [(0,)]
    assert caps["finished"].calls == [(0, "R_CANCELLED", None, 1)]
    # queue_finished still fires: the loop-top break exits the loop normally.
    assert len(caps["queue_finished"].calls) == 1


def test_cancel_before_run_emits_queue_finished_only(make_worker, mock_processor):
    items = [_make_item("a"), _make_item("b")]
    worker = make_worker(items=items)
    worker.cancel()
    caps = _connect_all(worker)

    worker.run()

    assert caps["started"].calls == []
    assert caps["finished"].calls == []
    assert mock_processor.process_episode.call_count == 0
    assert len(caps["queue_finished"].calls) == 1


# ---------------------------------------------------------------------------
# Curation bridge attrs published before the processor call
# ---------------------------------------------------------------------------


def test_curation_paths_published_before_processor_call(make_worker, mock_processor, test_config):
    # Nonzero offset: subtitle parsing applies config.subtitle_offset for all
    # sources, so the worker must publish it — not a hardcoded 0.
    config = replace(test_config, subtitle_offset=1.5)
    items = [_make_item("book01"), _make_item("book02")]
    observed: list[tuple] = []
    worker_box: dict = {}

    def _observe(audio, sub, **kw):
        w = worker_box["worker"]
        observed.append((w._curation_video, w._curation_subtitle, w._curation_offset))
        return "R"

    mock_processor.process_episode.side_effect = _observe

    worker = make_worker(items=items, config=config)
    worker_box["worker"] = worker
    worker.run()

    assert observed == [
        (items[0].audio_file, items[0].subtitle_file, 1.5),
        (items[1].audio_file, items[1].subtitle_file, 1.5),
    ]


def test_constructor_initial_curation_state(make_worker, mock_processor, test_config):
    config = replace(test_config, subtitle_offset=1.5)
    worker = make_worker(items=[], config=config)
    assert worker.curation_processor is mock_processor
    assert worker._curation_video is None
    assert worker._curation_subtitle is None
    assert worker._curation_offset == 1.5


# ---------------------------------------------------------------------------
# process_episode kwargs
# ---------------------------------------------------------------------------


def test_process_episode_kwargs(make_worker, mock_processor):
    def _curation(words):
        return words

    items = [_make_item("my_audiobook")]
    worker = make_worker(items=items, curation_callback=_curation, preview_mode=True)
    worker.run()

    call = mock_processor.process_episode.call_args
    assert call.args == (items[0].audio_file, items[0].subtitle_file)
    assert call.kwargs["audio_only"] is True
    assert call.kwargs["preview_mode"] is True
    assert call.kwargs["episode_name_override"] == "my_audiobook"
    assert call.kwargs["series_name_override"] == "Audio"
    assert call.kwargs["curation_callback"] is _curation


def test_worker_cancel_event_passed_to_process_episode(make_worker, mock_processor):
    """Stop mid-mine must reach the processor's checkpoints: the worker's own
    _cancel_event is handed to process_episode as cancel_event (NOT the sticky
    processor.cancel(), which would poison the shared processor across runs)."""
    worker = make_worker(items=[_make_item()])
    worker.run()

    kwargs = mock_processor.process_episode.call_args.kwargs
    assert kwargs["cancel_event"] is worker._cancel_event


def test_none_curation_callback_passed_through(make_worker, mock_processor):
    worker = make_worker(items=[_make_item()], curation_callback=None, preview_mode=False)
    worker.run()

    kwargs = mock_processor.process_episode.call_args.kwargs
    assert kwargs["curation_callback"] is None
    assert kwargs["preview_mode"] is False


# ---------------------------------------------------------------------------
# Progress adapter wiring
# ---------------------------------------------------------------------------


def test_progress_callback_routes_to_item_progress_signal(make_worker, mock_processor):
    items = [_make_item("a")]
    captured_cb = []

    def _capture(audio, sub, **kw):
        captured_cb.append(kw["progress_callback"])
        return "R"

    mock_processor.process_episode.side_effect = _capture

    worker = make_worker(items=items)
    caps = _connect_all(worker)
    worker.run()

    cb = captured_cb[0]
    cb.on_start(10, "Extracting media")
    cb.on_progress(5, "word-05")
    cb.on_complete()

    assert caps["progress"].calls == [
        (0, "Extracting media", 0),
        (0, "Extracting media: word-05", 50),
        (0, "Extracting media", 100),
    ]


# ---------------------------------------------------------------------------
# processor_factory path — construction deferred to the worker thread
# ---------------------------------------------------------------------------


def test_factory_path_builds_processor_inside_run(qapp, test_config):
    """Given processor_factory and processor=None, run() builds the processor
    before mining, calls it, and curation_processor returns it."""
    built = MagicMock(name="EpisodeProcessor")
    built.process_episode = MagicMock(return_value=MagicMock(name="ProcessingResult"))
    calls: list[int] = []

    def factory():
        calls.append(1)
        return built

    worker = AudiobookQueueWorker(
        processor=None,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        preview_mode=False,
        processor_factory=factory,
    )
    # Before run(), processor is None (not yet built).
    assert worker.curation_processor is None

    caps = _connect_all(worker)
    worker.run()

    assert calls == [1]
    assert worker.curation_processor is built
    built.process_episode.assert_called_once()
    assert len(caps["queue_finished"].calls) == 1


def test_factory_path_error_emits_error_and_queue_finished(qapp, test_config):
    """A factory that raises emits error + queue_finished and mines nothing."""

    def bad_factory():
        raise RuntimeError("registry scan failed")

    worker = AudiobookQueueWorker(
        processor=None,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        preview_mode=False,
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
    assert len(caps["queue_finished"].calls) == 1


def test_prebuilt_processor_path_does_not_use_factory(qapp, mock_processor, test_config):
    """When a processor is supplied directly, no factory is invoked."""
    factory = MagicMock(name="factory")
    # Both supplied would raise; here only processor is supplied.
    worker = AudiobookQueueWorker(
        processor=mock_processor,
        config=test_config,
        items=[_make_item()],
        curation_callback=None,
        preview_mode=False,
    )
    worker.run()

    factory.assert_not_called()
    assert worker.curation_processor is mock_processor


def test_both_processor_and_factory_raises(qapp, mock_processor, test_config):
    """Supplying both processor and processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="not both"):
        AudiobookQueueWorker(
            processor=mock_processor,
            config=test_config,
            items=[_make_item()],
            curation_callback=None,
            preview_mode=False,
            processor_factory=lambda: mock_processor,
        )


def test_neither_processor_nor_factory_raises(qapp, test_config):
    """Supplying neither processor nor processor_factory raises ValueError."""
    with pytest.raises(ValueError, match="Either processor or processor_factory"):
        AudiobookQueueWorker(
            processor=None,
            config=test_config,
            items=[_make_item()],
            curation_callback=None,
            preview_mode=False,
        )


# ---------------------------------------------------------------------------
# 4.0: schema-staleness pre-loop gate — abort once, no per-item rows
# ---------------------------------------------------------------------------


def test_stale_dict_aborts_queue_once(qapp, mock_processor, test_config):
    """A stale enabled dict slot surfaces the error exactly once (no per-item
    failure rows) and still emits queue_finished so the tab recovers."""
    from unittest.mock import patch

    worker = AudiobookQueueWorker(
        processor=mock_processor,
        config=test_config,
        items=[_make_item("book01"), _make_item("book02"), _make_item("book03")],
        curation_callback=None,
        preview_mode=False,
    )
    errors: list[str] = []
    worker.error.connect(errors.append)
    caps = _connect_all(worker)

    with patch(
        "anki_miner.gui.workers.audiobook_queue_worker.stale_dict_reimport_error",
        return_value="Dictionary 'X' needs reimport (schema upgrade) — Settings → Dictionaries → Reimport All",
    ):
        worker.run()

    assert len(errors) == 1
    assert "Reimport All" in errors[0]
    # Abort-once: no item was started/finished for any of the three items.
    assert caps["started"].calls == []
    assert caps["finished"].calls == []
    assert len(caps["queue_finished"].calls) == 1
    mock_processor.process_episode.assert_not_called()
