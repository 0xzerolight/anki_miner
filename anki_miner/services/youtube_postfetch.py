"""What a YouTube download still needs before mining: a transcribed or an aligned subtitle.

Shared by ``EpisodeProcessor.process_youtube_url`` and ``--api fetch``.
Qt-free: a step is reported by its key and the caller names it in its own
words. The ASR and retime entry points are imported inside the functions, as
the processor imported them, so tests that patch those module attributes keep
reaching them.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.youtube import TranscriptionFailedError, TranscriptionProducedNothingError
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.models.youtube import FetchedMedia

logger = logging.getLogger(__name__)

FetchStep = Literal["extracting", "transcribing", "aligning"]
StepReport = Callable[[FetchStep, float | None], None]


def transcribe_fetched(
    config: AnkiMinerConfig,
    media_extractor: object,
    fetched: FetchedMedia,
    workspace: Path,
    cancel_event: threading.Event,
    report: StepReport | None = None,
) -> FetchedMedia:
    """Fill a subtitle-less fetch by transcribing the downloaded video.

    The SRT is written into the caller-owned *workspace*, so the caller's
    cleanup of that folder covers it. *report* hears every step: ASR is by far
    the longest stage of a fetch and must never look hung.

    Returns the media unchanged (still ``subtitle_file=None``) when the run
    was cancelled mid-pass; the caller turns that into a cancelled result.
    """
    from anki_miner.services.asr.subtitle_generation import SubtitleGenStatus, generate_subtitle_one

    def _report(step: FetchStep, frac: float | None) -> None:
        if report is not None:
            report(step, frac)

    out_srt = workspace / f"{fetched.video_file.stem}.srt"
    result = generate_subtitle_one(
        config,
        media_extractor,
        fetched.video_file,
        out_srt,
        on_extract_start=lambda: _report("extracting", None),
        on_transcribe_start=lambda: _report("transcribing", 0.0),
        transcribe_progress_cb=lambda frac: _report("transcribing", frac),
        cancel_event=cancel_event,
        language=get_profile(config_language(config)).asr_language,
    )
    if result.status is SubtitleGenStatus.NO_SPEECH:
        raise TranscriptionProducedNothingError("Local transcription recognised no speech in the downloaded video.")
    if result.status is SubtitleGenStatus.EXTRACTION_FAILED:
        raise TranscriptionFailedError("Could not extract audio from the downloaded video for transcription.")
    if result.out_srt is None:
        return fetched
    return replace(fetched, subtitle_file=result.out_srt)


def align_fetched(
    config: AnkiMinerConfig,
    fetched: FetchedMedia,
    workspace: Path,
    cancel_event: threading.Event,
    report: StepReport | None = None,
) -> FetchedMedia | None:
    """Retime fetched captions against the video's own audio.

    Returns None when the alignment was cancelled — the outcome carries that
    fact, so the caller never has to re-read the event to learn it.

    Best-effort by contract: ``retime_subtitle`` never raises for content or
    tool reasons — every failure comes back as a falsy ``RetimeOutcome`` with
    the original file untouched — so alignment can degrade but never fail a
    run. YouTube's auto-captions being out of sync is why this exists.

    Writes a sibling rather than overwriting: ``retime_subtitle`` keeps no
    copy when ``out_sub`` is ``in_sub``, and a bad alignment must not destroy
    the captions we would otherwise mine.
    """
    from anki_miner.services.subtitle_retimer import retime_subtitle

    if fetched.subtitle_file is None:  # pragma: no cover - callers gate on this
        return fetched
    if report is not None:
        report("aligning", None)
    source = fetched.subtitle_file
    out_sub = workspace / f"{source.stem}.retimed{source.suffix}"
    outcome = retime_subtitle(
        config,
        fetched.video_file,
        source,
        out_sub,
        cancel_event=cancel_event,
        log_cb=logger.debug,
    )
    if outcome.cancelled:
        return None
    if not outcome:
        logger.info("YouTube caption alignment did not apply: %s", outcome.reason)
        return fetched
    logger.info("YouTube captions aligned with %s", outcome.engine)
    return replace(fetched, subtitle_file=out_sub)
