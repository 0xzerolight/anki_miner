"""How readable a subtitle file is for this learner (Utilities → Readability, #132)."""

from __future__ import annotations

from dataclasses import dataclass

#: ``line_buckets`` indices: lines with no unknown lemma, exactly one (i+1), two or more.
I_PLUS_0, I_PLUS_1, I_PLUS_2_OR_MORE = 0, 1, 2


@dataclass(frozen=True)
class ReadabilityStats:
    """One file's (or one run's) readability.

    ``word_count`` / ``unknown_count`` count OCCURRENCES of mineable words, not
    distinct words. ``new_words`` holds the distinct unknown ``mined_form``s, so a
    run total can union them. ``line_buckets`` covers only lines with at least
    one content lemma; the parser's line index skips the rest.
    """

    word_count: int
    unknown_count: int
    new_words: frozenset[str]
    line_buckets: tuple[int, int, int]

    @property
    def known_pct(self) -> float | None:
        """Share of word occurrences already known, or ``None`` with no words."""
        if self.word_count == 0:
            return None
        return (self.word_count - self.unknown_count) / self.word_count * 100.0

    @property
    def line_count(self) -> int:
        """Lines with at least one content word."""
        return sum(self.line_buckets)

    def line_pct(self, bucket: int) -> float | None:
        """Share of lines in *bucket* (an ``I_PLUS_*`` index), or ``None`` with no lines."""
        total = self.line_count
        return None if total == 0 else self.line_buckets[bucket] / total * 100.0
