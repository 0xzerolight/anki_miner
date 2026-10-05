"""Readability against known words (#132), on the mining Phase-2 basis.

- known/unknown is ``WordFilterService.filter_unknown`` (kana-variant and language fold included);
- the lemma bridge ``{w.lemma for w in unknown}`` is ``EpisodeProcessor._phase2_filter``'s;
- a line's bucket counts its unknown card fronts, the ``filter_i_plus_one`` intersection
  (a hand-built line without fronts counts lemmas).

Approximation kept for the occurrence share: counts arrive per lemma, so when
one lemma has two card fronts and only one is known, all of that lemma's
occurrences count as unknown.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from anki_miner.models.readability import ReadabilityStats

if TYPE_CHECKING:
    from anki_miner.models import LineLemmas, TokenizedWord
    from anki_miner.services.word_filter import WordFilterService


def measure(
    words: list[TokenizedWord],
    line_index: list[LineLemmas],
    lemma_counts: Mapping[str, int],
    known_forms: set[str],
    word_filter: WordFilterService,
) -> ReadabilityStats:
    """Score one parsed file against the learner's known forms."""
    unknown = word_filter.filter_unknown(words, known_forms)
    unknown_lemmas = {w.lemma for w in unknown}
    unknown_fronts = {w.mined_form for w in unknown}
    buckets = [0, 0, 0]
    for line in line_index:
        unknown_on_line = line.fronts & unknown_fronts if line.front_spans else line.lemmas & unknown_lemmas
        buckets[min(len(unknown_on_line), 2)] += 1
    return ReadabilityStats(
        word_count=sum(lemma_counts.values()),
        unknown_count=sum(n for lemma, n in lemma_counts.items() if lemma in unknown_lemmas),
        new_words=frozenset(w.mined_form for w in unknown),
        line_buckets=(buckets[0], buckets[1], buckets[2]),
    )


def combine(stats: Iterable[ReadabilityStats]) -> ReadabilityStats:
    """The run total: summed occurrences and lines, unioned new words (never a mean of file percentages)."""
    words = unknown = 0
    new: set[str] = set()
    buckets = [0, 0, 0]
    for s in stats:
        words += s.word_count
        unknown += s.unknown_count
        new |= s.new_words
        for i, n in enumerate(s.line_buckets):
            buckets[i] += n
    return ReadabilityStats(words, unknown, frozenset(new), (buckets[0], buckets[1], buckets[2]))
