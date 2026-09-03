"""Durable queue contents: a bounded, versioned, atomically-replaced snapshot.

D16-C: a queue someone spent twenty minutes assembling is not thrown away when
the app closes. One JSON file per queue under
``<home>/runtime_state/queues/<key>.json``, written on close and offered back on
the next launch through :mod:`~anki_miner.gui.controllers.recovery_controller`.

Three rules shape everything here:

**Only immutable facts are stored.** A queue row at rest is its inputs and its
outcome — a path, a URL, a status, a count. Never a QObject, a worker, a
processor, an event, a temporary workspace or a fetched media file. A YouTube row
keeps its URL and title and is re-probed; the ``VideoInfo`` a probe produced is
derived data with a lifetime shorter than the snapshot's.

**A row that was running is never restored as runnable.** Its status comes back
as :data:`STATUS_INTERRUPTED` — "Interrupted when Anki Miner closed" — which is
an unknown, not a failure and not a ready row. W5-T5 owns what the app can prove
about Anki writes and W5-T6 owns retry eligibility; an interrupted row satisfies
neither, so nothing about it is automatically re-run. This module is deliberately
not a second write journal.

**A pasted-text source is a form draft, and drafts are never restored (D7-B).**
:func:`reading_source` refuses ``ReadingSourceRef(kind="text")`` outright rather
than storing content the user typed into a box.

Reading is hostile-input reading: the file is size-capped before it is parsed,
the schema version must match exactly, every field is type- and bound-checked,
and anything unexpected yields "no snapshot" rather than a partial restore.

Paths are resolved from ``GUIConfigManager.CONFIG_FILE`` at call time (see
:mod:`anki_miner.gui.utils.runtime_state`), and the whole tree sits outside both
``AnkiMinerConfig`` and ``profiles/`` — so a settings export or a profile sidecar
structurally cannot carry it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anki_miner.gui.utils.runtime_state import is_within, queue_state_root, validate_key
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.utils.atomic_io import atomic_write_path
from anki_miner.utils.bounded_reader import read_json_bounded
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: Bumped when an older file can no longer be read. Mismatches are discarded
#: rather than migrated — the cost of guessing is a wrong queue, the cost of
#: discarding is re-adding rows.
SCHEMA_VERSION = 1

#: A 200-item queue of paths is a few tens of KiB. A megabyte is generous and
#: still bounds what a corrupt or hostile file can make the parser do.
MAX_BYTES = 1024 * 1024

#: Rows past this are dropped on save. A queue longer than this is a bug, and
#: restoring one would take longer than rebuilding it.
MAX_ITEMS = 500

#: Field length cap. Titles and error strings are for display; a megabyte of
#: them is not.
MAX_TEXT = 2000

# --- neutral status vocabulary ------------------------------------------------
# Facts about a row, not wording. W5/D30 owns what the user reads.
STATUS_READY = "ready"
STATUS_INTERRUPTED = "interrupted"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"
_STATUSES = frozenset({STATUS_READY, STATUS_INTERRUPTED, STATUS_COMPLETED, STATUS_ERROR})


def status_from_run_state(value: str) -> str:
    """Reduce a queue enum's value to the four facts worth persisting.

    ``processing`` becomes :data:`STATUS_INTERRUPTED` rather than round-tripping:
    a row that was running when the app closed is an unknown, and calling it
    ready would invite a re-run whose Anki writes the app cannot account for.
    Probe-time states (``pending``, ``probing``, ``probe_error``) all collapse to
    ready — a probe is cheap and is redone on restore anyway.
    """
    if value == "processing":
        return STATUS_INTERRUPTED
    if value == "completed":
        return STATUS_COMPLETED
    if value == "error":
        return STATUS_ERROR
    return STATUS_READY


# --- source descriptor kinds --------------------------------------------------
SOURCE_FOLDER_PAIR = "folder_pair"
SOURCE_FILE_PAIR = "file_pair"
SOURCE_URL = "url"
SOURCE_READING_REF = "reading_ref"

#: Which keys of each descriptor name a file that has to still exist. A row whose
#: input has since been moved or deleted restores as a failure rather than as a
#: row that would fail the moment it ran.
_PATH_KEYS: dict[str, tuple[str, ...]] = {
    SOURCE_FOLDER_PAIR: ("video", "subtitle"),
    SOURCE_FILE_PAIR: ("audio", "subtitle"),
    SOURCE_URL: (),
    SOURCE_READING_REF: ("path",),
}

_UNREADABLE = object()


# ---------------------------------------------------------------------------
# Source descriptors
# ---------------------------------------------------------------------------


def folder_pair_source(video: Path, subtitle: Path, *, offset: float = 0.0) -> dict[str, Any]:
    """Descriptor for a Batch video/subtitle folder pair."""
    return {"kind": SOURCE_FOLDER_PAIR, "video": str(video), "subtitle": str(subtitle), "offset": float(offset)}


def file_pair_source(audio: Path, subtitle: Path) -> dict[str, Any]:
    """Descriptor for an Audiobook audio/subtitle file pair."""
    return {"kind": SOURCE_FILE_PAIR, "audio": str(audio), "subtitle": str(subtitle)}


def url_source(url: str, *, title: str = "") -> dict[str, Any]:
    """Descriptor for a YouTube row: the URL and the label, nothing derived.

    A probe result names formats, a resolved subtitle mode and a workspace that
    may already have been cleaned up. None of that survives a restart, so none of
    it is written — the row is re-probed instead.
    """
    return {"kind": SOURCE_URL, "url": url, "title": _clip(title)}


def reading_source(ref: ReadingSourceRef) -> dict[str, Any] | None:
    """Descriptor for a file-backed reading source, or ``None`` for pasted text.

    ``kind="text"`` carries content the user typed into a box. That is a form
    draft, and D7-B says drafts are never restored — so it is not written at all,
    rather than written and skipped on the way back in.
    """
    if ref.kind == "text" or ref.path is None:
        return None
    return {
        "kind": SOURCE_READING_REF,
        "ref_kind": ref.kind,
        "path": str(ref.path),
        # An archive-backed volume keeps both halves of its identity: the
        # archive it lives in and, for a self-contained .cbz, the .mokuro member
        # inside it. Dropping ``ocr_entry`` would restore a volume with no OCR.
        "image_root": None if ref.image_root is None else str(ref.image_root),
        "title": _clip(ref.title),
        "volume": None if ref.volume is None else _clip(ref.volume),
        "ocr_entry": ref.ocr_entry,
    }


def reading_ref_from_source(source: Mapping[str, Any]) -> ReadingSourceRef | None:
    """Rebuild a :class:`ReadingSourceRef`, or ``None`` when the row is not one."""
    if source.get("kind") != SOURCE_READING_REF:
        return None
    ref_kind = source.get("ref_kind")
    path = source.get("path")
    if ref_kind not in {"mokuro", "epub", "txt", "subtitle"} or not isinstance(path, str):
        return None
    image_root = source.get("image_root")
    try:
        return ReadingSourceRef(
            kind=ref_kind,
            path=Path(path),
            image_root=None if image_root is None else Path(image_root),
            title=str(source.get("title") or ""),
            volume=source.get("volume"),
            ocr_entry=source.get("ocr_entry"),
        )
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Snapshot model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueItemSnapshot:
    """One durable queue row: its identity, its inputs and its outcome."""

    item_id: str
    source: Mapping[str, Any]
    title: str = ""
    status: str = STATUS_READY
    retry_count: int = 0
    error: str = ""
    result_count: int = 0

    @property
    def is_interrupted(self) -> bool:
        """Whether this row was mid-run when the app closed.

        Such a row is an unknown: the app cannot prove whether notes reached
        Anki, so it is never re-run without the user asking.
        """
        return self.status == STATUS_INTERRUPTED

    def input_paths(self) -> tuple[Path, ...]:
        """Every filesystem input this row needs, in descriptor order."""
        keys = _PATH_KEYS.get(str(self.source.get("kind")), ())
        return tuple(Path(str(self.source[key])) for key in keys if isinstance(self.source.get(key), str))

    def missing_paths(self) -> tuple[Path, ...]:
        """Inputs that are no longer on disk."""
        return tuple(path for path in self.input_paths() if not path.exists())

    def to_json(self) -> dict[str, Any]:
        """Serialise for :data:`SCHEMA_VERSION`."""
        return {
            "id": self.item_id,
            "source": dict(self.source),
            "title": self.title,
            "status": self.status,
            "retry_count": self.retry_count,
            "error": self.error,
            "result_count": self.result_count,
        }


@dataclass(frozen=True)
class QueueSnapshot:
    """Everything one queue needs to come back: its key and its ordered rows."""

    key: str
    items: tuple[QueueItemSnapshot, ...] = field(default_factory=tuple)

    @property
    def interrupted_count(self) -> int:
        """How many rows were mid-run when the app closed."""
        return sum(1 for item in self.items if item.is_interrupted)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def snapshot_path(key: str) -> Path:
    """Path of the snapshot file for ``key``, resolved at call time."""
    return queue_state_root() / f"{validate_key(key)}.json"


def save(snapshot: QueueSnapshot) -> None:
    """Write ``snapshot`` atomically, or remove the file when it is empty.

    Best-effort by construction: this runs from ``closeEvent``, and a read-only
    home or a full disk must not stop the window from closing.
    """
    try:
        path = snapshot_path(snapshot.key)
        if not snapshot.items:
            discard(snapshot.key)
            return
        payload = {
            "version": SCHEMA_VERSION,
            "key": snapshot.key,
            "items": [item.to_json() for item in snapshot.items[:MAX_ITEMS]],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_write_path(path) as tmp:
            tmp.write_text(json.dumps(payload), encoding="utf-8")
    except (OSError, ValueError, TypeError):
        logger.warning("Could not save the %s queue for recovery", snapshot.key, exc_info=True)


def load(key: str) -> QueueSnapshot | None:
    """Return the stored snapshot for ``key``, or ``None`` when there is none.

    ``None`` covers absent, oversized, undecodable, wrong-version and
    wrong-shaped alike: a queue is a convenience, and half a restored queue is
    worse than none. Individual rows that fail validation are dropped; the rows
    around them keep their order and their ids.

    Both outcomes are logged, because "restore lost my items" is otherwise
    indistinguishable from a bug in the screen that took the rows back. An
    unusable file is DEBUG — an absent snapshot is the normal first launch —
    while rows silently dropped out of a snapshot the user *did* get back are
    WARNING: that is the case where the count on screen is lower than the count
    they left behind, and nothing else records why. One line per restore, never
    one per row.
    """
    try:
        path = snapshot_path(key)
    except ValueError:
        log_summary(logger, "Queue restore skipped", level=logging.DEBUG, key=key, reason="invalid_key")
        return None
    raw = read_json_bounded(path, MAX_BYTES, _UNREADABLE, "queue snapshot")
    if raw is _UNREADABLE or not isinstance(raw, dict):
        reason = "unreadable" if raw is _UNREADABLE else "not_an_object"
        log_summary(logger, "Queue restore skipped", level=logging.DEBUG, key=key, reason=reason, path=path)
        return None
    if raw.get("version") != SCHEMA_VERSION or raw.get("key") != key:
        reason = "version_mismatch" if raw.get("version") != SCHEMA_VERSION else "key_mismatch"
        log_summary(
            logger,
            "Queue restore skipped",
            level=logging.DEBUG,
            key=key,
            reason=reason,
            found_version=raw.get("version"),
            found_key=raw.get("key"),
        )
        return None
    rows = raw.get("items")
    if not isinstance(rows, list) or len(rows) > MAX_ITEMS:
        log_summary(
            logger,
            "Queue restore skipped",
            level=logging.DEBUG,
            key=key,
            reason="items_not_a_bounded_list",
            rows=len(rows) if isinstance(rows, list) else None,
            limit=MAX_ITEMS,
        )
        return None
    items = tuple(item for item in (_decode_item(row) for row in rows) if item is not None)
    if not items:
        log_summary(
            logger,
            "Queue restore skipped",
            level=logging.DEBUG,
            key=key,
            reason="no_valid_rows",
            rows=len(rows),
        )
        return None
    dropped = len(rows) - len(items)
    if dropped:
        log_summary(
            logger,
            "Queue restore dropped rows",
            level=logging.WARNING,
            key=key,
            kept=len(items),
            dropped=dropped,
            path=path,
        )
    return QueueSnapshot(key=key, items=items)


def discard(key: str) -> None:
    """Remove the snapshot for ``key``. Only ever unlinks beneath our own root."""
    try:
        path = snapshot_path(key)
    except ValueError:
        return
    if not is_within(path.parent, queue_state_root()):
        return
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def stored_keys() -> tuple[str, ...]:
    """Every key with a snapshot on disk, sorted. Never raises."""
    root = queue_state_root()
    keys: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.endswith(".json"):
                    continue
                stem = entry.name[: -len(".json")]
                try:
                    keys.append(validate_key(stem))
                except ValueError:
                    continue
    except OSError:
        return ()
    return tuple(sorted(keys))


def discard_all(keys: Iterable[str] | None = None) -> None:
    """Remove every stored snapshot (or just ``keys``).

    Resolved paths only, and only beneath the queue root — Discard must never
    reach a file a hand-edited name pointed somewhere else.
    """
    for key in stored_keys() if keys is None else keys:
        discard(key)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_item(row: object) -> QueueItemSnapshot | None:
    """Validate one stored row, or return ``None`` to drop it."""
    if not isinstance(row, dict):
        return None
    item_id = row.get("id")
    if not isinstance(item_id, str) or not item_id or len(item_id) > MAX_TEXT:
        return None
    source = _decode_source(row.get("source"))
    if source is None:
        return None
    status = row.get("status")
    if status not in _STATUSES:
        return None
    retry_count = row.get("retry_count", 0)
    result_count = row.get("result_count", 0)
    # bool is an int subclass; a JSON `true` must not read as a count.
    if not _is_count(retry_count) or not _is_count(result_count):
        return None
    title = row.get("title", "")
    error = row.get("error", "")
    if not isinstance(title, str) or not isinstance(error, str):
        return None
    return QueueItemSnapshot(
        item_id=item_id,
        source=source,
        title=_clip(title),
        status=status,
        retry_count=retry_count,
        error=_clip(error),
        result_count=result_count,
    )


def _decode_source(source: object) -> dict[str, Any] | None:
    """Validate one source descriptor, or return ``None`` to drop the row."""
    if not isinstance(source, dict):
        return None
    kind = source.get("kind")
    if kind not in _PATH_KEYS:
        return None
    for key in _PATH_KEYS[kind]:
        value = source.get(key)
        if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
            return None
    if kind == SOURCE_URL:
        url = source.get("url")
        if not isinstance(url, str) or not url or len(url) > MAX_TEXT:
            return None
    if kind == SOURCE_READING_REF and reading_ref_from_source(source) is None:
        return None
    if kind == SOURCE_FOLDER_PAIR and not isinstance(source.get("offset", 0.0), (int, float)):
        return None
    return dict(source)


def _is_count(value: object) -> bool:
    """Whether ``value`` is a non-negative, non-boolean integer."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1_000_000


def _clip(text: str) -> str:
    """Bound a display string so a corrupt file cannot carry a novel."""
    return text[:MAX_TEXT]
