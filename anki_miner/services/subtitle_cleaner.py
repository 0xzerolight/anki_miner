"""Dialogue-only subtitle cleaning for alignment, and mapping results back.

Aligners (alass, ffsubsync) match cue timings against a reference signal.
Anything in a subtitle file that is not dialogue — signs, karaoke, OP/ED
lyrics, ♪ markers, hearing-impaired sound annotations — adds spans that have
nothing to do with speech and can drag the alignment onto a wrong optimum.
The community consensus for anime is unambiguous: strip non-dialogue from
BOTH sides before aligning.

Two sides, two entry points:

* :func:`clean_reference` — the extracted embedded track we align *against*.
  Produces a dialogue-only UTF-8 SRT (text is irrelevant to the aligner, so
  the format is normalized). Moved here from ``retime_reference``.
* :func:`clean_for_alignment` — the user's subtitle being retimed. Produces a
  same-format copy with non-dialogue cues *dropped* (never rewritten) and
  records which original cues survived, so the computed timings can be mapped
  back onto the untouched original with :func:`map_deltas_back` — every line
  and all ASS styling survive the round trip.

Nothing here raises for content reasons: an unparsable file returns None /
False and the caller falls back to aligning the original directly.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pysubs2

from anki_miner.utils.audio_track_detector import Chapter, list_chapters
from anki_miner.utils.subtitle_encoding import load_with_fallback_encoding

logger = logging.getLogger(__name__)

__all__ = [
    "OP_ED_CHAPTER_RE",
    "CleanedForAlignment",
    "SkipRanges",
    "clean_for_alignment",
    "clean_reference",
    "collapse_frame_bursts",
    "drop_non_dialogue_events",
    "map_deltas_back",
    "midpoint_in_ranges",
    "mining_events",
    "op_ed_ranges",
    "op_ed_skip_ranges",
    "transcode_for_alignment",
]

#: ASS style names that mark an event as something other than dialogue. The
#: alternation is bounded by non-word characters on both sides so a style named
#: ``Subtitle`` does not match ``title`` and ``Fixed`` does not match ``ed``.
NON_DIALOGUE_STYLE_RE = re.compile(
    r"(?:^|[\W_])"
    r"(?:signs?|songs?|karaoke|kara|op|ed|opening|ending|credits?|titles?|captions?|notes?|typeset\w*)"
    r"(?:$|[\W_])",
    re.IGNORECASE,
)

#: ASS drawing-mode override. An event carrying one paints a vector shape, not
#: text, and its timing has nothing to do with speech.
ASS_DRAWING_RE = re.compile(r"\\p[1-9]")

#: One parenthesized group, JP or ASCII parens. Japanese HoH subs annotate
#: sounds as ``（雷鳴）`` and furigana as ``漢字(かんじ)``; English HoH subs
#: use ``(sighs)``. Removing the groups and checking what remains classifies
#: annotation-only cues without touching mixed speech lines.
_PAREN_GROUP_RE = re.compile(r"[（(][^（）()]*[）)]")

#: What may remain in a cue that carries no speech: whitespace, music glyphs,
#: prolonged-sound/dash marks, ellipses and bare punctuation -- Latin, CJK,
#: Arabic (question mark, comma, semicolon), Hebrew (geresh, gershayim, maqaf)
#: and Thai (paiyannoi, maiyamok). The right-to-left and Thai characters are
#: named escapes, which ``re`` expands.
_NON_SPEECH_RESIDUE_RE = re.compile(
    r"^[\s♪♫♬♩〜～ー―—\-…・.。、,!?！？~"
    r"\N{ARABIC QUESTION MARK}\N{ARABIC COMMA}\N{ARABIC SEMICOLON}"
    r"\N{HEBREW PUNCTUATION GERESH}\N{HEBREW PUNCTUATION GERSHAYIM}\N{HEBREW PUNCTUATION MAQAF}"
    r"\N{THAI CHARACTER PAIYANNOI}\N{THAI CHARACTER MAIYAMOK}]*$"
)

#: Minimum surviving cues for a cleaned copy to be worth aligning. Below this
#: the drop list itself is suspect (wrong style names, annotation-heavy file)
#: and aligning the original unchanged is safer.
_MIN_ALIGNMENT_CUES = 10


def _is_non_speech_text(plaintext: str) -> bool:
    """True when a cue's visible text carries no speech to align against."""
    return bool(_NON_SPEECH_RESIDUE_RE.match(_PAREN_GROUP_RE.sub("", plaintext)))


def _is_non_dialogue_event(event: pysubs2.SSAEvent) -> bool:
    """True for cues an aligner should not see: comments, drawings,
    non-dialogue styles, zero/negative spans, and annotation/music-only text."""
    if getattr(event, "is_comment", None) is True:
        return True
    if event.end <= event.start:
        return True
    if NON_DIALOGUE_STYLE_RE.search(event.style or ""):
        return True
    if ASS_DRAWING_RE.search(event.text or ""):
        return True
    return _is_non_speech_text(event.plaintext.strip())


def _load(
    path: Path,
    *,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> pysubs2.SSAFile:
    """Load *path*, falling back to the caller's ladder when UTF-8 fails.

    *encodings* is the mining language's ``get_profile(...).import_encodings``,
    threaded down from the retime orchestrator. ``None`` (never ``()``) is the
    built-in Japanese ladder — the right answer for ``clean_reference``, whose
    input is an ffmpeg-extracted UTF-8 temp, and for a caller reading cue
    timings only. It is the wrong one for the user's own subtitle: GB18030 and
    Big5 bytes decode as EUC-JP into plausible-looking kanji without raising,
    and ``map_deltas_back`` saves what it read.
    """
    try:
        return pysubs2.load(str(path))
    except UnicodeDecodeError as exc:
        return load_with_fallback_encoding(path, exc, encodings=encodings, script_check=script_check)


@dataclass(frozen=True)
class ReferenceCleanResult:
    """What survived cleaning a reference track: cue count and covered span."""

    cues: int
    span_ms: int


def clean_reference(src: Path, dest: Path) -> ReferenceCleanResult:
    """Write the dialogue-only cues of *src* to *dest* as UTF-8 SRT.

    Returns the surviving cue count and their first-start-to-last-end span —
    the two signals reference selection uses to reject a track that is not
    real full-episode dialogue. Drops everything
    :func:`_is_non_dialogue_event` flags, plus duplicate spans. Only cue
    *timings* matter to the aligner, but the text is carried through so the
    temp file stays readable when a run needs diagnosing.
    """
    subs = _load(src)

    kept: list[pysubs2.SSAEvent] = []
    seen: set[tuple[int, int]] = set()
    for event in subs.events:
        if _is_non_dialogue_event(event):
            continue
        span = (event.start, event.end)
        if span in seen:
            continue
        seen.add(span)
        # SRT has no concept of an empty cue; substitute a marker so the writer
        # cannot merge or drop a span whose text was pure override tags.
        text = event.plaintext.strip() or "-"
        kept.append(pysubs2.SSAEvent(start=event.start, end=event.end, text=text))

    out = pysubs2.SSAFile()
    out.events = kept
    out.save(str(dest), encoding="utf-8", format_="srt")
    span_ms = max(e.end for e in kept) - min(e.start for e in kept) if kept else 0
    return ReferenceCleanResult(cues=len(kept), span_ms=span_ms)


@dataclass(frozen=True)
class CleanedForAlignment:
    """A dialogue-only copy of the subtitle being retimed.

    ``kept_indices[i]`` is the index into the original event list of the i-th
    cue in *path* — the contract :func:`map_deltas_back` uses to attach the
    aligner's output timings back onto the original file.
    """

    path: Path
    kept_indices: list[int]
    total_events: int

    @property
    def dropped(self) -> int:
        return self.total_events - len(self.kept_indices)


def clean_for_alignment(
    src: Path,
    dest: Path,
    *,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> CleanedForAlignment | None:
    """Write a same-format copy of *src* to *dest* with non-dialogue cues dropped.

    Cues are only ever dropped, never rewritten, so the copy stays a strict
    subset the aligner sees in original order. Returns None when the file
    cannot be parsed or too few cues survive — the caller then aligns the
    original file directly, which is exactly the pre-cleaning behaviour.

    *encodings* and *script_check* are the mining language's decode ladder; see
    :func:`_load`.
    """
    try:
        subs = _load(src, encodings=encodings, script_check=script_check)
    except Exception:  # noqa: BLE001 — an unparsable input falls back to as-is alignment
        logger.warning("subtitle cleaner: could not parse %s", src, exc_info=True)
        return None

    total = len(subs.events)
    kept_indices = [i for i, event in enumerate(subs.events) if not _is_non_dialogue_event(event)]
    if len(kept_indices) < _MIN_ALIGNMENT_CUES:
        logger.info(
            "subtitle cleaner: only %d of %d cues look like dialogue in %s; aligning uncleaned",
            len(kept_indices),
            total,
            src.name,
        )
        return None

    kept_set = set(kept_indices)
    cleaned = pysubs2.SSAFile()
    cleaned.styles = subs.styles.copy()
    cleaned.info = subs.info.copy()
    cleaned.events = [event.copy() for i, event in enumerate(subs.events) if i in kept_set]
    try:
        cleaned.save(str(dest), encoding="utf-8")
    except Exception:  # noqa: BLE001 — an unwritable copy falls back to as-is alignment
        logger.warning("subtitle cleaner: could not write %s", dest, exc_info=True)
        return None
    return CleanedForAlignment(path=dest, kept_indices=kept_indices, total_events=total)


def transcode_for_alignment(
    src: Path,
    dest: Path,
    *,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> CleanedForAlignment | None:
    """Write *src* to *dest* in *dest*'s format, dropping only what cannot survive it.

    The escape hatch for a format the aligners cannot read at all: alass v2
    accepts SubRip/SubStationAlpha/VobSub and errors out on WebVTT, so a .vtt
    retime would otherwise lose two of its four engines. Reached only when
    :func:`clean_for_alignment` declines (too few dialogue cues, or an
    unparsable file) — when it succeeds it has already transcoded, because
    ``pysubs2.save`` takes its format from the destination extension.

    Unlike :func:`clean_for_alignment` this drops nothing for being
    non-dialogue: with the cue floor already unmet, dropping more would leave
    less to align against. It drops only what the target writer would drop
    anyway — comments (SRT and WebVTT have no such concept) and non-positive
    spans — because ``kept_indices`` must match the cue count the aligner sees
    or :func:`map_deltas_back` discards every candidate.

    Returns None when *src* cannot be parsed or *dest* cannot be written; the
    caller then aligns the original directly, which is the pre-transcode
    behaviour.

    *encodings* and *script_check* are the mining language's decode ladder; see
    :func:`_load`.
    """
    try:
        subs = _load(src, encodings=encodings, script_check=script_check)
    except Exception:  # noqa: BLE001 — an unparsable input falls back to as-is alignment
        logger.warning("subtitle cleaner: could not parse %s for transcode", src, exc_info=True)
        return None

    total = len(subs.events)
    kept_indices = [
        i
        for i, event in enumerate(subs.events)
        if getattr(event, "is_comment", None) is not True and event.end > event.start
    ]
    kept_set = set(kept_indices)

    transcoded = pysubs2.SSAFile()
    transcoded.styles = subs.styles.copy()
    transcoded.info = subs.info.copy()
    transcoded.events = [event.copy() for i, event in enumerate(subs.events) if i in kept_set]
    try:
        transcoded.save(str(dest), encoding="utf-8")
    except Exception:  # noqa: BLE001 — an unwritable copy falls back to as-is alignment
        logger.warning("subtitle cleaner: could not write transcode %s", dest, exc_info=True)
        return None
    return CleanedForAlignment(path=dest, kept_indices=kept_indices, total_events=total)


def map_deltas_back(
    original: Path,
    synced_clean: Path,
    kept_indices: list[int],
    out: Path,
    *,
    encodings: tuple[str, ...] | None = None,
    script_check: Callable[[str], bool] | None = None,
) -> bool:
    """Apply the aligner's timing changes to the untouched *original* file.

    *synced_clean* is the aligner's output for the cleaned copy; its i-th cue
    corresponds to original event ``kept_indices[i]``. Kept cues take their
    exact new timings. Dropped cues take the start/end delta of the nearest
    kept cue by original start time — aligners shift in per-block constants,
    so the nearest anchor's shift is the block-correct one, where linear
    interpolation across a block boundary would land between blocks.

    Writes the result to *out* in the original's format (styles and all lines
    preserved). Returns False when the cue counts do not match — the aligner
    dropped or merged cues and the mapping would be wrong.

    *encodings* and *script_check* are the mining language's decode ladder; see
    :func:`_load`. This is the load whose text reaches disk, so a ladder that
    does not match the user's subtitle writes mojibake into *out*.
    """
    try:
        subs = _load(original, encodings=encodings, script_check=script_check)
        synced = _load(synced_clean, encodings=encodings, script_check=script_check)
    except Exception:  # noqa: BLE001 — parse failure means the candidate is unusable
        logger.warning("subtitle cleaner: map-back parse failed", exc_info=True)
        return False

    if len(synced.events) != len(kept_indices):
        logger.warning(
            "subtitle cleaner: aligner changed cue count (%d cleaned, %d synced); discarding",
            len(kept_indices),
            len(synced.events),
        )
        return False
    if any(i >= len(subs.events) for i in kept_indices):
        logger.warning("subtitle cleaner: kept indices no longer fit the original; discarding")
        return False

    # Anchors sorted by original start time for nearest-neighbour lookup.
    anchors = sorted(
        (
            subs.events[orig_i].start,
            synced_event.start - subs.events[orig_i].start,
            synced_event.end - subs.events[orig_i].end,
        )
        for orig_i, synced_event in zip(kept_indices, synced.events, strict=True)
    )
    starts = [a[0] for a in anchors]

    kept_set = set(kept_indices)
    by_original_index = dict(zip(kept_indices, synced.events, strict=True))
    for i, event in enumerate(subs.events):
        if i in kept_set:
            event.start = by_original_index[i].start
            event.end = by_original_index[i].end
            continue
        pos = bisect_left(starts, event.start)
        if pos <= 0:
            _, delta_start, delta_end = anchors[0]
        elif pos >= len(anchors):
            _, delta_start, delta_end = anchors[-1]
        else:
            before, after = anchors[pos - 1], anchors[pos]
            nearest = before if event.start - before[0] <= after[0] - event.start else after
            _, delta_start, delta_end = nearest
        event.start = max(0, event.start + delta_start)
        event.end = max(0, event.end + delta_end)

    try:
        subs.save(str(out), encoding="utf-8")
    except Exception:  # noqa: BLE001 — write failure means the candidate is unusable
        logger.warning("subtitle cleaner: could not write %s", out, exc_info=True)
        return False
    return True


# ---------------------------------------------------------------------------
# The cues mining, Condense and Readability read (MS-12, F-08, SK-19)
# ---------------------------------------------------------------------------

#: A cue showing the same text as an open run joins it when it starts no
#: earlier than the run's first cue and at most this long after the run's latest
#: end (subminer's subtitle-cue-dedup threshold, findings/subminer.md S4).
_BURST_GAP_MS = 50
#: A run with no animation tag is a burst only when it has at least this many
#: frames, each at most _BURST_FRAME_MS long: two equal lines in a row are
#: ordinary dialogue far more often than an effect.
_BURST_MIN_FRAMES = 3
_BURST_FRAME_MS = 300
#: ASS overrides that animate a line: transforms, karaoke timing, moves, fades.
_ANIMATION_TAG_RE = re.compile(r"\\(?:t\(|[kK][fo]?\d|move\(|fade?\()")
#: A line whose first override block puts it at a fixed place on screen: a
#: typeset sign (SK-19), unless the file's main dialogue style carries it.
_POSITIONED_START_RE = re.compile(r"^\{[^}]*\\(?:pos|move)\(")


def _burst_key(event: pysubs2.SSAEvent) -> str:
    """The text a viewer sees, whitespace-folded: frames of one burst share it."""
    return " ".join(event.plaintext.split())


def _is_burst(run: list[pysubs2.SSAEvent]) -> bool:
    """Whether a run of same-text cues is one effect rather than separate lines."""
    if len(run) < 2:
        return False
    first = run[0]
    if all(event.start == first.start and event.end == first.end for event in run):
        return True  # layered copies of one cue: shadow, border and fill
    if any(_ANIMATION_TAG_RE.search(event.text) for event in run):
        return True
    return len(run) >= _BURST_MIN_FRAMES and all(event.end - event.start <= _BURST_FRAME_MS for event in run)


def collapse_frame_bursts(events: Sequence[pysubs2.SSAEvent]) -> list[pysubs2.SSAEvent]:
    """Collapse each karaoke or animated-sign frame burst into one cue (MS-12).

    Fansub karaoke and animated signs repeat one line once per frame, which
    inflates occurrence counts and turns 50 ms fragments into sentence
    candidates. A burst is a run of cues with the same visible text, each
    starting within ``_BURST_GAP_MS`` of the run's end, that is layered copies,
    carries an animation tag, or is at least three short frames. It becomes a
    copy of its first cue ending at the run's latest end, in that first cue's
    place. Every other cue keeps its place and timing; *events* is not changed.
    """
    runs: list[list[pysubs2.SSAEvent]] = []
    run_ends: list[int] = []
    run_of: list[int] = []
    open_runs: dict[str, int] = {}
    for event in events:
        key = _burst_key(event)
        index = open_runs.get(key) if key else None
        if index is not None and runs[index][0].start <= event.start <= run_ends[index] + _BURST_GAP_MS:
            runs[index].append(event)
            run_ends[index] = max(run_ends[index], event.end)
        else:
            index = len(runs)
            runs.append([event])
            run_ends.append(event.end)
            if key:
                open_runs[key] = index
        run_of.append(index)

    bursts = [_is_burst(run) for run in runs]
    result: list[pysubs2.SSAEvent] = []
    emitted: set[int] = set()
    for event, index in zip(events, run_of, strict=True):
        if not bursts[index]:
            result.append(event)
            continue
        if index in emitted:
            continue
        emitted.add(index)
        merged = runs[index][0].copy()
        merged.end = run_ends[index]
        result.append(merged)
    return result


def _main_styles(events: Sequence[pysubs2.SSAEvent]) -> frozenset[str]:
    """The style (or tied styles) most cues use: the file's dialogue."""
    counts = Counter(event.style for event in events)
    if not counts:
        return frozenset()
    most = max(counts.values())
    return frozenset(style for style, count in counts.items() if count == most)


def drop_non_dialogue_events(events: Sequence[pysubs2.SSAEvent]) -> list[pysubs2.SSAEvent]:
    """Drop song, sign and drawing cues (F-08 step 1, SK-19).

    A drawing (``\\p1``…``\\p9``) goes in any style. Outside the file's main
    style, a cue goes when its style name is a song/sign/credits name
    (:data:`NON_DIALOGUE_STYLE_RE`, the retime path's vocabulary) or its text
    opens with a ``\\pos``/``\\move`` placement. The main style is never
    dropped by name, so a file whose dialogue style is called "Caption" keeps
    its dialogue. An unstyled file (SRT, VTT: every cue "Default") is untouched.
    """
    main = _main_styles(events)
    kept: list[pysubs2.SSAEvent] = []
    for event in events:
        text = event.text or ""
        if ASS_DRAWING_RE.search(text):
            continue
        if event.style not in main and (
            NON_DIALOGUE_STYLE_RE.search(event.style or "") or _POSITIONED_START_RE.match(text)
        ):
            continue
        kept.append(event)
    return kept


def mining_events(events: Sequence[pysubs2.SSAEvent], *, skip_non_dialogue: bool) -> list[pysubs2.SSAEvent]:
    """The cues mining, Condense and Readability read from a loaded file.

    Comment events go, karaoke and animated-sign bursts collapse (MS-12, always),
    and with *skip_non_dialogue* (``config.skip_opening_ending``, F-08) songs,
    signs and drawings go too. The collapse runs first, so a karaoke opening's
    hundreds of frames cannot make its style the file's main style.
    """
    spoken = [event for event in events if getattr(event, "is_comment", None) is not True]
    collapsed = collapse_frame_bursts(spoken)
    return drop_non_dialogue_events(collapsed) if skip_non_dialogue else collapsed


#: Chapter titles that mark an opening, ending, preview or credits (F-08,
#: rulings R2 and C8). Latin names are whole words, case-insensitive, and may
#: carry a number (OP1, ED 2); "Episode", "Opener" and "Part A" do not match.
#: "Intro" is left out on purpose: cold opens carry dialogue (C8). The
#: Japanese names match anywhere in the title (次回予告).
OP_ED_CHAPTER_RE = re.compile(
    r"(?:^|[\W_])(?:op|ed|opening|ending|outro|preview|pv|credits?)(?=$|[\W_\d])" r"|オープニング|エンディング|予告",
    re.IGNORECASE,
)

#: Spans in seconds whose cues are left out, ``((start, end), ...)``.
SkipRanges = tuple[tuple[float, float], ...]


def op_ed_ranges(chapters: Sequence[Chapter]) -> SkipRanges:
    """The spans of *chapters* whose title names an opening, ending, preview or credits."""
    return tuple(
        (chapter.start, chapter.end)
        for chapter in chapters
        if chapter.end > chapter.start and chapter.title and OP_ED_CHAPTER_RE.search(chapter.title)
    )


def op_ed_skip_ranges(media: Path, ffprobe_cmd: str) -> SkipRanges:
    """Probe *media*'s chapters once and return its opening/ending spans (F-08 step 2).

    A path that is not an existing file is not probed: there is nothing to
    read, and the pipeline reports the missing file itself. A file without such
    chapters returns ``()``, which leaves every cue in place. An external
    subtitle file paired with a video is judged by the video's chapters: the
    caller passes the video here.
    """
    if not media.is_file():
        return ()
    chapters = list_chapters(media, ffprobe_cmd)
    ranges = op_ed_ranges(chapters)
    if ranges:
        titles = [chapter.title for chapter in chapters if (chapter.start, chapter.end) in ranges]
        logger.info("Skipping opening/ending chapters: file=%s chapters=%s", media.name, titles)
    return ranges


def midpoint_in_ranges(start: float, end: float, ranges: Sequence[tuple[float, float]]) -> bool:
    """Whether the cue ``[start, end]``'s midpoint lies in one of *ranges* (start in, end out)."""
    middle = (start + end) / 2
    return any(low <= middle < high for low, high in ranges)
