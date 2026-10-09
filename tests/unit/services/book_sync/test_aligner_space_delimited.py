"""Audiobook Sync on space-delimited languages (services/book_sync/aligner.py).

Two UNRELATED passages already share a character-level equal share that
depends on the alphabet: kana 0.25-0.27, Spanish, German and Russian
0.41-0.51 (the aligner's null estimate on books like these, see NULL_MARGIN).
A fixed floor tuned on kana (0.35) therefore never fires off-track there.
The books here are built from the words of the language fixtures (no
copyrighted text) and read back the way Whisper damages them: some words
dropped, some replaced, lowercase, no punctuation. Segment k covers seconds
[k, k+1).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import pytest

from anki_miner.services.book_sync import aligner
from anki_miner.services.book_sync.aligner import (
    MIN_WINDOW_MATCH,
    REANCHOR_MIN_SCORE,
    BookCursor,
    BookText,
    SentenceTiming,
    TimedText,
    align_to_book,
)
from anki_miner.services.book_sync.normalize import normalize_for_alignment
from tests.unit.services.book_sync.test_aligner import _book as _kana_book

_FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
_CORPORA = {"es": "es/pos_corpus.jsonl", "de": "de/pos_corpus.jsonl", "ru": "ru/tokens.jsonl"}
_WORD = re.compile(r"\w+")


def _vocab(code: str) -> list[str]:
    """Every word of the fixture's sentences, in file order (repeats kept, so common words stay common)."""
    words: list[str] = []
    for line in (_FIXTURES / _CORPORA[code]).read_text(encoding="utf-8").splitlines():
        if line.strip():
            words += _WORD.findall(json.loads(line)["sentence"])
    return words


def _book(vocab: list[str], n: int, seed: int) -> BookText:
    rng = random.Random(seed)
    sentences = []
    for _ in range(n):
        body = " ".join(rng.choice(vocab) for _ in range(rng.randint(8, 14)))
        sentences.append(f"{body[0].upper()}{body[1:]}.")
    return BookText(title="t", sentences=tuple(sentences), keys=tuple(normalize_for_alignment(s) for s in sentences))


def _read(
    book: BookText,
    vocab: list[str],
    rng: random.Random,
    *,
    drop: float = 0.1,
    sub: float = 0.1,
    wps: int = 10,
    first: int = 0,
) -> tuple[list[TimedText], dict[int, tuple[int, int]]]:
    """Segments of ``wps`` words reading sentences ``first``.. to the end, plus
    each read sentence's (first segment, last segment). A word is dropped with
    probability ``drop``, replaced by a random vocabulary word with ``sub``."""
    words: list[tuple[str, int]] = []
    for s in range(first, len(book.sentences)):
        for word in _WORD.findall(book.sentences[s]):
            roll = rng.random()
            if roll < drop:
                continue
            if roll < drop + sub:
                word = rng.choice(vocab)
            words.append((word.lower(), s))
    segments: list[TimedText] = []
    spans: dict[int, tuple[int, int]] = {}
    for k in range(0, len(words), wps):
        seg = k // wps
        chunk = words[k : k + wps]
        segments.append(TimedText(float(seg), float(seg + 1), " ".join(w for w, _ in chunk)))
        for _, s in chunk:
            lo, hi = spans.get(s, (seg, seg))
            spans[s] = (min(lo, seg), max(hi, seg))
    return segments, spans


def _assert_monotonic(timings: list[SentenceTiming]) -> None:
    for prev, cur in zip(timings, timings[1:], strict=False):
        assert cur.index > prev.index
        assert cur.start >= prev.end
        assert cur.end > cur.start


@pytest.mark.parametrize("code", sorted(_CORPORA))
def test_noisy_asr_reading_times_every_sentence_in_place(code):
    vocab = _vocab(code)
    book = _book(vocab, 300, seed=1)
    segments, spans = _read(book, vocab, random.Random(6))
    cursor = BookCursor()

    timings = align_to_book(segments, book, cursor)

    # A sentence that lost most of its words can miss MIN_SENTENCE_MATCH_SHARE; a gated-out window loses ~20.
    assert len(timings) >= 297
    _assert_monotonic(timings)
    for t in timings:
        first, last = spans[t.index]
        assert first - 1 <= t.start <= last + 2
    assert cursor.sentence == 300


@pytest.mark.parametrize("code", sorted(_CORPORA))
def test_an_unrelated_passage_times_nothing(code):
    """Same language, same words, a different text: nothing in it is in the book."""
    vocab = _vocab(code)
    book = _book(vocab, 300, seed=1)
    other = _book(vocab, 300, seed=3)
    segments, _ = _read(other, vocab, random.Random(4))
    cursor = BookCursor()

    timings = align_to_book(segments, book, cursor)

    assert timings == []
    assert cursor.sentence == 0


@pytest.mark.parametrize("code", sorted(_CORPORA))
def test_a_file_far_past_the_cursor_re_anchors(code):
    """The audio starts at sentence 150 while the cursor is still at 0; the
    first book window looks at unread text and must not be taken as a match."""
    vocab = _vocab(code)
    book = _book(vocab, 300, seed=1)
    segments, spans = _read(book, vocab, random.Random(5), first=150)
    cursor = BookCursor()

    timings = align_to_book(segments, book, cursor)

    assert timings
    assert timings[0].index >= 145
    _assert_monotonic(timings)
    for t in timings:
        if t.index in spans:
            first, last = spans[t.index]
            assert first - 1 <= t.start <= last + 2
    assert cursor.sentence == 300


def test_japanese_floor_is_unchanged():
    """Pins the kana FIXTURE's floors to the constants: its null + NULL_MARGIN sits ~0.004 under MIN_WINDOW_MATCH."""
    assert aligner._null_floors(_kana_book(600)) == (MIN_WINDOW_MATCH, REANCHOR_MIN_SCORE)


def test_a_book_too_short_to_calibrate_keeps_the_constants():
    vocab = _vocab("es")
    assert aligner._null_floors(_book(vocab, 20, seed=1)) == (MIN_WINDOW_MATCH, REANCHOR_MIN_SCORE)
