"""Which neighbouring subtitle cues belong to the word's own sentence.

A subtitle sentence is routinely split across two or three cues, so the cue a
word was mined from is often a fragment. This module decides — purely, from the
cue list alone — how many cues on each side finish that sentence, and returns
the answer in exactly the shape ``TokenizedWord.line_expansion`` already
carries (Issue #120): the Word Curator stamps the same pair by hand, and
``WordFilterService.expand_word_lines`` materialises both the same way. Nothing
here merges text or times; the intent is all it produces.

The terminator/ellipsis/closer sets come from the mining language's
``SentenceRules`` (``languages/profile.py``), so ja/ko/zh bring their own
punctuation and no call site branches on a language code. ``rules`` is a
parameter rather than a lookup so this module stays a leaf.

``SentenceRules.space_aware`` needs no handling here: it exists so a terminator
set containing ``.`` (Korean's) requires whitespace or end-of-text after the
run, and a cue's own end is always end-of-text.

Every bound is a module constant, never a setting: the one setting this feature
adds (``config.merge_incomplete_cues``) is the on/off switch.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from anki_miner.services.reading.sentence_splitter import _DOT, _run_is_terminating

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

#: Cues absorbed per side. A sentence split across more than three cues is
#: rare; more absorption mostly buys long clips, and the curator's ± line
#: buttons are right there for the exception.
MAX_MERGE_CUES = 2

#: Longest merged window, in seconds. ``gui/widgets/audio_clip_editor.py``
#: imports this as its own ``MAX_CLIP_SECONDS``, so one number bounds the
#: automatic merge and the manual strip alike: an automatic merge can never
#: produce a window the ± buttons would have refused.
MAX_MERGED_SECONDS = 30.0

#: Longest silence between two cues that can still be one sentence. A
#: continuation follows within a beat; two seconds of silence is a scene
#: change, not a comma.
MAX_MERGE_GAP_SECONDS = 2.0


def merge_budget_seconds(audio_padding: float) -> float:
    """The window budget for one merge at this ``config.audio_padding``.

    The extractor widens every clip by the padding on both sides, so the merged
    cue span must leave room for it — the same arithmetic the curator's
    ``_refresh_expansion_buttons`` does before enabling a ± button.
    """
    return max(0.0, MAX_MERGED_SECONDS - 2 * audio_padding)


def ends_sentence(text: str, rules: SentenceRules) -> bool:
    """Whether a cue's cleaned text ends a sentence under ``rules``.

    Trailing whitespace and closing brackets/quotes are stripped first, so
    ``「なるほど。」`` reads as terminated; the remaining run of sentence
    punctuation is judged by the splitter's own ``_run_is_terminating`` (a lone
    ``．`` terminates, a run of 2+ and the ellipsis marks do not).
    """
    stripped = text.rstrip()
    while stripped and stripped[-1] in rules.closers:
        stripped = stripped[:-1].rstrip()
    punct = rules.terminators | rules.ellipses | {_DOT}
    cut = len(stripped)
    while cut > 0 and stripped[cut - 1] in punct:
        cut -= 1
    run = stripped[cut:]
    return bool(run) and _run_is_terminating(run, rules.terminators)


def _span(entries: Sequence[tuple[float, float, str]], lo: int, hi: int) -> float:
    """Seconds from the earliest start to the latest end over ``entries[lo:hi + 1]``.

    min/max rather than ``entries[hi][1] - entries[lo][0]`` for the same reason
    ``merge_cue_window`` uses them: ASS events are not guaranteed ordered.
    """
    window = entries[lo : hi + 1]
    return max(cue[1] for cue in window) - min(cue[0] for cue in window)


def auto_line_expansion(
    entries: Sequence[tuple[float, float, str]],
    index: int,
    rules: SentenceRules,
    *,
    max_cues: int = MAX_MERGE_CUES,
    max_seconds: float = MAX_MERGED_SECONDS,
    max_gap: float = MAX_MERGE_GAP_SECONDS,
) -> tuple[int, int]:
    """``(prev_count, next_count)`` of cues that finish ``entries[index]``'s sentence.

    Forward: while the last cue in the window does not end a sentence, the
    following cue is part of it. Backward: while the cue *before* the window
    does not end a sentence, it runs into this one, so the word's cue is a
    continuation of it. Forward is resolved first and the backward span check
    includes it, so the pair is deterministic and the whole window obeys
    ``max_seconds``.

    ``(0, 0)`` — the common answer — for a cue that already ends a sentence and
    follows one, for a file edge, and for an out-of-range index.
    """
    if not 0 <= index < len(entries):
        return (0, 0)

    next_count = 0
    while next_count < max_cues:
        last = index + next_count
        following = last + 1
        if following >= len(entries) or ends_sentence(entries[last][2], rules):
            break
        if entries[following][0] - entries[last][1] > max_gap:
            break
        if _span(entries, index, following) > max_seconds:
            break
        next_count += 1

    prev_count = 0
    while prev_count < max_cues:
        first = index - prev_count
        preceding = first - 1
        if preceding < 0 or ends_sentence(entries[preceding][2], rules):
            break
        if entries[first][0] - entries[preceding][1] > max_gap:
            break
        if _span(entries, preceding, index + next_count) > max_seconds:
            break
        prev_count += 1

    return (prev_count, next_count)
