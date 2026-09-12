"""Windowed, monotonic transcript→book alignment (services/book_sync/aligner.py).

The fixtures fabricate a "book" of numbered sentences and a "transcript" read
from it with controlled damage, so every expected timing is derivable by hand:
segment k covers seconds [k, k+1).
"""

from __future__ import annotations

import random

from anki_miner.services.book_sync.aligner import (
    BookCursor,
    BookText,
    SentenceTiming,
    TimedText,
    align_to_book,
)
from anki_miner.services.book_sync.normalize import normalize_for_alignment

_KANA = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをん"


def _sentence(i: int, rng: random.Random) -> str:
    body = "".join(rng.choice(_KANA) for _ in range(rng.randint(18, 40)))
    return f"{body}。"


def _book(n: int, seed: int = 7) -> BookText:
    rng = random.Random(seed)
    sentences = tuple(_sentence(i, rng) for i in range(n))
    return BookText(title="t", sentences=sentences, keys=tuple(normalize_for_alignment(s) for s in sentences))


def _read(
    book: BookText, first: int, last: int, *, rng: random.Random, damage: float = 0.0, seg_chars: int = 30
) -> list[TimedText]:
    """Fabricate Whisper segments reading sentences first..last with ``damage`` char substitution rate."""
    text = "".join(book.sentences[first : last + 1])
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch != "。" and rng.random() < damage:
            chars[i] = rng.choice(_KANA)
    text = "".join(chars)
    segs: list[TimedText] = []
    for k, start in enumerate(range(0, len(text), seg_chars)):
        segs.append(TimedText(start=float(k), end=float(k + 1), text=text[start : start + seg_chars]))
    return segs


def _index_of(segs: list[TimedText], book: BookText, first: int, index: int) -> tuple[int, int]:
    """(first segment, last segment) carrying a piece of sentence ``index`` of a read that started at ``first``.

    Offsets come from the book, not from searching the transcript: damage
    changes characters but never lengths, so the segment layout is exact.
    """
    start = sum(len(s) for s in book.sentences[first:index])
    end = start + len(book.sentences[index]) - 1
    per_seg = [len(s.text) for s in segs]
    bounds = []
    acc = 0
    for n in per_seg:
        bounds.append((acc, acc + n))
        acc += n
    first = next(i for i, (a, b) in enumerate(bounds) if a <= start < b)
    last = next(i for i, (a, b) in enumerate(bounds) if a <= end < b)
    return first, last


def _assert_monotonic(timings: list[SentenceTiming]) -> None:
    for prev, cur in zip(timings, timings[1:], strict=False):
        assert cur.index > prev.index
        assert cur.start >= prev.end
        assert cur.end > cur.start


def test_clean_reading_times_every_sentence_inside_its_segments():
    book = _book(40)
    segs = _read(book, 0, 39, rng=random.Random(1))
    cursor = BookCursor()

    timings = align_to_book(segs, book, cursor)

    assert [t.index for t in timings] == list(range(40))
    _assert_monotonic(timings)
    for t in timings:
        first, last = _index_of(segs, book, 0, t.index)
        assert first <= t.start < last + 1.0
        assert first < t.end <= last + 1.0
    assert cursor.sentence == 40


def test_noisy_reading_still_times_almost_every_sentence():
    book = _book(120)
    segs = _read(book, 0, 119, rng=random.Random(2), damage=0.15)

    timings = align_to_book(segs, book, BookCursor())

    assert len(timings) >= 110
    _assert_monotonic(timings)
    for t in timings:
        first, last = _index_of(segs, book, 0, t.index)
        assert first - 1 <= t.start <= last + 2  # never drifts more than a segment either side


def test_long_book_spans_many_windows_without_drift():
    book = _book(600)  # ~15k chars → ~8 transcript windows
    segs = _read(book, 0, 599, rng=random.Random(3), damage=0.08)

    timings = align_to_book(segs, book, BookCursor())

    assert len(timings) >= 570
    _assert_monotonic(timings)
    last = timings[-1]
    first_seg, last_seg = _index_of(segs, book, 0, last.index)
    assert first_seg - 1 <= last.start <= last_seg + 2


def test_transcript_intro_not_in_book_is_skipped():
    """A narrator's preamble before chapter 1 has no book text; the aligner re-anchors after it."""
    book = _book(60)
    rng = random.Random(4)
    intro = [TimedText(float(k), float(k + 1), "".join(rng.choice(_KANA) for _ in range(30))) for k in range(40)]
    body = _read(book, 0, 59, rng=rng)
    body = [TimedText(s.start + 40, s.end + 40, s.text) for s in body]
    log: list[str] = []

    timings = align_to_book(intro + body, book, BookCursor(), log=log.append)

    assert any(line.startswith("Skipped") for line in log)  # the prefix probe dropped the intro
    assert timings[0].index == 0
    assert timings[0].start >= 40.0
    assert len(timings) >= 55
    _assert_monotonic(timings)


def test_small_unread_preface_is_absorbed_by_the_window_slack():
    """25 skipped sentences (~700 chars) fit inside the first book window's slack: no re-anchor needed."""
    book = _book(80)
    segs = _read(book, 25, 79, rng=random.Random(5))  # audio starts at sentence 25
    cursor = BookCursor()

    timings = align_to_book(segs, book, cursor)

    assert timings[0].index == 25
    assert all(t.index >= 25 for t in timings)
    assert len(timings) >= 52
    assert cursor.sentence == 80


def test_large_unread_preface_forces_a_reanchor():
    """150 skipped sentences (~4 300 chars) exceed any book window: the first
    window matches nothing, the fuzzy search jumps the cursor, and the rest aligns."""
    book = _book(300)
    segs = _read(book, 150, 299, rng=random.Random(5))  # ~4 300 transcript chars → 3 windows
    cursor = BookCursor()
    log: list[str] = []

    timings = align_to_book(segs, book, cursor, log=log.append)

    assert any("Re-anchored" in line for line in log)
    assert timings[0].index == 150
    assert all(t.index >= 150 for t in timings)
    assert len(timings) >= 140
    assert cursor.sentence == 300


def test_cursor_carries_across_audio_files():
    book = _book(60)
    cursor = BookCursor()
    part1 = _read(book, 0, 29, rng=random.Random(6))
    part2 = _read(book, 30, 59, rng=random.Random(7))

    t1 = align_to_book(part1, book, cursor)
    assert t1[-1].index == 29 and cursor.sentence == 30
    t2 = align_to_book(part2, book, cursor)

    assert t2[0].index == 30 and t2[-1].index == 59
    assert t2[0].start < 1.0  # file 2's timings are relative to file 2


def test_untimed_sentences_between_timed_ones_are_absent_not_invented():
    book = _book(30)
    segs = _read(book, 0, 9, rng=random.Random(8)) + [
        TimedText(s.start + 20, s.end + 20, s.text) for s in _read(book, 20, 29, rng=random.Random(9))
    ]

    timings = align_to_book(segs, book, BookCursor())

    indices = {t.index for t in timings}
    assert set(range(0, 10)) <= indices and set(range(20, 30)) <= indices
    assert not (set(range(10, 20)) & indices)


def test_empty_inputs():
    book = _book(5)
    assert align_to_book([], book, BookCursor()) == []
    assert align_to_book([TimedText(0.0, 1.0, "…")], book, BookCursor()) == []
    exhausted = BookCursor(sentence=5)
    assert align_to_book(_read(book, 0, 4, rng=random.Random(1)), book, exhausted) == []


def test_cancel_check_stops_between_windows():
    book = _book(600)
    segs = _read(book, 0, 599, rng=random.Random(3))
    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    timings = align_to_book(segs, book, BookCursor(), cancel_check=cancel)
    assert 0 < len(timings) < 600


def test_minimum_cue_duration_and_no_overlap():
    book = _book(20)
    # ten sentences crammed into one 0.4 s segment → clipping must still yield ordered, ≥0.3 s cues
    text = "".join(book.sentences[:10])
    segs = [TimedText(0.0, 0.4, text)]
    timings = align_to_book(segs, book, BookCursor())
    _assert_monotonic(timings)
    assert all(t.end - t.start >= 0.3 - 1e-9 for t in timings)
