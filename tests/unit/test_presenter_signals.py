"""Signal-contract tests for :class:`GUIPresenter` and :class:`GUIProgressCallback`.

Both Qt bridges implement their protocols by structural subtyping (they are
``QObject`` subclasses, NOT Protocol subclasses — see the metaclass-conflict
note in ``gui_presenter.py``). The contract under test: every protocol method
emits its paired Qt signal carrying the exact same payload, so a worker-thread
call lands on the main thread unchanged.

``PresenterProtocol`` is ``@runtime_checkable``; the isinstance checks below
confirm both the Qt and Null implementations satisfy it structurally.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from PyQt6.QtCore import QCoreApplication

from anki_miner.gui.presenters.gui_presenter import GUIPresenter
from anki_miner.gui.presenters.gui_progress_callback import GUIProgressCallback
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.presenters import NullPresenter

# Qt needs a core application for signal connection. Created once per process.
_app = QCoreApplication.instance() or QCoreApplication([])


class _Capture:
    """Collect signal emissions (any arity) as tuples."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args) -> None:
        self.calls.append(args)


# ===========================================================================
# GUIPresenter — method → paired signal with same payload
# ===========================================================================


def test_show_info_emits_info_signal():
    p = GUIPresenter()
    cap = _Capture()
    p.info_signal.connect(cap)

    p.show_info("hello")

    assert cap.calls == [("hello",)]


def test_show_success_emits_success_signal():
    p = GUIPresenter()
    cap = _Capture()
    p.success_signal.connect(cap)

    p.show_success("done")

    assert cap.calls == [("done",)]


def test_show_warning_emits_warning_signal():
    p = GUIPresenter()
    cap = _Capture()
    p.warning_signal.connect(cap)

    p.show_warning("careful")

    assert cap.calls == [("careful",)]


def test_show_error_emits_error_signal():
    p = GUIPresenter()
    cap = _Capture()
    p.error_signal.connect(cap)

    p.show_error("broken")

    assert cap.calls == [("broken",)]


def test_show_validation_result_emits_validation_signal_with_same_object():
    p = GUIPresenter()
    cap = _Capture()
    p.validation_result_signal.connect(cap)
    result = MagicMock(name="ValidationResult")

    p.show_validation_result(result)

    assert cap.calls == [(result,)]
    assert cap.calls[0][0] is result


def test_show_processing_result_emits_processing_signal_with_same_object():
    p = GUIPresenter()
    cap = _Capture()
    p.processing_result_signal.connect(cap)
    result = MagicMock(name="ProcessingResult")

    p.show_processing_result(result)

    assert cap.calls == [(result,)]
    assert cap.calls[0][0] is result


def test_show_word_preview_emits_word_preview_signal_with_same_list():
    p = GUIPresenter()
    cap = _Capture()
    p.word_preview_signal.connect(cap)
    words = [MagicMock(name="TokenizedWord")]

    p.show_word_preview(words)

    assert cap.calls == [(words,)]
    # The same list object is delivered (identity preserved across the signal).
    assert cap.calls[0][0] == words


def test_gui_presenter_satisfies_runtime_checkable_protocol():
    """The Qt presenter structurally satisfies the runtime-checkable protocol."""
    assert isinstance(GUIPresenter(), PresenterProtocol)


def test_null_presenter_satisfies_runtime_checkable_protocol():
    """The silent test presenter satisfies the same protocol."""
    assert isinstance(NullPresenter(), PresenterProtocol)


# ===========================================================================
# GUIProgressCallback — method → paired signal with same payload
# ===========================================================================


def test_on_start_emits_start_signal_with_total_and_description():
    cb = GUIProgressCallback()
    cap = _Capture()
    cb.start_signal.connect(cap)

    cb.on_start(10, "Extracting")

    assert cap.calls == [(10, "Extracting")]


def test_on_progress_emits_progress_signal_with_current_and_description():
    cb = GUIProgressCallback()
    cap = _Capture()
    cb.progress_signal.connect(cap)

    cb.on_progress(3, "word-03")

    assert cap.calls == [(3, "word-03")]


def test_on_complete_emits_complete_signal_with_no_args():
    cb = GUIProgressCallback()
    cap = _Capture()
    cb.complete_signal.connect(cap)

    cb.on_complete()

    assert cap.calls == [()]


def test_on_error_emits_error_signal_with_item_and_message():
    cb = GUIProgressCallback()
    cap = _Capture()
    cb.error_signal.connect(cap)

    cb.on_error("ep01.mkv", "ffmpeg failed")

    assert cap.calls == [("ep01.mkv", "ffmpeg failed")]
