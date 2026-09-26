"""Subtitle lines, candidates.json, and the two curation callbacks prepare and commit pass.

The processor hands a curation callback the filtered words, each carrying its
``sentence_candidates`` (one leaf variant per line it can be mined from, the
current pick included). prepare's callback records them and returns ``[]``, so
the run stops where the Word Curator would open. commit's returns the named
words, each switched to the variant on its chosen line with its
``line_expansion`` set — exactly what the curator's get_selected_words returns.
Lines are ``parse_raw_entries`` indices: the same cleaned text and offset
clamp as the words, so ``cue_index`` finds a word's line exactly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from anki_miner.cli.api.files import WordPick
from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models.word import TokenizedWord
from anki_miner.services.cue_merge import auto_line_expansion, merge_budget_seconds
from anki_miner.services.word_filter import find_cue_index, merge_cue_window

Entries = Sequence[tuple[float, float, str]]


def cue_index(entries: Entries, word: TokenizedWord) -> int | None:
    """The line *word* sits on; None when no line carries its exact text (the materializer's guard)."""
    index = find_cue_index(entries, word.start_time, word.sentence, tolerance=1e-3)
    return index if index is not None and entries[index][2] == word.sentence else None


def line_merges(config: AnkiMinerConfig, entries: Entries) -> list[tuple[int, int]]:
    """The automatic merge each line would get; all (0, 0) with merge_incomplete_cues off."""
    if not config.merge_incomplete_cues:
        return [(0, 0)] * len(entries)
    rules = get_profile(config_language(config)).sentence_rules
    budget = merge_budget_seconds(config.audio_padding)
    return [auto_line_expansion(entries, i, rules, max_seconds=budget) for i in range(len(entries))]


def candidates_file(
    run_id: str, entries: Entries, merges: Sequence[tuple[int, int]], words: Sequence[TokenizedWord]
) -> dict[str, object]:
    """``candidates.json`` (proposal, "prepare"); ``dropped`` is not reported by this build."""
    return {
        "schema": 1,
        "run_id": run_id,
        "lines": [[i, start, end, text, list(merges[i])] for i, (start, end, text) in enumerate(entries)],
        "candidates": [_candidate(entries, word) for word in words],
        "dropped": None,
    }


def _candidate(entries: Entries, word: TokenizedWord) -> dict[str, object]:
    line = cue_index(entries, word)
    on = {line} if line is not None else set()
    on.update(i for variant in word.sentence_candidates if (i := cue_index(entries, variant)) is not None)
    return {
        "mined_form": word.mined_form,
        "lemma": word.lemma,
        "orth_base": word.orth_base,
        "surface": word.surface,
        "expression_reading": word.expression_reading,
        "line": line,
        "sentence_candidates": sorted(on),
    }


class CandidateCapture:
    """prepare: keep the words the curator would have opened on; card none."""

    #: The [] is a listing, not "no words selected" (see _run_curation).
    suppress_curation_messages = True

    def __init__(self) -> None:
        self.words: list[TokenizedWord] = []

    def __call__(self, words: list[TokenizedWord]) -> list[TokenizedWord]:
        self.words = list(words)
        return []


def validate_picks(picks: Sequence[WordPick], candidates: Sequence[Mapping[str, object]]) -> str | None:
    """Why the picks refuse the whole run (BAD_LINE), or None. Checked before any media is cut."""
    lines_of: dict[str, object] = {str(c.get("mined_form")): c.get("sentence_candidates") or [] for c in candidates}
    for pick in picks:
        if pick.line_expansion is not None and min(pick.line_expansion) < 0:
            return f"{pick.mined_form}: line_expansion cannot be negative."
        allowed = lines_of.get(pick.mined_form)
        if pick.line is not None and isinstance(allowed, list) and pick.line not in allowed:
            return f"{pick.mined_form}: line {pick.line} is not one of its sentence_candidates."
    return None


@dataclass(frozen=True)
class _Chosen:
    line: int | None
    expansion: tuple[int, int]
    word: TokenizedWord


class PickSelection:
    """commit: the named words only, each on its chosen line with its merge."""

    def __init__(self, picks: Sequence[WordPick], entries: Entries, merges: Sequence[tuple[int, int]]) -> None:
        self._picks = list(picks)
        self._by_form = {pick.mined_form: pick for pick in picks}
        self._entries = entries
        self._merges = merges
        self._chosen: dict[str, _Chosen] = {}

    def __call__(self, words: list[TokenizedWord]) -> list[TokenizedWord]:
        selected: list[TokenizedWord] = []
        for word in words:
            pick = self._by_form.get(word.mined_form)
            if pick is None:
                continue
            variant, line = self._variant(word, pick.line)
            if variant is None:
                continue
            if pick.line_expansion is not None:
                expansion = pick.line_expansion
            elif line is not None:
                expansion = self._merges[line]  # left out: the chosen line's automatic merge
            else:
                expansion = variant.line_expansion
            chosen = replace(variant, line_expansion=expansion, clip_override=None, screenshot_override=None)
            self._chosen[word.mined_form] = _Chosen(line, expansion, chosen)
            selected.append(chosen)
        return selected

    def _variant(self, word: TokenizedWord, line: int | None) -> tuple[TokenizedWord | None, int | None]:
        if line is None:
            return word, cue_index(self._entries, word)
        for candidate in (word, *word.sentence_candidates):
            if cue_index(self._entries, candidate) == line:
                return candidate, line
        return None, None

    def report(self, created: Mapping[str, int], media_missing: Mapping[str, list[str]]) -> list[dict[str, object]]:
        """``words`` for result-<n>.json: created (with note_id), not_created, or not_found.

        ``media_missing`` is the processor's per-word record of cuts that
        produced no file (EpisodeProcessor.last_media_missing); [] when none did.
        """
        rows: list[dict[str, object]] = []
        for pick in self._picks:
            chosen = self._chosen.get(pick.mined_form)
            if chosen is None:
                rows.append(
                    {
                        "mined_form": pick.mined_form,
                        "status": "not_found",
                        "note_id": None,
                        "media_missing": [],
                        "line_range": None,
                        "sentence": None,
                        "start": None,
                        "end": None,
                    }
                )
                continue
            note_id = created.get(pick.mined_form)
            rows.append(
                {
                    "mined_form": pick.mined_form,
                    "status": "created" if note_id is not None else "not_created",
                    "note_id": note_id,
                    "media_missing": list(media_missing.get(pick.mined_form, [])),
                    **self._span(chosen),
                }
            )
        return rows

    def _span(self, chosen: _Chosen) -> dict[str, object]:
        if chosen.line is None:
            word = chosen.word
            return {"line_range": None, "sentence": word.sentence, "start": word.start_time, "end": word.end_time}
        before, after = chosen.expansion
        window = merge_cue_window(self._entries, chosen.line, before, after)
        first = max(0, chosen.line - before)
        last = min(len(self._entries) - 1, chosen.line + after)
        return {"line_range": [first, last], "sentence": window.text, "start": window.start, "end": window.end}
