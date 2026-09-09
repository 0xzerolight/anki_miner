"""Background expression-audio prefetch for the Word Curator (FUTURE_IDEAS item 4).

Deliberately not a ``CancellableWorker`` subclass. The prefetch needs a thread,
cancellation, ownership that survives a closing window, a bounded join at app
close and a named log identity — and ``gui/utils/run_off_thread`` already owns
every one of those for the short-lived background work in this app. What it
does not own is a per-item result, because it delivers one value at the end,
so that is all this module adds: a GUI-thread relay object to emit through, and
the plain loop that runs on the worker thread.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.models import TokenizedWord


class ExpressionAudioProbeChannel(QObject):
    """GUI-thread relay carrying one Audio-column answer per row.

    Constructed on the GUI thread and parented to the TAB rather than to the
    curator window, so it cannot be destroyed under a worker that is still
    emitting; the window going away simply drops the connection, and an emit
    with no receiver is a no-op. The prefetch emits on it from the worker
    thread. Emitting a signal across threads is safe, and because this object's
    receiver — the Word Curator — lives on the GUI thread while the emitting
    thread does not, Qt's auto-connection is queued, which is exactly the
    delivery a table update needs. One is built per curated item and deleted
    when its dispatch finishes; the tab would otherwise collect a dead relay
    per item for the whole session.

    A relay rather than a signal on the worker so the thread side of this
    feature can stay a plain function that ``run_off_thread`` dispatches.
    """

    #: ``(original word index, audio found)`` for one resolved row.
    word_resolved = pyqtSignal(int, bool)


def run_expression_audio_prefetch(
    fetch_fn: Callable[[TokenizedWord, Callable[[], bool] | None], bool],
    pending: Sequence[tuple[int, TokenizedWord]],
    channel: ExpressionAudioProbeChannel,
    cancelled_check: Callable[[], bool],
) -> None:
    """Resolve the curator's unknown Audio cells, one word at a time.

    The zero-network probe that runs before the window opens answers every word
    some source already holds; the rest cannot be answered without asking, and
    this is what asks — in table order, emitting each answer as it lands so
    cells fill in under the user rather than all at once at the end.

    It runs while the mining worker is parked in the curation gate, so it has
    the run's chained fetcher to itself. That is enforced, not assumed:
    ``MiningTabBase`` cancels this dispatch before releasing the gate and joins
    it on the mining thread the moment that thread unparks. Words the user goes
    on to keep therefore hit a warm cache in phase 3; words they reject cost a
    fetch a later run would have paid anyway.

    Raises nothing of its own and swallows nothing either: ``fetch_fn`` is the
    run's chained fetcher, which never raises, and anything that does escape
    belongs to ``SingleCallWorker.run``'s catch-all, where ``report_failure``
    classifies it at the right volume.

    Args:
        fetch_fn: ``EpisodeProcessor.expression_audio_curation_fn``.
        pending: ``(original word index, word)`` pairs, in table order, for the
            rows the pre-dialog probe left unanswered.
        channel: The GUI-thread relay to emit each answer on.
        cancelled_check: Live cancellation predicate, supplied by
            ``run_off_thread(..., pass_cancel_check=True)``.
    """
    for index, word in pending:
        if cancelled_check():
            return
        found = fetch_fn(word, cancelled_check)
        # Re-checked after the fetch: a cancel that landed mid-word means the
        # window is gone, and the row it belonged to no longer exists to paint.
        if cancelled_check():
            return
        channel.word_resolved.emit(index, bool(found))
