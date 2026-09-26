from __future__ import annotations

import json
import os
import sys
import threading

from anki_miner.cli.events import SCHEMA_VERSION, EventPresenter, EventProgress, EventSink, fd_writer
from anki_miner.interfaces.presenter import PresenterProtocol


def _sink() -> tuple[EventSink, list[bytes]]:
    lines: list[bytes] = []
    return EventSink(write=lines.append), lines


def _records(lines: list[bytes]) -> list[dict]:
    return [json.loads(line) for line in b"".join(lines).decode("ascii").splitlines()]


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1


def test_emit_writes_one_json_line_with_event_key() -> None:
    sink, lines = _sink()
    sink.emit("start", items=2)
    assert len(lines) == 1 and lines[0].endswith(b"\n")
    assert _records(lines) == [{"event": "start", "items": 2}]


def test_current_item_is_stamped_unless_given_and_never_on_result() -> None:
    sink, lines = _sink()
    sink.current_item = 3
    sink.emit("message", level="info", text="x")
    sink.emit("item_done", item=1)
    sink.emit("result", status="success")
    records = _records(lines)
    assert records[0]["item"] == 3
    assert records[1]["item"] == 1
    assert "item" not in records[2]


def test_non_ascii_round_trips_as_ascii_json() -> None:
    sink, lines = _sink()
    path = "/media/アニメ/第01話.mkv"
    sink.emit("item_start", input={"video": path})
    b"".join(lines).decode("ascii")  # must not raise
    assert _records(lines)[0]["input"]["video"] == path


def test_paths_and_other_objects_serialise_via_str(tmp_path) -> None:
    sink, lines = _sink()
    sink.emit("item_start", input={"video": tmp_path / "a.mkv"})
    assert _records(lines)[0]["input"]["video"] == str(tmp_path / "a.mkv")


def test_default_writer_uses_fd1_even_when_sys_stdout_is_none(capfd, monkeypatch) -> None:
    # A console=False frozen Windows build leaves sys.stdout as None while fd 1
    # is still the caller's pipe (see _ffsubsync_child.py).
    monkeypatch.setattr(sys, "stdout", None)
    EventSink().emit("result", status="success")
    assert json.loads(capfd.readouterr().out) == {"event": "result", "status": "success"}


def test_fd_writer_writes_everything_to_the_given_fd() -> None:
    read_fd, write_fd = os.pipe()
    try:
        fd_writer(write_fd)(b"x" * 1000)
        os.close(write_fd)
        assert os.read(read_fd, 2000) == b"x" * 1000
    finally:
        os.close(read_fd)


def test_write_failure_is_swallowed() -> None:
    def broken(_: bytes) -> None:
        raise OSError("EPIPE")

    EventSink(write=broken).emit("result", status="success")  # must not raise


def test_emit_is_thread_safe_whole_lines() -> None:
    sink, lines = _sink()

    def burst() -> None:
        for i in range(200):
            sink.emit("progress", current=i)

    threads = [threading.Thread(target=burst) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_records(lines)) == 800


def test_presenter_satisfies_protocol_and_maps_levels() -> None:
    sink, lines = _sink()
    presenter = EventPresenter(sink)
    assert isinstance(presenter, PresenterProtocol)
    presenter.show_info("i")
    presenter.show_success("s")
    presenter.show_warning("w")
    presenter.show_error("e")
    presenter.show_stage(2, 5, "Filtering")
    presenter.show_processing_result(object())
    presenter.show_run_details(object())
    presenter.show_validation_result(object())
    assert _records(lines) == [
        {"event": "message", "item": None, "level": "info", "text": "i"},
        {"event": "message", "item": None, "level": "success", "text": "s"},
        {"event": "message", "item": None, "level": "warning", "text": "w"},
        {"event": "message", "item": None, "level": "error", "text": "e"},
        {"event": "stage", "item": None, "index": 2, "total": 5, "name": "Filtering"},
    ]


def test_progress_reports_counts_and_ignores_stage_duplicates() -> None:
    sink, lines = _sink()
    progress = EventProgress(sink)
    progress.on_stage(2, 5, "Filtering")  # presenter.show_stage already reports it
    progress.on_start(40, "Looking up")
    progress.on_progress(12, "Looking up")
    progress.on_complete()
    progress.on_error("Lookup", "boom")
    assert _records(lines) == [
        {"event": "progress", "item": None, "current": 0, "total": 40, "desc": "Looking up"},
        {"event": "progress", "item": None, "current": 12, "total": 40, "desc": "Looking up"},
        {"event": "message", "item": None, "level": "error", "text": "Lookup: boom"},
    ]
