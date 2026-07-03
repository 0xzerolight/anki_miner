"""QThread worker that checks installed dictionaries for available updates.

Runs the notify-only update check (``services/dictionary/updater``) off the GUI
thread for a batch of dictionaries, honouring cancellation between each. All
network work lives here, invoked only behind the explicit Settings →
Dictionaries → Check for updates action (plan 9.2).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import pyqtSignal

from anki_miner.exceptions import SetupError
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.dictionary.storage import read_meta
from anki_miner.services.dictionary.updater import UpdateInfo, check_for_update

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpdateCheckOutcome:
    """One dictionary's update-check result (only reported when noteworthy).

    ``info`` is set when a newer revision is available; ``error`` is set when the
    remote index could not be fetched or validated. Up-to-date and
    not-updatable dictionaries produce no outcome.
    """

    display_name: str
    dict_id: str
    info: UpdateInfo | None
    error: str | None


class DictionaryUpdateCheckWorker(CancellableWorker):
    """Checks a batch of dictionaries for updates in the background.

    Signals:
        progress(int, int): ``(checked_so_far, total)``.
        check_finished(list): list of :class:`UpdateCheckOutcome` — updates
            available plus per-dictionary errors.
        failed(str): catastrophic failure aborting the whole batch.
    """

    progress = pyqtSignal(int, int)
    check_finished = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(
        self,
        jobs: list[tuple[str, str, Path]],
        *,
        session_factory: Callable[[], object] | None = None,
        parent: object = None,
    ) -> None:
        """Args:
        jobs: ``(dict_id, display_name, db_path)`` for each dictionary to check.
        session_factory: Builds the HTTP client (``get(url, timeout)`` +
            ``close()``). Defaults to a ``requests.Session``; tests inject a fake.
        """
        super().__init__(parent)
        self._jobs = jobs
        self._session_factory = session_factory

    def _make_session(self) -> object:
        if self._session_factory is not None:
            return self._session_factory()
        import requests

        return requests.Session()

    def run(self) -> None:
        try:
            outcomes: list[UpdateCheckOutcome] = []
            session = self._make_session()
            try:
                total = len(self._jobs)
                for i, (dict_id, display_name, db_path) in enumerate(self._jobs):
                    if self.is_cancelled:
                        break
                    self.progress.emit(i, total)
                    meta = read_meta(db_path)
                    try:
                        info = check_for_update(meta, session=session)  # type: ignore[arg-type]
                    except SetupError as exc:
                        outcomes.append(UpdateCheckOutcome(display_name, dict_id, None, str(exc)))
                        continue
                    if info is not None:
                        outcomes.append(UpdateCheckOutcome(display_name, dict_id, info, None))
                self.progress.emit(total, total)
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            self.check_finished.emit(outcomes)
        except Exception as exc:  # noqa: BLE001 — surface every failure to GUI
            logger.exception("DictionaryUpdateCheckWorker unhandled exception")
            self.failed.emit(str(exc))
