"""Subtitle retiming orchestrator: clean → align → validate → commit.

One episode's retime is a pipeline, not a single tool call:

1. **Reference** — :mod:`anki_miner.services.retime_reference` picks what to
   align against (embedded dialogue track preferred, extracted audio fallback,
   raw video as last resort).
2. **Clean** — :mod:`anki_miner.services.subtitle_cleaner` strips non-dialogue
   cues (signs, songs, ♪ markers, HoH annotations) from the input into a
   same-format copy; aligners see dialogue only.
3. **Align** — engines are tried in order until one produces a candidate that
   survives validation: ffsubsync in split mode (in-process, has its own
   quality gate), then alass in split mode, then alass with a single global
   offset. A missing alass binary (macOS) just shortens the chain.
4. **Map back** — the winning candidate's timings are applied to the untouched
   original, so every line and all ASS styling survive.
5. **Validate** — :mod:`anki_miner.services.sync_validator` rejects the failure
   signatures aligners produce when they lock onto a wrong optimum. A rejected
   candidate is discarded; the chain moves on.
6. **Commit** — only a validated candidate replaces *out_sub*, atomically, and
   an existing file is first copied to ``<name>.pre-retime.bak``.

The guarantee callers build UX on: **a bad sync never overwrites a usable
subtitle** — when every engine fails validation the original files are left
exactly as they were and the outcome says why.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from anki_miner.exceptions.subtitle import AlassNotFoundError
from anki_miner.services.retime_reference import ReferenceOverride, resolve_reference
from anki_miner.services.subtitle_cleaner import clean_for_alignment, map_deltas_back
from anki_miner.services.sync_engines import SyncResult
from anki_miner.services.sync_engines.alass_engine import sync_with_alass
from anki_miner.services.sync_engines.ffsubsync_engine import sync_with_ffsubsync
from anki_miner.services.sync_validator import validate_candidate
from anki_miner.utils.audio_track_detector import get_media_duration_seconds
from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe

logger = logging.getLogger(__name__)

__all__ = ["RetimeOutcome", "retime_subtitle"]

#: Sibling suffix for the pre-overwrite copy of an existing output subtitle.
BACKUP_SUFFIX = ".pre-retime.bak"


@dataclass(frozen=True)
class RetimeOutcome:
    """What one episode's retime pipeline did.

    Truthy exactly when a validated result was written. ``attempts`` holds one
    human-readable line per engine tried — the forensic trail the old pipeline
    never kept.
    """

    ok: bool
    engine: str | None = None
    reference_label: str | None = None
    attempts: tuple[str, ...] = ()
    reason: str = ""
    cancelled: bool = False

    def __bool__(self) -> bool:
        return self.ok


def retime_subtitle(
    config,
    video: Path,
    in_sub: Path,
    out_sub: Path,
    *,
    reference_override: ReferenceOverride | None = None,
    cancel_event: threading.Event | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> RetimeOutcome:
    """Retime *in_sub* to *video*, writing a validated result to *out_sub*.

    Engine order, validation, and the keep-original guarantee are described in
    the module docstring. Never raises for content or tool reasons; every
    failure path comes back as a falsy :class:`RetimeOutcome` with the original
    files untouched.

    Args:
        config: Application config (used to resolve tool paths and temp dirs).
        video:  The video the subtitle should be matched against.
        in_sub: The off-timed subtitle file to correct.
        out_sub: Destination path. May alias *in_sub* (in-place retime): the
            pipeline writes temps beside it and replaces atomically, and the
            pre-replace backup keeps the original recoverable.
        reference_override: Explicit user pick of the reference track; None
            auto-selects (embedded subtitle preferred, audio fallback).
        cancel_event: Checked between pipeline steps; alass runs are killed
            mid-flight, an in-process ffsubsync run finishes first.
        log_cb: Called with human-readable progress/decision lines.
    """
    logger.info("retime: %s <- %s (out %s)", video.name, in_sub.name, out_sub.name)
    temps: list[Path] = []
    reference = None
    try:
        duration_s = get_media_duration_seconds(video, resolve_ffprobe(config))
        reference = resolve_reference(
            config,
            video,
            override=reference_override,
            video_duration_seconds=duration_s,
            cancel_event=cancel_event,
            log_cb=log_cb,
        )
        if _cancelled(cancel_event):
            return RetimeOutcome(ok=False, cancelled=True, reason="cancelled")

        reference_label = reference.label if reference is not None else "raw video"
        reference_path = reference.path if reference is not None else video
        sub_reference = reference is not None and reference.kind == "subtitle"

        cleaned = clean_for_alignment(in_sub, out_sub.parent / (out_sub.stem + ".retime-clean" + in_sub.suffix))
        if cleaned is not None:
            temps.append(cleaned.path)
            if cleaned.dropped:
                _log(log_cb, f"Ignoring {cleaned.dropped} non-dialogue lines during alignment.")
        align_input = cleaned.path if cleaned is not None else in_sub

        attempts: list[str] = []
        alass_missing = False
        for label, runner in _engine_chain(config, sub_reference=sub_reference, cancel_event=cancel_event):
            if _cancelled(cancel_event):
                return RetimeOutcome(
                    ok=False,
                    cancelled=True,
                    reference_label=reference_label,
                    attempts=tuple(attempts),
                    reason="cancelled",
                )
            if alass_missing and label.startswith("alass"):
                continue

            candidate = out_sub.parent / f"{out_sub.stem}.retime-cand-{len(attempts)}{out_sub.suffix}"
            temps.append(candidate)
            try:
                result = runner(reference_path, align_input, candidate, log_cb)
            except AlassNotFoundError:
                alass_missing = True
                attempts.append(f"{label}: binary not installed")
                _log(log_cb, "alass is not installed; skipping alass attempts.")
                continue

            final_candidate = candidate
            if result.ok and cleaned is not None:
                mapped = out_sub.parent / f"{out_sub.stem}.retime-map-{len(attempts)}{out_sub.suffix}"
                temps.append(mapped)
                if map_deltas_back(in_sub, candidate, cleaned.kept_indices, mapped):
                    final_candidate = mapped
                else:
                    attempts.append(f"{label}: aligner changed the cue count; discarded")
                    continue

            verdict = validate_candidate(in_sub, final_candidate, result, video_duration_seconds=duration_s)
            if verdict.ok:
                attempts.append(f"{label}: accepted")
                _commit(final_candidate, out_sub)
                summary = _success_summary(result)
                logger.info(
                    "retime: %s accepted for %s (reference %s)%s",
                    label,
                    video.name,
                    reference_label,
                    summary,
                )
                _log(log_cb, f"Retimed with {label}{summary}.")
                return RetimeOutcome(
                    ok=True,
                    engine=result.engine,
                    reference_label=reference_label,
                    attempts=tuple(attempts),
                )

            reason = "; ".join(verdict.reasons) or "rejected"
            attempts.append(f"{label}: {reason}")
            _log(log_cb, f"{label} result rejected: {reason}")

        reason = "no engine produced a trustworthy sync; original left untouched"
        logger.warning(
            "retime: kept original for %s (reference %s). Attempts: %s",
            video.name,
            reference_label,
            " | ".join(attempts) or "none",
        )
        return RetimeOutcome(
            ok=False,
            reference_label=reference_label,
            attempts=tuple(attempts),
            reason=reason,
        )
    finally:
        for temp in temps:
            _unlink_quiet(temp)
        if reference is not None and reference.temp is not None:
            _unlink_quiet(reference.temp)


def _engine_chain(config, *, sub_reference: bool, cancel_event: threading.Event | None):
    """Yield ``(label, runner)`` pairs in the order they should be tried."""

    def run_ffsubsync(reference: Path, in_sub: Path, out: Path, log_cb) -> SyncResult:
        return sync_with_ffsubsync(config, reference, in_sub, out, cancel_event=cancel_event, log_cb=log_cb)

    def run_alass_split(reference: Path, in_sub: Path, out: Path, log_cb) -> SyncResult:
        return sync_with_alass(
            config,
            reference,
            in_sub,
            out,
            sub_reference=sub_reference,
            no_split=False,
            cancel_event=cancel_event,
            log_cb=log_cb,
        )

    def run_alass_offset(reference: Path, in_sub: Path, out: Path, log_cb) -> SyncResult:
        return sync_with_alass(
            config,
            reference,
            in_sub,
            out,
            sub_reference=sub_reference,
            no_split=True,
            cancel_event=cancel_event,
            log_cb=log_cb,
        )

    yield "ffsubsync", run_ffsubsync
    yield "alass", run_alass_split
    yield "alass (single offset)", run_alass_offset


def _commit(candidate: Path, out_sub: Path) -> None:
    """Atomically place *candidate* at *out_sub*, backing up any existing file.

    The backup is a copy (not a rename): with in-place retiming the existing
    output *is* the input subtitle, which must stay readable until the final
    ``os.replace``. One backup per output name; a rerun overwrites it.
    """
    if out_sub.exists():
        backup = out_sub.with_name(out_sub.name + BACKUP_SUFFIX)
        try:
            shutil.copy2(out_sub, backup)
        except OSError:
            logger.warning("retime: could not write backup %s", backup, exc_info=True)
    os.replace(candidate, out_sub)


def _success_summary(result: SyncResult) -> str:
    if result.offset_seconds is not None:
        return f" (offset {result.offset_seconds:+.2f}s)"
    if result.block_shifts_seconds:
        shifts = result.block_shifts_seconds
        if len(shifts) == 1:
            return f" (offset {shifts[0]:+.2f}s)"
        return f" ({len(shifts)} blocks, shifts {min(shifts):+.2f}s..{max(shifts):+.2f}s)"
    return ""


def _cancelled(cancel_event: threading.Event | None) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def _log(log_cb: Callable[[str], None] | None, message: str) -> None:
    logger.info("retime: %s", message)
    if log_cb is not None:
        log_cb(message)


def _unlink_quiet(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)
