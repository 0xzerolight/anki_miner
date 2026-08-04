"""What survived the last session, and the one question the app asks about it.

D16-C. On the next launch the app takes stock of two things it deliberately kept:
partial downloads under ``runtime_state/downloads`` and queue contents under
``runtime_state/queues``. If there is anything to offer it asks once —
*"Resume JMdict download? 312 MB already saved"*, *"Restore previous queue?
200 items"* — with **Restore** and **Discard**, and then gets out of the way.

Three things this controller deliberately does not do:

* it never restores automatically, and it never *runs* anything it restored. A
  row that was mid-run comes back as "Interrupted when Anki Miner closed" and
  waits for the user;
* it never re-validates a partial download itself. Whether those bytes may be
  appended to is decided at the moment of the request by
  :mod:`anki_miner.services.download_resume`, against the server's own
  validators. Restore here means "keep the file"; the resume leg still has to
  prove the artifact is unchanged and silently restarts clean if it cannot;
* on Discard it removes only resolved paths beneath the two runtime-state roots.

The prompt is a **choice**, not an error report: there is no failure to recover
from and no answer the app could pick on the user's behalf. It is classified as
such in ``tests/unit/test_message_box_policy.py``.
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QMessageBox, QWidget

from anki_miner.gui.utils import queue_state_store
from anki_miner.gui.utils.queue_state_store import QueueSnapshot
from anki_miner.gui.utils.runtime_state import download_resume_root, is_within
from anki_miner.services.download_resume import ResumeState
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: Below this a partial is not worth a question — finishing it costs less than
#: reading the sentence that offers it.
MIN_OFFERED_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PartialDownload:
    """One kept partial transfer, described only by what is on disk."""

    key: str
    saved_bytes: int
    total_bytes: int
    url: str


@dataclass(frozen=True)
class RecoveryInventory:
    """Everything the previous session left behind, and nothing derived."""

    downloads: tuple[PartialDownload, ...] = field(default_factory=tuple)
    queues: tuple[QueueSnapshot, ...] = field(default_factory=tuple)

    @property
    def queued_items(self) -> int:
        """Total rows across every restorable queue."""
        return sum(len(snapshot.items) for snapshot in self.queues)

    def __bool__(self) -> bool:
        """Whether there is anything worth asking about."""
        return bool(self.downloads or self.queues)


def take_inventory() -> RecoveryInventory:
    """Report what is recoverable. Never raises, never mutates anything."""
    inventory = RecoveryInventory(downloads=_partial_downloads(), queues=_queue_snapshots())
    log_summary(
        logger,
        "Recovery inventory",
        snapshots=len(inventory.queues),
        downloads=len(inventory.downloads),
        queued_items=inventory.queued_items,
    )
    return inventory


def _partial_downloads() -> tuple[PartialDownload, ...]:
    root = download_resume_root()
    found: list[PartialDownload] = []
    try:
        with os.scandir(root) as entries:
            names = sorted(entry.name for entry in entries if entry.is_file() and entry.name.endswith(".json"))
    except OSError:
        return ()
    for name in names:
        key = name[: -len(".json")]
        try:
            state = ResumeState(root, key)
        except ValueError:
            continue
        manifest = state.load()
        if manifest is None:
            continue
        try:
            saved = state.part_path.stat().st_size
        except OSError:
            continue
        # The manifest is the authority on how many bytes are DURABLE; anything
        # past it was never fsynced and is truncated away on resume.
        saved = min(saved, manifest.length)
        if saved < MIN_OFFERED_BYTES:
            continue
        found.append(PartialDownload(key=key, saved_bytes=saved, total_bytes=manifest.total, url=manifest.url))
    return tuple(found)


def _queue_snapshots() -> tuple[QueueSnapshot, ...]:
    snapshots = []
    for key in queue_state_store.stored_keys():
        snapshot = queue_state_store.load(key)
        if snapshot is not None:
            snapshots.append(snapshot)
    return tuple(snapshots)


def discard_all() -> None:
    """Delete every kept partial and queue snapshot.

    Only resolved paths beneath the two runtime-state roots are ever unlinked,
    so a hand-edited key or a symlinked ``.part`` cannot redirect the deletion.
    """
    root = download_resume_root()
    try:
        with os.scandir(root) as entries:
            names = [entry.name for entry in entries]
    except OSError:
        names = []
    for name in names:
        path = root / name
        if not is_within(path, root):
            continue
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)
    queue_state_store.discard_all()


def format_bytes(count: int) -> str:
    """Render a byte count the way the offer sentence reads it: ``312 MB``."""
    if count >= 1024**3:
        return f"{count / 1024**3:.1f} GB"
    if count >= 1024**2:
        return f"{count / 1024**2:.0f} MB"
    return f"{max(count // 1024, 1)} KB"


class RecoveryController:
    """Asks the one Restore/Discard question and reports the answer.

    Owns no widgets and no state beyond the inventory it was handed. The caller
    decides what "restore" means for each of its screens; all this does is make
    sure the user was asked exactly once, after the UI exists to receive the
    answer.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        """Bind the prompt to ``parent`` (used only as the dialog's owner)."""
        self._parent = parent
        self._asked = False

    @property
    def asked(self) -> bool:
        """Whether the question has already been put to the user this session."""
        return self._asked

    def offer(self, inventory: RecoveryInventory) -> bool:
        """Ask about ``inventory``; return True when the user chose Restore.

        An empty inventory asks nothing and answers False. Asking twice is a
        programming error the second call simply declines to make.
        """
        if self._asked or not inventory:
            return False
        self._asked = True

        box = QMessageBox(self._parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(QCoreApplication.translate("RecoveryController", "Pick up where you left off?"))
        box.setText(describe(inventory))
        box.setInformativeText(
            QCoreApplication.translate("RecoveryController", "Nothing starts on its own — restored rows wait for you.")
        )
        restore = box.addButton(
            QCoreApplication.translate("RecoveryController", "Restore"), QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton(
            QCoreApplication.translate("RecoveryController", "Discard"), QMessageBox.ButtonRole.DestructiveRole
        )
        box.setDefaultButton(restore)
        box.exec()
        restored = box.clickedButton() is restore
        log_summary(
            logger,
            "Recovery choice",
            action="restore" if restored else "discard",
            snapshots=len(inventory.queues),
            queued_items=inventory.queued_items,
        )
        return restored


def describe(inventory: RecoveryInventory) -> str:
    """One line per thing on offer, each stating a number the app actually has."""
    lines: list[str] = []
    for download in inventory.downloads:
        lines.append(
            tr_format(
                QCoreApplication.translate("RecoveryController", "Resume %1? %2 already saved"),
                _download_label(download),
                format_bytes(download.saved_bytes),
            )
        )
    for snapshot in inventory.queues:
        lines.append(
            tr_format(
                QCoreApplication.translate("RecoveryController", "Restore previous queue? %1 items"),
                len(snapshot.items),
            )
        )
    return "\n".join(lines)


def _download_label(download: PartialDownload) -> str:
    """A human name for a partial: the artifact's filename, never the URL."""
    name = Path(download.url).name
    return name or download.key
