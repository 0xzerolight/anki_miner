"""Worker that condenses media files to dialogue-only audio (Audio Condenser).

The 5-signal contract and per-file queue loop live in
:class:`~anki_miner.gui.workers.file_queue_worker.FileQueueWorker`; the per-file
product policy (subtitle-source priority, JP-track pick, the pure interval math,
the ffmpeg condense pass, sidecar writing) lives in
:func:`~anki_miner.services.audio_condenser.condense_one`. This worker is the
signal adapter: it uses the precomputed output path, runs the skip gate, calls
``condense_one``, and maps its structured :class:`~anki_miner.services.audio_condenser.CondenseStatus`
back to translated messages. ``EncoderUnavailableError`` and
``FilterUnavailableError`` are declared as queue-stopping fatal exceptions.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from anki_miner.gui.workers.file_queue_worker import FileQueueWorker
from anki_miner.services.audio_condenser import (
    CondenseResult,
    CondenseStatus,
    EncoderUnavailableError,
    FilterUnavailableError,
    condense_one,
)
from anki_miner.services.audio_tagger import TrackMetadata
from anki_miner.utils.file_pairing import output_path_identity, resolve_output_paths
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)

_DEFAULT_COMPONENT_BYTES = 255
_CONDENSED_SUFFIX = "_condensed"
_TRUNCATED_HASH_CHARS = 12


@dataclass
class CondenseItem:
    """One media file queued for condensing.

    ``external_sub`` is a user-picked subtitle file (single mode); when None the
    service discovers a sibling or embedded subtitle track (D9). ``metadata``
    is set only when the Tag-output-files dialog ran (Issue #113); None keeps
    the legacy untagged behavior.
    """

    media: Path
    external_sub: Path | None = None
    metadata: TrackMetadata | None = None


class CondenseOutputCollisionError(ValueError):
    """Raised when multiple Condense inputs share a planned output path."""

    def __init__(self, collisions: dict[Path, tuple[Path, ...]]) -> None:
        super().__init__("Multiple Condense inputs would write to the same output path")
        self.collisions = collisions


def plan_condense_outputs(
    items: list[CondenseItem],
    output_dir: Path | None,
    output_format: str,
) -> list[Path]:
    """Resolve bounded output paths for every queued item before work starts."""
    extension = f".{output_format}"
    requests_by_dir: dict[Path, list[tuple[int, str]]] = {}
    for index, item in enumerate(items):
        out_dir = (output_dir if output_dir is not None else item.media.parent).resolve()
        name = _bounded_condense_name(item.media.stem, extension, _component_byte_limit(out_dir))
        requests_by_dir.setdefault(out_dir, []).append((index, name))

    outputs_by_index: dict[int, Path] = {}
    for out_dir, requests in requests_by_dir.items():
        resolved = resolve_output_paths(out_dir, [name for _index, name in requests])
        for (index, _name), path in zip(requests, resolved, strict=True):
            outputs_by_index[index] = path
    outputs = [outputs_by_index[index] for index in range(len(items))]
    return validate_condense_outputs(items, outputs)


def validate_condense_outputs(items: list[CondenseItem], output_paths: list[Path]) -> list[Path]:
    """Return *output_paths* or raise for missing or duplicate destinations."""
    paths = list(output_paths)
    if len(paths) != len(items):
        raise ValueError("output_paths must contain one path per CondenseItem")

    outputs_by_identity: dict[tuple[Path, str | None], tuple[Path, list[Path]]] = {}
    for item, output in zip(items, paths, strict=True):
        _first_output, sources = outputs_by_identity.setdefault(
            output_path_identity(output),
            (output, []),
        )
        sources.append(item.media)
    collisions = {output: tuple(sources) for output, sources in outputs_by_identity.values() if len(sources) > 1}
    if collisions:
        raise CondenseOutputCollisionError(collisions)
    return paths


def _bounded_condense_name(stem: str, extension: str, byte_limit: int) -> str:
    fixed = _CONDENSED_SUFFIX + extension
    candidate = stem + fixed
    if len(candidate.encode("utf-8")) <= byte_limit:
        return candidate

    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:_TRUNCATED_HASH_CHARS]
    marker = f"-{digest}"
    stem_budget = byte_limit - len((marker + fixed).encode("utf-8"))
    if stem_budget < 0:
        raise ValueError("Condense output suffix exceeds the filesystem component limit")
    return _truncate_utf8(stem, stem_budget) + marker + fixed


def _truncate_utf8(value: str, byte_budget: int) -> str:
    used = 0
    for index, char in enumerate(value):
        char_bytes = len(char.encode("utf-8"))
        if used + char_bytes > byte_budget:
            return value[:index]
        used += char_bytes
    return value


def _component_byte_limit(directory: Path) -> int:
    try:
        limit = os.pathconf(directory, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        return _DEFAULT_COMPONENT_BYTES
    return limit if limit > 0 else _DEFAULT_COMPONENT_BYTES


class CondenseWorker(FileQueueWorker):
    """Condense a list of media files down to their dialogue audio.

    Per file:
    1. Emits ``file_started(idx)``.
    2. Uses the planned ``<stem>_condensed.<format>`` path in *output_dir* (or
       next to the media). If it already exists and *overwrite* is False — emits
       ``file_skipped(idx, out_audio, reason)`` and continues.
    3. Delegates to :func:`~anki_miner.services.audio_condenser.condense_one`,
       which resolves a subtitle source (D9 priority), runs the pipeline, invokes
       ffmpeg, and optionally writes sidecars.
    4. Maps the returned :class:`~anki_miner.services.audio_condenser.CondenseStatus`
       to a translated ``file_finished`` / ``file_progress`` message. On a
       successful audio write whose optional sidecar write failed, the warning is
       surfaced through the final progress message — never as a ``file_finished``
       error (the audio is already good).

    :class:`~anki_miner.services.audio_condenser.EncoderUnavailableError` and
    :class:`~anki_miner.services.audio_condenser.FilterUnavailableError` stop the
    entire queue (every remaining file would hit the same broken ffmpeg) after
    emitting a per-file error for the triggering file — distinct from a
    user cancel (``is_cancelled`` stays False). All other exceptions are caught
    per-file so the queue continues.

    Cancel is honoured between files and propagated into the service via
    ``self._cancel_event``. After the loop, ``queue_finished()`` is emitted
    unconditionally.

    Args:
        config: Frozen :class:`~anki_miner.config.AnkiMinerConfig` instance.
        items: Ordered list of :class:`CondenseItem`.
        output_dir: When given, condensed audio is written here instead of next
            to each source media file.
        output_paths: Precomputed paths from :func:`plan_condense_outputs`.
            Direct worker callers may omit this and let the worker plan them.
        overwrite: When ``True``, an existing condensed audio file is regenerated.
        padding_ms: Milliseconds of padding added to each cue before merging.
        offset_ms: Millisecond offset applied to every cue (once).
        output_format: ``mp3`` | ``opus`` | ``flac`` — the audio container/codec.
        bitrate_kbps: Bitrate for lossy formats (ignored by flac).
        filtered_chars: Characters whose removal empties a line (SFX/music glyphs).
        write_subs: When ``True``, condensed SRT + LRC sidecars are written.
        audio_track_override: Audio-stream index to condense; None auto-detects.
        subtitle_track_override: Embedded subtitle ``sub_index`` to extract; None
            picks the first Japanese-tagged text track, else the first text track.
        service: Optional :class:`~anki_miner.services.audio_condenser.AudioCondenserService`;
            one is built from *config* if omitted (injected by tests).
        parent: Optional parent QObject.
    """

    #: A missing encoder, or an ffmpeg whose ``aselect`` does not filter, dooms
    #: every remaining file — stop the queue (see base loop).
    _FATAL_QUEUE_EXCEPTIONS = (EncoderUnavailableError, FilterUnavailableError)

    def __init__(
        self,
        config,
        items: list[CondenseItem],
        *,
        output_dir: Path | None = None,
        output_paths: list[Path] | None = None,
        overwrite: bool = False,
        padding_ms: int = 500,
        offset_ms: int = 0,
        output_format: str = "mp3",
        bitrate_kbps: int = 96,
        filtered_chars: str = "",
        write_subs: bool = False,
        audio_track_override: int | None = None,
        subtitle_track_override: int | None = None,
        service=None,
        parent=None,
    ) -> None:
        """Initialise the worker."""
        super().__init__(parent)
        self._config = config
        self._items = list(items)
        self._output_dir = output_dir
        planned_outputs = (
            plan_condense_outputs(self._items, output_dir, output_format)
            if output_paths is None
            else list(output_paths)
        )
        self._output_paths = validate_condense_outputs(self._items, planned_outputs)
        self._overwrite = overwrite
        self._padding_ms = padding_ms
        self._offset_ms = offset_ms
        self._bitrate_kbps = bitrate_kbps
        self._filtered_chars = filtered_chars
        self._write_subs = write_subs
        self._audio_track_override = audio_track_override
        self._subtitle_track_override = subtitle_track_override

        if service is None:
            from anki_miner.services.audio_condenser import AudioCondenserService

            self._service = AudioCondenserService(config)
        else:
            self._service = service

    def _queue_items(self) -> list[CondenseItem]:
        return self._items

    def _process_item(self, idx: int, item: CondenseItem) -> None:
        out_audio = self._output_paths[idx]

        # Skip-if-exists keyed on the audio file only (D11).
        if out_audio.exists() and not self._overwrite:
            logger.debug("condense_worker: skipped %s (exists)", out_audio)
            msg = self.tr("Skipped, exists")
            self.file_progress.emit(idx, 100, msg)
            self.file_skipped.emit(idx, out_audio, msg)
            return

        if self._output_dir is not None:
            self._output_dir.mkdir(parents=True, exist_ok=True)

        self._process_file(idx, item, out_audio)

    def _process_file(self, idx: int, item: CondenseItem, out_audio: Path) -> None:
        """Run :func:`condense_one` for one file and map its result to signals.

        Per-file errors are forwarded as signals; only a ``_FATAL_QUEUE_EXCEPTIONS``
        member (encoder missing) propagates, for the base loop to stop the queue.
        """

        def _progress_cb(pct: int) -> None:
            self.file_progress.emit(idx, pct, tr_format(self.tr("Condensing: %1%"), pct))

        try:
            result = condense_one(
                self._service,
                self._config,
                item.media,
                item.external_sub,
                out_audio,
                offset_ms=self._offset_ms,
                padding_ms=self._padding_ms,
                filtered_chars=self._filtered_chars,
                bitrate_kbps=self._bitrate_kbps,
                audio_track_override=self._audio_track_override,
                subtitle_track_override=self._subtitle_track_override,
                write_subs=self._write_subs,
                metadata=item.metadata,
                progress_cb=_progress_cb,
                cancel_event=self._cancel_event,
            )
        except self._FATAL_QUEUE_EXCEPTIONS:
            # A broken ffmpeg affects every remaining file. Re-raise so the base
            # queue loop reports this file's error and stops the queue without
            # poisoning is_cancelled (a tool error, not a user cancel).
            raise
        except Exception as exc:  # noqa: BLE001 — per-file isolation
            logger.exception("condense_worker: error on %s", item.media)
            if not self.is_cancelled:
                self.file_finished.emit(idx, None, str(exc))
            return

        self._emit_result(idx, item, result)

    def _emit_result(self, idx: int, item: CondenseItem, result: CondenseResult) -> None:
        """Map a :class:`CondenseResult` status code to translated worker signals."""
        name = item.media.name
        status = result.status

        if status is CondenseStatus.SUCCESS:
            if result.sidecar_error and result.tag_error:
                warning = tr_format(
                    self.tr("Audio done; subtitle write failed: %1; tagging failed: %2"),
                    result.sidecar_error,
                    result.tag_error,
                )
            elif result.sidecar_error:
                warning = tr_format(self.tr("Audio done; subtitle write failed: %1"), result.sidecar_error)
            elif result.tag_error:
                warning = tr_format(self.tr("Audio done; tagging failed: %1"), result.tag_error)
            else:
                warning = None
            self.file_progress.emit(idx, 100, warning or self.tr("Done"))
            self.file_finished.emit(idx, result.out_audio, None)
        elif status is CondenseStatus.CANCELLED:
            self.file_finished.emit(idx, None, self.tr("Cancelled"))
        elif status is CondenseStatus.NO_SOURCE:
            self.file_finished.emit(idx, None, tr_format(self.tr("No subtitle source found for %1"), name))
        elif status is CondenseStatus.SUBTITLE_TRACK_NOT_FOUND:
            self.file_finished.emit(
                idx,
                None,
                tr_format(
                    self.tr("Subtitle track %1 not found in %2"),
                    self._subtitle_track_override,
                    name,
                ),
            )
        elif status is CondenseStatus.BITMAP_ONLY:
            self.file_finished.emit(
                idx,
                None,
                tr_format(
                    self.tr("Only image-based subtitles (%1) in %2, which can't be condensed"),
                    result.codecs,
                    name,
                ),
            )
        elif status is CondenseStatus.EXTRACT_FAILED:
            self.file_finished.emit(idx, None, tr_format(self.tr("Failed to extract embedded subtitle from %1"), name))
        elif status is CondenseStatus.NO_DIALOGUE:
            self.file_finished.emit(idx, None, tr_format(self.tr("No dialogue lines found in %1"), name))
        elif status is CondenseStatus.CONDENSE_FAILED:
            # CONDENSE_FAILED covers a launch failure, a nonzero exit and a
            # timeout alike, so the bare name is not actionable — carry ffmpeg's
            # own one-line diagnosis. The full 50-line tail stays in the log:
            # for the aselect-depth bug the offending line was the whole 4 KB
            # filter expression, and this string lands in the Activity Log and
            # the Copy buffer behind it.
            self.file_finished.emit(
                idx,
                None,
                tr_format(self.tr("Condensing failed for %1"), name)
                + (f" — {result.failure_reason}" if result.failure_reason else ""),
            )
