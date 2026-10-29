"""MS-25 sync report and MS-24 unheard gaps (services/book_sync/report.py)."""

from __future__ import annotations

import pytest

from anki_miner.services.book_sync.aligner import BookCursor, SentenceTiming
from anki_miner.services.book_sync.report import (
    BookSyncReport,
    UnheardGap,
    book_unheard,
    build_report,
    grade_for,
    speech_coverage,
    unheard_gaps,
)


@pytest.mark.parametrize(
    ("sentence_pct", "audio_pct", "grade"),
    [
        (100, 100, "A"),
        (90, 95, "A"),
        (89, 99, "B"),
        (99, 75, "B"),
        (74, 100, "C"),
        (50, 50, "C"),
        (49, 100, "D"),
        (100, 0, "D"),
    ],
)
def test_the_grade_is_set_by_the_weaker_percentage(sentence_pct, audio_pct, grade):
    assert grade_for(sentence_pct, audio_pct) == grade


def test_report_percentages_round_down_so_the_grade_matches_what_is_shown():
    report = BookSyncReport(
        "neko.epub", sentences_timed=899, sentences_spanned=1000, speech_seconds=100.0, matched_seconds=95.0
    )

    assert report.sentence_pct == 89  # 89.9 shown as 89, never as 90
    assert report.audio_pct == 95
    assert report.grade == "B"


def test_an_empty_report_scores_zero():
    report = BookSyncReport("x.epub", sentences_timed=0, sentences_spanned=0, speech_seconds=0.0, matched_seconds=0.0)
    assert (report.sentence_pct, report.audio_pct, report.grade) == (0, 0, "D")


def test_build_report_counts_the_cursor_timed_sentences_across_files():
    cursor = BookCursor(sentence=40, timed=set(range(10, 40)) - {20, 21})

    report = build_report("neko.epub", cursor, speech_seconds=200.0, matched_seconds=180.0)

    assert report is not None
    assert (report.sentences_timed, report.sentences_spanned) == (28, 30)
    assert (report.sentence_pct, report.audio_pct, report.grade) == (93, 90, "A")


def test_build_report_is_none_when_nothing_was_timed():
    assert build_report("neko.epub", BookCursor(), 10.0, 0.0) is None


def test_speech_coverage_counts_speech_inside_cues_only():
    segments = [(0.0, 10.0, "a"), (5.0, 12.0, "b"), (20.0, 30.0, "c"), (40.0, 50.0, "   ")]
    timings = [SentenceTiming(0, 2.0, 4.0), SentenceTiming(1, 11.0, 25.0), SentenceTiming(2, 60.0, 70.0)]

    speech, matched = speech_coverage(segments, timings)

    assert speech == pytest.approx(22.0)  # [0, 12] + [20, 30]; the blank segment is no speech
    assert matched == pytest.approx(2.0 + 1.0 + 5.0)


def test_unheard_gaps_are_the_runs_between_timed_sentences():
    timings = [
        SentenceTiming(0, 0.0, 1.0),
        SentenceTiming(1, 1.0, 2.0),
        SentenceTiming(5, 9.0, 10.0),
        SentenceTiming(6, 10.0, 11.0),
        SentenceTiming(9, 20.0, 21.0),
    ]

    assert unheard_gaps(timings) == [UnheardGap(2, 4, 2.0, 9.0), UnheardGap(7, 8, 11.0, 20.0)]


def test_no_gap_is_guessed_where_the_cues_run_backwards():
    """Sentence 2 was read after sentence 9 (MS-24): between 2 and 9 no time range can be guessed."""
    timings = [SentenceTiming(0, 0.0, 1.0), SentenceTiming(9, 5.0, 6.0), SentenceTiming(2, 50.0, 51.0)]

    assert unheard_gaps(timings) == [UnheardGap(1, 1, 1.0, 50.0)]


def test_sentences_another_file_timed_are_never_unheard():
    """MS-24: this file's span covers 2-6 and 8-11, which an earlier file of the book timed."""
    timings = [SentenceTiming(1, 0.0, 1.0), SentenceTiming(7, 5.0, 6.0), SentenceTiming(12, 9.0, 10.0)]

    gaps = unheard_gaps(timings, known={2, 3, 5, 6, 8, 9, 10, 11})

    assert gaps == [UnheardGap(4, 4, 1.0, 5.0)]


def test_the_books_unheard_runs_span_its_files_and_drop_what_a_later_file_timed():
    """File 1 left 2-4 and 9 unheard, file 2 left 10-11; file 2 then timed 3 (MS-24), so 2-4 splits."""
    gaps = [UnheardGap(2, 4, 1.0, 5.0), UnheardGap(9, 9, 8.0, 9.0), UnheardGap(10, 11, 0.0, 2.0)]

    assert book_unheard(gaps, timed={0, 1, 3, 5, 8, 12}) == [(2, 2), (4, 4), (9, 11)]


def test_a_book_with_no_gaps_has_no_unheard_runs():
    assert book_unheard([], timed={0, 1}) == []
