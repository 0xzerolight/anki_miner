"""Start Anki once per launch when the startup check finds it closed.

Opt-in through ``auto_open_anki``. Only the launch-time system check triggers
it; a check the user asks for (Refresh, Re-check) never starts Anki. After the
start it asks AnkiConnect again off the GUI thread every few seconds until Anki
answers or the attempts run out, then calls ``on_ready`` so the full checks run
again and the badges, health rows and deck lists catch up.

The next check is scheduled only after the previous one answers, because a
check can take up to its 5 s timeout, which is longer than the interval.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer

from anki_miner.gui.utils.ankiconnect_help import anki_launch_command, launch_anki
from anki_miner.gui.utils.run_off_thread import run_off_thread

logger = logging.getLogger(__name__)

#: Anki can sit on its profile picker or a sync before AnkiConnect listens.
POLL_INTERVAL_MS = 3000
POLL_ATTEMPTS = 40  # two minutes


class AnkiAutoOpener(QObject):
    """Launches Anki and waits for AnkiConnect to answer."""

    def __init__(
        self,
        parent: QObject,
        check: Callable[[], tuple[bool, str]],
        on_ready: Callable[[], None],
        *,
        interval_ms: int = POLL_INTERVAL_MS,
        attempts: int = POLL_ATTEMPTS,
    ) -> None:
        super().__init__(parent)
        self._check = check
        self._on_ready = on_ready
        self._attempts = attempts
        self._attempts_left = 0
        self._opened = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

    def open(self) -> None:
        """Start Anki and wait for AnkiConnect. Every call after the first does nothing."""
        if self._opened:
            return
        self._opened = True
        command = anki_launch_command()
        if command is None:
            logger.info("Open Anki at startup: Anki is not in its standard install place")
            return
        if not launch_anki(command):
            logger.warning("Open Anki at startup: the system did not start %s", command[0])
            return
        logger.info("Open Anki at startup: started %s", command[0])
        self._attempts_left = self._attempts
        self._timer.start()

    def _poll(self) -> None:
        self._attempts_left -= 1
        run_off_thread(self, self._check, self._on_checked, self._on_check_failed)

    def _on_checked(self, result: object) -> None:
        if isinstance(result, tuple) and result and result[0]:
            logger.info("Open Anki at startup: AnkiConnect answered")
            self._on_ready()
        elif self._attempts_left > 0:
            self._timer.start()
        else:
            logger.warning("Open Anki at startup: AnkiConnect did not answer after %d checks", self._attempts)

    def _on_check_failed(self, message: str) -> None:
        self._on_checked((False, message))
