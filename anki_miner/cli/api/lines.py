"""Subtitle lines and the curation callback a mine run passes (API.md, "A word").

The processor hands a curation callback the filtered words, each carrying its
``sentence_candidates`` (one leaf variant per line it can be mined from, its
own line included; empty when it has one line). ``WordSelection`` returns the
named words, each switched to the variant on its chosen line with its
``line_expansion`` set: what the Word Curator's get_selected_words returns.
Lines are ``parse_raw_entries`` indices: the same cleaned text and offset
clamp as the words, so ``cue_index`` finds a word's line exactly. The run also
parses the file at offset 0 (``raw``: the same lines, index for index) for the
times the caller wrote.

A word can also be made from its named line (Z-2): ``LineWords`` holds the
run's seams for that, bound by ``runs`` to its processor.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from anki_miner.cli.api.files import WordRequest
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


def find_folded(text: str, needle: str) -> tuple[int, int] | None:
    """The first ``[start, end)`` of *text* that reads *needle* under ``_fold``, in *text*'s own offsets."""
    target = _fold(needle)
    if not target:
        return None
    chars: list[str] = []
    origin: list[int] = []  # origin[k]: the index in *text* of folded character k
    for i, char in enumerate(text):
        for folded in _fold(char):
            chars.append(folded)
            origin.append(i)
    found = "".join(chars).find(target)
    if found < 0:
        return None
    return origin[found], origin[found + len(target) - 1] + 1


def nearest_line(raw: Entries, start: float) -> int:
    """The line starting nearest *start* in the file's own times; a tie goes to the earlier line."""
    return min(range(len(raw)), key=lambda i: abs(raw[i][0] - start))  # min keeps the first of equals


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
    #: the phase-2 duplicate-expression losers and the front each merged into (EpisodeProcessor.last_collapsed)
    collapsed: Sequence[tuple[TokenizedWord, str]] = ()
    #: mined_form -> the status a run ended a word on other than created: "ready", "duplicate", "no_definition" (dry run)
    made: Mapping[str, str] = field(default_factory=dict)
    #: True when the run was cancelled or failed; a finished run that never reached
    #: curation (nothing parsed, everything filtered) found nothing rather than stopping
    stopped: bool = False


@dataclass(frozen=True)
class LineWords:
    """What making a word from its named line needs from the run, as plain callables.

    ``runs`` binds them to the run's processor; tests pass fakes.
    """

    #: EpisodeProcessor.parse_sentence_fn: one text through the run's own parser
    parse_line: Callable[[str], list[TokenizedWord]]
    #: EpisodeProcessor.word_on_line
    word_on_line: Callable[..., TokenizedWord]
    #: WordFilterService.with_reading: the entry's reading on a word the parse produced
    with_reading: Callable[[TokenizedWord, str], TokenizedWord]
    #: DefinitionService.offline_term_readings
    readings: Callable[[list[str]], Mapping[str, list[str]]]
    #: Whether the dictionary check or the duplicate-expression merge removed the word a name names
    removed: Callable[[str], bool]

    def make(
        self,
        template: TokenizedWord | None,
        request: WordRequest,
        line: tuple[float, float, str],
        span: tuple[int, int],
        *,
        front: str,
    ) -> TokenizedWord:
        """The word *request* names, made on *line* at *span* with *front* as its card front.

        The other fields come from *template* (the word the episode produced on
        other lines), else from the one token the run's parser makes of the
        matched text, else from the entry alone. The reading is the entry's, else
        the template's, else the dictionary's when it attests exactly one.
        """
        reading = request.reading
        if template is None:
            surface = line[2][span[0] : span[1]]
            template = next(
                (t for t in self.parse_line(surface) if (t.surface_start, t.surface_end) == (0, len(surface))),
                None,
            )
            if template is None:
                template = TokenizedWord(
                    surface=surface,
                    lemma=front,
                    reading="",
                    sentence=surface,
                    start_time=0.0,
                    end_time=0.0,
                    duration=0.0,
                    orth_base=front,
                )
                if not reading:
                    attested = self.readings([front]).get(front) or []
                    reading = attested[0] if len(attested) == 1 else None
        return self.word_on_line(replace(template, mined_form_override=front), line, span, reading=reading)


@dataclass(frozen=True)
class _Placed:
    word: TokenizedWord  # the word as the processor listed it
    variant: TokenizedWord  # the word itself, or its variant on the chosen line
    line: int | None
    from_line: bool = False  # made from its named line (LineWords.make)


@dataclass(frozen=True)
class _Picked:
    word: TokenizedWord  # the variant handed on, its line_expansion set
    line: int | None
    expansion: tuple[int, int]
    from_line: bool = False


class WordSelection:
    """A mine run's curation callback: the named words only, each on its line with its merge (API.md, "A word")."""

    def __init__(
        self,
        requests: Sequence[WordRequest],
        entries: Entries,
        raw: Entries,
        merges: Sequence[tuple[int, int]],
        budget: float,
        *,
        clean: Callable[[str], str] | None = None,
        fold: Callable[[str], str] | None = None,
        allow_duplicates: bool = False,
        line_words: LineWords | None = None,
        dry_run: bool = False,
    ) -> None:
        self._requests = list(requests)
        self._entries = entries
        self._raw = raw
        self._folded = [_fold(text) for _, _, text in entries]
        self._merges = merges
        self._budget = budget
        #: The cleaner the lines went through (markup, speaker tags, furigana, the
        #: user's filter), so a ``line_text`` copied from the file matches its line.
        self._clean = clean or (lambda text: text)
        #: The language's comparison fold (``LanguageProfile.dedup_fold``): the last
        #: way a name matches, and how fronts compare for repeats while the merge is on.
        self._fold = fold
        #: ``allow_duplicate_cards``: repeats then compare exact fronts (NFC), as
        #: phase 2 keeps fold-equal words apart.
        self._allow_duplicates = allow_duplicates
        #: The run's seams for making a named word from its named line; None: never made.
        self._line_words = line_words
        #: The picks are recorded but none is handed on (``[]``: no media, definitions or cards).
        self._dry_run = dry_run
        self._chosen: dict[int, _Picked] = {}  # by request index
        self._repeats: dict[int, str] = {}  # request index -> the mined_form an earlier request took
        #: False until the processor reached the curation step.
        self.ran = False

    def _key(self, front: str) -> str:
        """NFC, then the language's fold (phase 2's collapse key)."""
        front = _nfc(front)
        return front if self._fold is None else self._fold(front)

    def _repeat_key(self, front: str) -> str:
        """How fronts compare for repeats: the collapse key while the merge is on, else the exact front."""
        return _nfc(front) if self._allow_duplicates else self._key(front)

    def __call__(self, words: list[TokenizedWord]) -> list[TokenizedWord]:
        self.ran = True
        by_form: dict[str, TokenizedWord] = {}
        by_lemma: dict[str, list[TokenizedWord]] = {}
        by_fold: dict[str, list[TokenizedWord]] = {}
        for word in words:
            by_form.setdefault(_nfc(word.mined_form), word)
            by_lemma.setdefault(_nfc(word.lemma), []).append(word)
            if self._fold is not None:
                by_fold.setdefault(self._key(word.mined_form), []).append(word)
        taken: set[str] = set()
        selected: list[TokenizedWord] = []
        for i, request in enumerate(self._requests):
            key = _nfc(request.word)
            pools = ([by_form[key]] if key in by_form else [], by_lemma.get(key, []), by_fold.get(self._key(key), []))
            placed = self._place(pools, request)
            if placed is None:
                continue
            front = placed.variant.mined_form  # a made word's front is the one _place chose
            if self._repeat_key(front) in taken:
                self._repeats[i] = front
                continue
            taken.add(self._repeat_key(front))
            variant = placed.variant
            if request.reading and not placed.from_line and self._line_words is not None:
                variant = self._line_words.with_reading(variant, request.reading)  # a made word has it already
            expansion = self._expansion(request, placed)
            chosen = replace(variant, line_expansion=expansion, clip_override=None, screenshot_override=None)
            self._chosen[i] = _Picked(chosen, placed.line, expansion, placed.from_line)
            selected.append(chosen)
        return [] if self._dry_run else selected

    def picked(self) -> list[tuple[TokenizedWord, bool]]:
        """Each word handed on (or, in a dry run, that would have been), with whether it was made from its line."""
        return [(p.word, p.from_line) for _, p in sorted(self._chosen.items())]

    def _lines_of(self, word: TokenizedWord) -> list[_Placed]:
        """Each located line *word* can be mined from; on its own line, the word itself."""
        own = cue_index(self._entries, word)
        found = [_Placed(word, word, own)] if own is not None else []
        for variant in word.sentence_candidates:
            line = cue_index(self._entries, variant)
            if line is not None and line != own:
                found.append(_Placed(word, variant, line))
        return found

    def _named_line(self, request: WordRequest) -> int | None:
        """The line the entry names: nearest ``line_start``, else the first holding ``line_text``."""
        if not self._entries:
            return None
        if request.line_start is not None:
            return nearest_line(self._raw, request.line_start)
        if request.line_text is not None:
            # A needle the cleaner empties would match every line: keep it as written.
            needle = _fold(self._clean(request.line_text)) or _fold(request.line_text)
            return next((i for i, text in enumerate(self._folded) if needle in text), None)
        return None

    def _place(self, pools: Sequence[list[TokenizedWord]], request: WordRequest) -> _Placed | None:
        """Where *request*'s word is mined: on its named line (found there, or made from it), else its own line.

        *pools* are the words the name matches by card front, by dictionary form
        and through the fold, in that order.
        """
        which, pool = next(((i, p) for i, p in enumerate(pools) if p), (None, []))
        named = self._named_line(request)
        if named is None:
            if not pool:
                return None
            first = pool[0]
            return _Placed(first, first, cue_index(self._entries, first))
        for candidates in pools:
            on_line = [p for word in candidates for p in self._lines_of(word) if p.line == named]
            if on_line:
                return on_line[0]
        # R2: a name the dictionary check or the merge removed is never made from a line.
        # A survivor whose card front is the name (which == 0) makes that word moved;
        # one matched by dictionary form or fold may sit beside a removed word the name names.
        if self._line_words is None or (which != 0 and self._line_words.removed(request.word)):
            return None
        template = pool[0] if pool else None
        # (spelling, whether the text it matches may be a card front): a surface, the
        # caller's or the episode's, can be inflected (Häuser) and never is one.
        tries = [(request.surface, False)] if request.surface else [(request.word, True)]
        if template is not None and not request.surface:
            tries += [(template.mined_form, True), (template.surface, False)]  # how the episode itself writes it
        text = self._entries[named][2]
        hit = next(
            ((span, keep) for spelling, keep in tries if (span := find_folded(text, spelling)) is not None), None
        )
        if hit is None:
            return None
        span, keep_text = hit
        if which != 2 or template is None:
            front = _nfc(request.word)
        elif keep_text:
            front = _nfc(text[span[0] : span[1]])  # through the fold (Z-12) the card keeps the subtitle's spelling
        else:
            front = template.mined_form
        made = self._line_words.make(template, request, self._entries[named], span, front=front)
        return _Placed(made, made, named, from_line=True)

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
            "from_line": False,  # made from its named line (API.md, "Choosing the line")
            "media_missing": [],
            "line_start": None,
            "sentence": None,
            "start": None,
            "end": None,
            "filter": None,  # the step that removed a named word (API.md): "duplicate-expression" or null
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
                from_line=chosen.from_line,
                media_missing=[] if status == "not_attempted" else list(fates.media_missing.get(form, [])),
            )
        elif i in self._repeats:
            row.update(mined_form=self._repeats[i], status="duplicate")
        elif (rejected := named_word(request.word, fates.rejected, self._fold)) is not None:
            row.update(mined_form=rejected.mined_form, status="no_definition")
        elif (
            merged := next(
                (winner for loser, winner in fates.collapsed if named_word(request.word, [loser], self._fold)), None
            )
        ) is not None:
            row.update(mined_form=merged, filter="duplicate-expression")
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
    return fates.made.get(form) or fates.not_created.get(form) or fates.dropped.get(form) or "not_attempted"


def named_word(
    name: str, words: Sequence[TokenizedWord], fold: Callable[[str], str] | None = None
) -> TokenizedWord | None:
    """The word *name* names by card front, else by dictionary form (both NFC), else by
    card front under the language's *fold*."""
    key = _nfc(name)
    found = next((w for w in words if _nfc(w.mined_form) == key), None)
    found = found or next((w for w in words if _nfc(w.lemma) == key), None)
    if found is None and fold is not None:
        found = next((w for w in words if fold(_nfc(w.mined_form)) == fold(key)), None)
    return found
