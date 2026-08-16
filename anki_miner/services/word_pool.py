"""Season-wide curation pooling for batch mining.

Pure helpers (no Qt) behind the batch tab's one-curator-per-season flow:
a capture pre-pass grabs every episode's filtered words through the
existing curation hook, ``merge_pools`` folds them into one deduped list
for a single curator showing, and ``split_selection`` hands each episode
back its curated subset for the mine pass.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from pathlib import Path

from anki_miner.models.word import TokenizedWord
from anki_miner.services.stats_service import StatsService

logger = logging.getLogger(__name__)


def stamp_episode(words: list[TokenizedWord], video: Path) -> None:
    """Mark ``words`` (and their candidates) as sourced from ``video``.

    ``TokenizedWord.video_file`` is the per-word episode carrier the
    season curator and ``split_selection`` key on.
    """
    for word in words:
        word.video_file = video
        for candidate in word.sentence_candidates:
            candidate.video_file = video


def _leaf_copy(word: TokenizedWord) -> TokenizedWord:
    # Candidate entries must be leaves (empty sentence_candidates — the
    # documented invariant _swap_word_to_line also enforces); inserting the
    # word itself would make the list self-referential.
    return dataclasses.replace(word, sentence_candidates=[])


def merge_pools(
    pools: list[list[TokenizedWord]],
    *,
    max_candidates: int | None = None,
) -> list[TokenizedWord]:
    """Fold per-episode word lists into one deduped season pool.

    One row per ``mined_form``; the first-seen episode's word is the
    primary. Sentence candidates are the ordered concatenation of each
    episode's candidate set (a bare word contributes a leaf copy of
    itself); ``occurrence_count`` becomes the season total. A word seen
    once on a single line keeps its empty candidate list, so the
    curator's sentence picker stays hidden — matching the per-episode
    invariant that a non-empty list always holds the current pick plus
    at least one alternative.

    ``max_candidates`` is unbounded by default, matching
    ``WordFilterService.attach_sentence_candidates``: the picker scrolls,
    so a season word keeps every line it was mineable on. Merging builds
    no new variants (the per-episode pass already did), so the only cost
    of the concatenation is memory. Pass an int to bound the list.
    """
    merged: dict[str, TokenizedWord] = {}
    candidates: dict[str, list[TokenizedWord]] = {}
    for pool in pools:
        for word in pool:
            key = word.mined_form
            contribution = (
                [_leaf_copy(c) for c in word.sentence_candidates] if word.sentence_candidates else [_leaf_copy(word)]
            )
            if key not in merged:
                merged[key] = word
                candidates[key] = contribution
            else:
                primary = merged[key]
                primary.occurrence_count += word.occurrence_count
                candidates[key].extend(contribution)
    for key, primary in merged.items():
        cands = candidates[key] if max_candidates is None else candidates[key][:max_candidates]
        # Single-episode, single-line word: leave the picker off.
        primary.sentence_candidates = cands if len(cands) > 1 else []
    return list(merged.values())


def split_selection(
    selected: list[TokenizedWord],
) -> dict[Path | None, list[TokenizedWord]]:
    """Partition curated words by their episode (``video_file``).

    ``get_selected_words`` substitutes the chosen sentence variant, so
    each word's ``video_file`` names the episode it mines from. A word
    with no stamp (defensive; should not occur) joins the first group.
    """
    subsets: dict[Path | None, list[TokenizedWord]] = {}
    strays: list[TokenizedWord] = []
    for word in selected:
        if word.video_file is None:
            strays.append(word)
        else:
            subsets.setdefault(word.video_file, []).append(word)
    if strays:
        if subsets:
            first_key = next(iter(subsets))
            logger.warning(
                "%d curated word(s) missing episode stamp; mining from %s",
                len(strays),
                first_key,
            )
            subsets[first_key].extend(strays)
        else:
            subsets[None] = strays
    return subsets


class CaptureCurationCallback:
    """Curation callback for the season pre-pass.

    Stamps the incoming words with the current episode, records them,
    and returns ``[]`` so ``process_episode`` completes as a zero-card
    success (phases 3-5 skipped). ``suppress_curation_messages`` tells
    ``_run_curation`` to keep its per-episode info lines quiet — the
    worker narrates the pre-pass itself.
    """

    suppress_curation_messages = True

    def __init__(self) -> None:
        self.pools: list[list[TokenizedWord]] = []
        self._episode: Path | None = None

    def set_episode(self, video: Path) -> None:
        self._episode = video

    def __call__(self, words: list[TokenizedWord]) -> list[TokenizedWord]:
        if self._episode is not None:
            stamp_episode(words, self._episode)
        self.pools.append(words)
        return []


def fixed_selection(subset: list[TokenizedWord]) -> Callable[[list], list]:
    """Mine-pass curation callback: return ``subset`` verbatim.

    ``_run_curation`` consumes the callback's return directly, so the curated
    objects (chosen sentence, ``clip_override``, episode timings) pass through
    to phases 3-5 untouched.
    """

    def _callback(words: list) -> list:
        del words
        return subset

    return _callback


class MinePassStats(StatsService):
    """StatsService variant whose ``record_difficulty`` is a no-op.

    The season pre-pass already recorded one difficulty row per episode
    (the ``[]`` zero-card path records difficulty on success); the mine
    pass would insert a duplicate. A subclass rather than a duck proxy:
    ``EpisodeProcessor.stats_service`` is annotated as the concrete
    ``StatsService``. Same sqlite file — ``_ensure_loaded`` lazy-inits.
    """

    def __init__(self, inner: StatsService) -> None:
        super().__init__(inner._db_path)

    def record_difficulty(
        self,
        series_name: str,
        episode_name: str,
        total_words: int,
        unknown_words: int,
    ) -> None:
        del series_name, episode_name, total_words, unknown_words
