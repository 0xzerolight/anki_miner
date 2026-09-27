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

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from anki_miner.cli.api.files import WordPick, WordRequest
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


def fit_expansion(entries: Entries, line: int, wanted: tuple[int, int], budget: float) -> tuple[int, int]:
    """*wanted* ``[before, after]`` cut to the file's ends and to *budget* seconds.

    Lines are added one at a time, those after *line* first (as the automatic
    merge does), while the merged window stays within *budget*: the limit the
    Word Curator's ± buttons keep.
    """
    most_before = min(wanted[0], line)
    most_after = min(wanted[1], len(entries) - 1 - line)

    def fits(before: int, after: int) -> bool:
        window = merge_cue_window(entries, line, before, after)
        return window.end - window.start <= budget

    before = after = 0
    while after < most_after and fits(before, after + 1):
        after += 1
    while before < most_before and fits(before + 1, after):
        before += 1
    return before, after


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _fold(text: str) -> str:
    """NFKC with every whitespace run removed, for ``line_text``."""
    return "".join(unicodedata.normalize("NFKC", text).split())


@dataclass(frozen=True)
class Fates:
    """What a run did with its words, read off the processor and its AnkiService afterwards."""

    #: mined_form -> note id of each card created
    created: Mapping[str, int] = field(default_factory=dict)
    #: mined_form -> "duplicate" | "refused" | "uncertain" (AnkiService.last_not_created)
    not_created: Mapping[str, str] = field(default_factory=dict)
    #: mined_form -> "media_failed" | "no_definition" (EpisodeProcessor.last_word_drops)
    dropped: Mapping[str, str] = field(default_factory=dict)
    #: the words the phase-2 offline-definition probe removed (EpisodeProcessor.last_definition_rejects)
    rejected: Sequence[TokenizedWord] = ()
    #: mined_form -> the cuts that produced no file (EpisodeProcessor.last_media_missing)
    media_missing: Mapping[str, list[str]] = field(default_factory=dict)
    #: True when the run was cancelled or failed; a finished run that never reached
    #: curation (nothing parsed, everything filtered) found nothing rather than stopping
    stopped: bool = False


@dataclass(frozen=True)
class _Placed:
    word: TokenizedWord  # the word as the processor listed it
    variant: TokenizedWord  # the word itself, or its variant on the chosen line
    line: int | None


@dataclass(frozen=True)
class _Picked:
    word: TokenizedWord  # the variant handed on, its line_expansion set
    line: int | None
    expansion: tuple[int, int]


class WordSelection:
    """A mine run's curation callback: the named words only, each on its line with its merge (API.md, "A word")."""

    def __init__(
        self,
        requests: Sequence[WordRequest],
        entries: Entries,
        raw: Entries,
        merges: Sequence[tuple[int, int]],
        budget: float,
    ) -> None:
        self._requests = list(requests)
        self._entries = entries
        self._raw = raw
        self._merges = merges
        self._budget = budget
        self._chosen: dict[int, _Picked] = {}  # by request index
        self._repeats: dict[int, str] = {}  # request index -> the mined_form an earlier request took
        #: False until the processor reached the curation step.
        self.ran = False

    def __call__(self, words: list[TokenizedWord]) -> list[TokenizedWord]:
        self.ran = True
        by_form: dict[str, TokenizedWord] = {}
        by_lemma: dict[str, list[TokenizedWord]] = {}
        for word in words:
            by_form.setdefault(_nfc(word.mined_form), word)
            by_lemma.setdefault(_nfc(word.lemma), []).append(word)
        taken: set[str] = set()
        selected: list[TokenizedWord] = []
        for i, request in enumerate(self._requests):
            key = _nfc(request.word)
            pool = [by_form[key]] if key in by_form else by_lemma.get(key, [])
            if not pool:
                continue
            placed = self._place(pool, request)
            if placed.word.mined_form in taken:
                self._repeats[i] = placed.word.mined_form
                continue
            taken.add(placed.word.mined_form)
            expansion = self._expansion(request, placed)
            chosen = replace(placed.variant, line_expansion=expansion, clip_override=None, screenshot_override=None)
            self._chosen[i] = _Picked(chosen, placed.line, expansion)
            selected.append(chosen)
        return selected

    def _lines_of(self, word: TokenizedWord) -> list[_Placed]:
        """Each located line *word* can be mined from; on its own line, the word itself."""
        own = cue_index(self._entries, word)
        found = [_Placed(word, word, own)] if own is not None else []
        for variant in word.sentence_candidates:
            line = cue_index(self._entries, variant)
            if line is not None and line != own:
                found.append(_Placed(word, variant, line))
        return found

    def _place(self, pool: list[TokenizedWord], request: WordRequest) -> _Placed:
        """The word and line *request* names: nearest ``line_start``, else first line containing ``line_text``,
        else the first word on its own line."""
        placed = sorted((p for word in pool for p in self._lines_of(word)), key=lambda p: p.line or 0)
        if request.line_start is not None and placed:
            start = request.line_start
            # min keeps the first of equals: a tie goes to the earlier line
            return min(placed, key=lambda p: abs(self._raw[p.line or 0][0] - start))
        if request.line_text is not None:
            needle = _fold(request.line_text)
            for p in placed:
                if needle in _fold(self._entries[p.line or 0][2]):
                    return p
        first = pool[0]
        return _Placed(first, first, cue_index(self._entries, first))

    def _expansion(self, request: WordRequest, placed: _Placed) -> tuple[int, int]:
        if placed.line is None:
            return placed.variant.line_expansion  # no line to merge around
        if request.line_expansion is not None:
            return fit_expansion(self._entries, placed.line, request.line_expansion, self._budget)
        return self._merges[placed.line]  # left out: the chosen line's automatic merge

    def report(self, fates: Fates) -> list[dict[str, object]]:
        """``words`` for result-<n>.json: one row per request, in the run file's order, every key present."""
        return [self._row(i, request, fates) for i, request in enumerate(self._requests)]

    def _row(self, i: int, request: WordRequest, fates: Fates) -> dict[str, object]:
        row: dict[str, object] = {
            "word": request.word,
            "mined_form": None,
            "status": "not_found",
            "note_id": None,
            "media_missing": [],
            "line_start": None,
            "sentence": None,
            "start": None,
            "end": None,
            "filter": None,  # this build does not name the step that removed a word
        }
        chosen = self._chosen.get(i)
        if chosen is not None:
            form = chosen.word.mined_form
            status = _status(form, fates)
            row.update(self._span(chosen))
            row.update(
                mined_form=form,
                status=status,
                note_id=fates.created.get(form),
                media_missing=[] if status == "not_attempted" else list(fates.media_missing.get(form, [])),
            )
        elif i in self._repeats:
            row.update(mined_form=self._repeats[i], status="duplicate")
        elif (rejected := _match(request.word, fates.rejected)) is not None:
            row.update(mined_form=rejected.mined_form, status="no_definition")
        elif not self.ran and fates.stopped:
            row["status"] = "not_attempted"
        return row

    def _span(self, chosen: _Picked) -> dict[str, object]:
        if chosen.line is None:
            word = chosen.word
            return {"line_start": None, "sentence": word.sentence, "start": word.start_time, "end": word.end_time}
        before, after = chosen.expansion
        window = merge_cue_window(self._entries, chosen.line, before, after)
        return {
            "line_start": self._raw[chosen.line][0],
            "sentence": window.text,
            "start": window.start,
            "end": window.end,
        }


def _status(form: str, fates: Fates) -> str:
    if form in fates.created:
        return "created"
    return fates.not_created.get(form) or fates.dropped.get(form) or "not_attempted"


def _match(name: str, words: Sequence[TokenizedWord]) -> TokenizedWord | None:
    """The word *name* names by card front, else by dictionary form (both NFC)."""
    key = _nfc(name)
    by_form = next((w for w in words if _nfc(w.mined_form) == key), None)
    return by_form or next((w for w in words if _nfc(w.lemma) == key), None)
