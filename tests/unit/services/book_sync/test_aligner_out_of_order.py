"""MS-24: a passage read out of book order is found behind the cursor (services/book_sync/aligner.py).

Same fixtures as test_aligner.py: a kana "book" of numbered sentences read back
as Whisper segments, segment k covering seconds [k, k+1).
"""

from __future__ import annotations

import random

from anki_miner.services.book_sync.aligner import BookCursor, TimedText, align_to_book
from tests.unit.services.book_sync.test_aligner import _book, _read


def _shifted(segs: list[TimedText], by: float) -> list[TimedText]:
    return [TimedText(s.start + by, s.end + by, s.text) for s in segs]


def test_a_foreword_read_after_the_story_is_timed_where_the_audio_has_it():
    """The audio reads sentences 100-299 first, then 0-99 (the foreword, read last)."""
    book = _book(300)
    story = _read(book, 100, 299, rng=random.Random(21))
    foreword = _shifted(_read(book, 0, 99, rng=random.Random(22)), len(story))
    cursor = BookCursor()
    log: list[str] = []

    timings = align_to_book(story + foreword, book, cursor, log=log.append)

    indices = [t.index for t in timings]
    assert len({i for i in indices if i >= 100}) >= 190
    assert len({i for i in indices if i < 100}) >= 80  # the window that straddles the switch is lost
    assert len(indices) == len(set(indices))  # no sentence timed twice
    starts = [t.start for t in timings]
    assert starts == sorted(starts)  # audio order, for the SRT
    for t in timings:
        if t.index < 100:
            assert t.start >= len(story) - 1  # the foreword's cues sit in the foreword's audio
    assert cursor.sentence == 300  # the main cursor is untouched by the detour
    assert cursor.timed == set(indices)
    assert any("out of book order" in line for line in log)


def test_a_chapter_skipped_then_read_later_in_the_same_file():
    """Sentences 0-99, then 200-299, then the skipped 100-199."""
    book = _book(300)
    first = _read(book, 0, 99, rng=random.Random(23))
    second = _shifted(_read(book, 200, 299, rng=random.Random(24)), len(first))
    late = _shifted(_read(book, 100, 199, rng=random.Random(25)), len(first) + len(second))
    cursor = BookCursor()

    timings = align_to_book(first + second + late, book, cursor)

    indices = {t.index for t in timings}
    assert len(indices & set(range(100, 200))) >= 80
    assert len(indices & set(range(0, 100))) >= 90
    assert len(indices & set(range(200, 300))) >= 70  # the forward jump loses its first window, as before
    starts = [t.start for t in timings]
    assert starts == sorted(starts)
    assert cursor.sentence == 300


def test_two_detours_read_in_reverse_book_order_keep_their_own_times():
    """Sentences 300-399, then 200-299, then 100-199: two detours, the earlier-heard one later in the book."""
    book = _book(400)
    first = _read(book, 300, 399, rng=random.Random(1))
    second = _shifted(_read(book, 200, 299, rng=random.Random(2)), len(first))
    third = _shifted(_read(book, 100, 199, rng=random.Random(3)), len(first) + len(second))
    cursor = BookCursor()

    timings = align_to_book(first + second + third, book, cursor)

    second_cues = [t for t in timings if 200 <= t.index < 300]
    third_cues = [t for t in timings if 100 <= t.index < 200]
    assert len(second_cues) >= 60 and len(third_cues) >= 70
    # Each passage's cues sit in its own audio (one second of slack for the boundary window).
    assert all(len(first) - 1 <= t.start < len(first) + len(second) for t in second_cues)
    assert all(len(first) + len(second) - 1 <= t.start < len(first) + len(second) + len(third) for t in third_cues)
    assert [t.start for t in timings] == sorted(t.start for t in timings)


def test_a_sentence_another_file_already_timed_is_not_timed_again():
    """File 2 repeats the end of file 1 (a recap): nothing is timed twice across the book."""
    book = _book(120)
    cursor = BookCursor()
    part1 = _read(book, 0, 99, rng=random.Random(26))
    t1 = align_to_book(part1, book, cursor)
    assert cursor.timed == {t.index for t in t1}

    recap = _read(book, 60, 99, rng=random.Random(27)) + _shifted(_read(book, 100, 119, rng=random.Random(28)), 60)
    t2 = align_to_book(recap, book, cursor)

    assert not ({t.index for t in t2} & {t.index for t in t1})
    assert {t.index for t in t2} >= set(range(105, 120))


def test_an_exhausted_cursor_still_finds_untimed_text_behind_it():
    """A last file that reads the foreword after the whole book was timed by earlier files."""
    book = _book(200)
    cursor = BookCursor()
    align_to_book(_read(book, 50, 199, rng=random.Random(29)), book, cursor)
    assert cursor.sentence == 200

    timings = align_to_book(_read(book, 0, 49, rng=random.Random(30)), book, cursor)

    assert len({t.index for t in timings} & set(range(50))) >= 45
    assert cursor.sentence == 200


def test_a_spanish_foreword_read_last_is_timed_too():
    """The same detour on a space-delimited book (Review Focus 3: no kana assumption)."""
    from anki_miner.services.book_sync.aligner import BookText
    from tests.unit.services.book_sync.test_aligner_space_delimited import _book as _es_book
    from tests.unit.services.book_sync.test_aligner_space_delimited import _read as _es_read
    from tests.unit.services.book_sync.test_aligner_space_delimited import _vocab

    vocab = _vocab("es")
    book = _es_book(vocab, 300, seed=1)
    story, _ = _es_read(book, vocab, random.Random(31), first=100)
    head = BookText(title="t", sentences=book.sentences[:100], keys=book.keys[:100])
    foreword, _ = _es_read(head, vocab, random.Random(32))
    cursor = BookCursor()

    timings = align_to_book(story + _shifted(foreword, len(story)), book, cursor)

    indices = {t.index for t in timings}
    assert len(indices & set(range(100, 300))) >= 180
    assert len(indices & set(range(100))) >= 75
    assert [t.start for t in timings] == sorted(t.start for t in timings)
    assert cursor.sentence >= 290  # the main pass ended in the story, not in the foreword


def test_a_detour_sentence_cut_by_a_window_boundary_keeps_its_whole_cue():
    """A detour that runs on into the next window resumes inside the sentence the
    previous window was cut in; that sentence's cue must reach its real end (Z.2 WB3 I-1)."""
    from tests.unit.services.book_sync.test_aligner import _index_of

    book = _book(300)
    story = _read(book, 100, 299, rng=random.Random(21))
    foreword_read = _read(book, 0, 99, rng=random.Random(22))
    cursor = BookCursor()

    timings = align_to_book(story + _shifted(foreword_read, len(story)), book, cursor)

    detour = [t for t in timings if t.index < 100]
    assert len(detour) >= 80
    short = []
    for t in detour:
        _first_seg, last_seg = _index_of(foreword_read, book, 0, t.index)
        if t.end < len(story) + last_seg:  # ends before the segment holding its last character starts
            short.append((t.index, round(len(story) + last_seg - t.end, 2)))
    assert short == []
