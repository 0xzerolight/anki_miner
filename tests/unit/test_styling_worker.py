"""Tests for :class:`StylingWorker` (Issue #44).

The worker reads the note type's CSS, edits the managed block, and writes it
back — one AnkiConnect read + one write, off the GUI thread. Tests pin:

- mode dispatch (``apply`` composes + inserts the block; ``remove`` strips it),
- read-before-write ordering (a read failure surfaces with no write),
- mid-run cancel between read and write (no write, no success),
- the two error branches (``AnkiConnectionError`` vs generic).

StylingWorker was NOT merged into ``SingleCallWorker`` (two distinct service
calls + a finished_ok payload), so it is tested as its own class. Exercised
synchronously via ``run()``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.workers.styling_worker import StylingWorker
from anki_miner.services.dictionary.card_styling import BEGIN_MARKER, END_MARKER


class _Capture:
    """Collect single-arg signal emissions."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, value) -> None:
        self.calls.append(value)


def _make_worker(service, *, mode="apply", note_type="Japanese-1.0"):
    return StylingWorker(
        service,
        mode=mode,
        preset="dark",
        custom_css=".card { color: red; }",
        note_type=note_type,
    )


# ---------------------------------------------------------------------------
# Mode dispatch
# ---------------------------------------------------------------------------


def test_apply_mode_reads_then_writes_composed_block():
    service = MagicMock()
    service.get_model_styling.return_value = ".card {}"
    worker = _make_worker(service, mode="apply")
    ok = _Capture()
    worker.finished_ok.connect(ok)

    worker.run()

    service.get_model_styling.assert_called_once_with("Japanese-1.0")
    service.update_model_styling.assert_called_once()
    written_css, written_note = service.update_model_styling.call_args.args
    # The managed block markers must be present in the written CSS.
    assert BEGIN_MARKER in written_css
    assert END_MARKER in written_css
    # The user's pre-existing card CSS is preserved alongside our block.
    assert ".card {}" in written_css
    assert written_note == "Japanese-1.0"
    assert ok.calls == ["Applied styles to 'Japanese-1.0'."]


def test_remove_mode_strips_block_and_writes():
    managed = f".card {{}}\n{BEGIN_MARKER}\n.card {{ color: red; }}\n{END_MARKER}\n"
    service = MagicMock()
    service.get_model_styling.return_value = managed
    worker = _make_worker(service, mode="remove")
    ok = _Capture()
    worker.finished_ok.connect(ok)

    worker.run()

    service.update_model_styling.assert_called_once()
    written_css, _ = service.update_model_styling.call_args.args
    # The managed markers are gone after a strip; user CSS survives.
    assert BEGIN_MARKER not in written_css
    assert END_MARKER not in written_css
    assert ".card {}" in written_css
    assert ok.calls == ["Removed Anki Miner styles from 'Japanese-1.0'."]


# ---------------------------------------------------------------------------
# Read-before-write ordering
# ---------------------------------------------------------------------------


def test_read_failure_surfaces_before_any_write():
    """A failed get_model_styling must error out with NO update_model_styling."""
    service = MagicMock()
    service.get_model_styling.side_effect = AnkiConnectionError("Anki not running")
    worker = _make_worker(service, mode="apply")
    errors = _Capture()
    ok = _Capture()
    worker.error.connect(errors)
    worker.finished_ok.connect(ok)

    worker.run()

    service.update_model_styling.assert_not_called()
    assert ok.calls == []
    assert errors.calls == ["Anki not running"]


def test_read_happens_strictly_before_write():
    """Ordering guard: the read is observed before the write is attempted."""
    service = MagicMock()
    order: list[str] = []
    service.get_model_styling.side_effect = lambda nt: (order.append("read"), ".card {}")[1]
    service.update_model_styling.side_effect = lambda css, nt: order.append("write")

    worker = _make_worker(service, mode="apply")
    worker.run()

    assert order == ["read", "write"]


# ---------------------------------------------------------------------------
# Mid-run cancel
# ---------------------------------------------------------------------------


def test_cancel_before_run_skips_read_and_write():
    service = MagicMock()
    service.get_model_styling.return_value = ".card {}"
    worker = _make_worker(service)
    ok = _Capture()
    worker.finished_ok.connect(ok)

    worker.cancel()
    worker.run()

    service.get_model_styling.assert_not_called()
    service.update_model_styling.assert_not_called()
    assert ok.calls == []


def test_cancel_between_read_and_write_skips_write():
    """A cancel landing during the read returns before the write — no partial
    state and no finished_ok."""
    service = MagicMock()

    def _read(note_type):
        worker.cancel()  # user navigated away while the read was in flight
        return ".card {}"

    service.get_model_styling.side_effect = _read
    worker = _make_worker(service, mode="apply")
    ok = _Capture()
    worker.finished_ok.connect(ok)

    worker.run()

    service.get_model_styling.assert_called_once()
    service.update_model_styling.assert_not_called()
    assert ok.calls == []


def test_success_suppressed_when_cancelled_after_write():
    """A cancel landing during the write swallows the finished_ok emit."""
    service = MagicMock()
    service.get_model_styling.return_value = ".card {}"

    def _write(css, note_type):
        worker.cancel()

    service.update_model_styling.side_effect = _write
    worker = _make_worker(service, mode="apply")
    ok = _Capture()
    worker.finished_ok.connect(ok)

    worker.run()

    service.update_model_styling.assert_called_once()
    assert ok.calls == []


# ---------------------------------------------------------------------------
# Error branches
# ---------------------------------------------------------------------------


def test_anki_connection_error_emitted_bare():
    """AnkiConnectionError surfaces with its own message, no prefix."""
    service = MagicMock()
    service.get_model_styling.return_value = ".card {}"
    service.update_model_styling.side_effect = AnkiConnectionError("connection refused")
    worker = _make_worker(service, mode="apply")
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == ["connection refused"]


def test_generic_exception_gets_styling_prefix():
    """A non-AnkiConnectionError failure is wrapped with the 'Styling update failed:' prefix."""
    service = MagicMock()
    service.get_model_styling.return_value = ".card {}"
    service.update_model_styling.side_effect = ValueError("bad css")
    worker = _make_worker(service, mode="apply")
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == ["Styling update failed: bad css"]


def test_error_suppressed_when_cancelled_during_failure():
    """A failure coinciding with a cancel stays silent."""
    service = MagicMock()

    def _read(note_type):
        worker.cancel()
        raise ValueError("boom")

    service.get_model_styling.side_effect = _read
    worker = _make_worker(service, mode="apply")
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == []
