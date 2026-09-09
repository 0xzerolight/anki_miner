"""Tests for the Word Curator's expression-audio prefetch work function."""

from __future__ import annotations

import pytest

from anki_miner.gui.workers.expression_audio_prefetch import (
    ExpressionAudioProbeChannel,
    run_expression_audio_prefetch,
)
from anki_miner.models import TokenizedWord


def _word(lemma: str = "食べる") -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="たべる",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )


def _channel(qtbot):
    """A relay plus the list of everything it emits.

    ``qtbot`` is taken only to guarantee the QApplication exists; the channel is
    a bare QObject, not a widget, so there is nothing to ``addWidget``. Emitter
    and receiver are on the same thread here, so the connection is direct and
    every emit lands before the call returns.
    """
    channel = ExpressionAudioProbeChannel()
    seen: list[tuple[int, bool]] = []
    channel.word_resolved.connect(lambda index, found: seen.append((index, found)))
    return channel, seen


def test_emits_one_result_per_pending_word(qtbot):
    channel, seen = _channel(qtbot)
    pending = [(0, _word("食べる")), (2, _word("猫"))]

    run_expression_audio_prefetch(
        lambda word, _cancelled_check=None: word.lemma == "食べる",
        pending,
        channel,
        lambda: False,
    )

    assert seen == [(0, True), (2, False)]


def test_the_index_is_the_original_word_index_not_the_position(qtbot):
    channel, seen = _channel(qtbot)

    run_expression_audio_prefetch(lambda word, _cancelled_check=None: True, [(7, _word())], channel, lambda: False)

    assert seen == [(7, True)]


def test_a_cancel_before_the_first_word_emits_nothing(qtbot):
    channel, seen = _channel(qtbot)
    asked: list[str] = []

    def _fetch(word, cancelled_check=None):
        asked.append(word.lemma)
        return True

    run_expression_audio_prefetch(_fetch, [(0, _word())], channel, lambda: True)

    assert seen == []
    assert asked == []


def test_a_cancel_during_a_word_drops_that_word_s_result(qtbot):
    """The window is gone by then, so there is no row left to paint."""
    channel, seen = _channel(qtbot)
    cancelled = []

    def _fetch(word, cancelled_check=None):
        cancelled.append(True)
        return True

    run_expression_audio_prefetch(_fetch, [(0, _word())], channel, lambda: bool(cancelled))

    assert seen == []


def test_the_cancel_check_is_forwarded_to_the_fetcher(qtbot):
    channel, _seen = _channel(qtbot)
    received: list[object] = []

    def _fetch(word, cancelled_check=None):
        received.append(cancelled_check)
        return False

    def _cancelled() -> bool:
        return False

    run_expression_audio_prefetch(_fetch, [(0, _word())], channel, _cancelled)

    assert received == [_cancelled]


def test_a_raising_fetcher_propagates_to_the_worker_catch_all(qtbot):
    """The function does NOT swallow: SingleCallWorker.report_failure classifies it."""
    channel, _seen = _channel(qtbot)

    def _fetch(word, cancelled_check=None):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        run_expression_audio_prefetch(_fetch, [(0, _word())], channel, lambda: False)


def test_no_pending_words_does_nothing(qtbot):
    channel, seen = _channel(qtbot)

    run_expression_audio_prefetch(lambda word, _c=None: True, [], channel, lambda: False)

    assert seen == []
