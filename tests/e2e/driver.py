"""In-process driver for the real ``SingleEpisodeTab`` (offscreen Qt).

:class:`EpisodeTabDriver` constructs the ACTUAL mining tab — not a mock, not the
full ``MainWindow`` — wired to a real :class:`EpisodeProcessor` via the project's
own ``GUIPresenter`` / ``GUIProgressCallback``, and drives it the way a user would:
it clicks the real buttons and reads the real progress/log widgets. It deliberately
avoids ``MainWindow`` so there is no blocking ``ResultsDialog`` / welcome dialog and
no heavy app startup; the tab itself shows results non-modally (via the presenter
signal), so preview/process runs need nothing dismissed.

Capture mechanics (connect-before-start)
----------------------------------------
``SingleEpisodeTab._start_processing`` builds the ``EpisodeWorkerThread``, connects
the tab's own slots, and calls ``.start()`` ALL synchronously on the click. A worker
that errors the instant ``run()`` begins can therefore emit ``error`` before any slot
the driver attaches *after* the click — for a Qt queued (default cross-thread)
connection a slot connected AFTER an emit does NOT receive that emission, so a
post-click connect would miss it and time out spuriously.

To capture deterministically, the driver does NOT connect after the click. Instead it
connects once (in ``__init__``) to the tab's ``worker_created`` signal — a test seam
the tab emits with the freshly built worker JUST BEFORE ``.start()``. That emit runs
synchronously on the GUI thread inside ``_start_processing`` (same-thread DIRECT
connection), so :meth:`_on_worker_created` attaches the capture slots to
``result_ready`` (payload = ``ProcessingResult``) and ``error`` (payload = ``str``)
BEFORE the worker is started and can emit. The ``click_*`` methods just click the
button; capture is armed by the signal. :meth:`wait_for_result` spins the GUI event
loop (the proven ``_drain_until`` idiom from
``tests/unit/test_mining_tab_base_curation.py``) until a payload is captured or the
timeout fires, joins the worker, then raises / returns.

``AppDriver`` (full-``MainWindow`` inspection mode) is intentionally NOT built here:
``MainWindow()`` takes no config (it loads the user's real ``gui_config.json``) and
runs heavy startup, so injecting the harness config + tearing it down cleanly is its
own task. A later harness task can add it on top of this driver.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from PyQt6.QtTest import QTest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.models import ProcessingResult
from anki_miner.services.stats_service import StatsService
from tests.e2e.artifacts import RunDir

__all__ = ["E2EMiningError", "E2ETimeout", "EpisodeTabDriver"]

# Bounded join for the worker after a result/error is captured (mirrors the
# project's MiningTabBase teardown timeout).
_WORKER_JOIN_TIMEOUT_MS = 5000


class E2ETimeout(RuntimeError):
    """Raised when a mining run does not finish within the driver's budget."""


class E2EMiningError(RuntimeError):
    """Raised when the worker emits its ``error`` signal instead of a result."""


def _drain_until(predicate, timeout_ms: int = 3000, step_ms: int = 10) -> bool:
    """Spin the GUI event loop (delivering queued signals) until predicate or timeout.

    Copied verbatim from ``tests/unit/test_mining_tab_base_curation.py`` — the
    proven way to advance queued Qt signal delivery in a headless test without a
    running ``app.exec()``.
    """
    waited = 0
    while not predicate() and waited < timeout_ms:
        # qWait is a static method at runtime; the PyQt6 stub mistypes it as an
        # instance method (and as returning Any), so the call + return are ignored.
        QTest.qWait(step_ms)  # type: ignore[call-arg, arg-type]
        waited += step_ms
    return bool(predicate())


class EpisodeTabDriver:
    """Drive a real ``SingleEpisodeTab`` in-process like a user would.

    Construct it, register ``driver.tab`` with ``qtbot.addWidget`` in the test,
    select files, click preview/process, then :meth:`wait_for_result`.

    Args:
        config: The mining config (build via ``app_config.build_app_config``).
        run_dir: Artifact directory for screenshots/JSON.
        stats_service: Optional stats service forwarded to the tab.

    Attributes:
        tab: The live ``SingleEpisodeTab`` (pass to ``qtbot.addWidget``).
        presenter: The ``GUIPresenter`` the tab/worker emit through.
        run_dir: The :class:`RunDir` screenshots are written to.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        run_dir: RunDir,
        stats_service: StatsService | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.presenter = GUIPresenter()
        self.progress_callback = GUIProgressCallback()
        self.tab = SingleEpisodeTab(
            config=config,
            presenter=self.presenter,
            progress_callback=self.progress_callback,
            stats_service=stats_service,
        )
        # Capture state, reset whenever a new worker is created (see
        # _on_worker_created). Connect ONCE: the tab emits worker_created
        # synchronously, just before .start(), so we attach our capture slots to
        # the worker before it can emit (connect-before-start — see module docstring).
        self._result: ProcessingResult | None = None
        self._error: str | None = None
        self.tab.worker_created.connect(self._on_worker_created)

    # ----- input actions -------------------------------------------------

    def select_video(self, path: Path | str) -> None:
        """Type a video path into the real video FileSelector and assert it validates."""
        self.tab.video_selector.set_path(str(path))
        assert self.tab.video_selector.is_valid(), f"video path did not validate: {path}"

    def select_subtitle(self, path: Path | str) -> None:
        """Type a subtitle path into the real subtitle FileSelector and assert it validates."""
        self.tab.subtitle_selector.set_path(str(path))
        assert self.tab.subtitle_selector.is_valid(), f"subtitle path did not validate: {path}"

    def set_offset(self, seconds: float) -> None:
        """Set the subtitle-offset spinbox value (as the user would)."""
        self.tab.offset_spinbox.setValue(seconds)

    # ----- run actions ---------------------------------------------------

    def _on_worker_created(self, worker: Any) -> None:
        """Reset capture state and connect one-shot slots to the new worker.

        Invoked synchronously (same-thread DIRECT connection) from
        ``SingleEpisodeTab._start_processing`` via the ``worker_created`` seam,
        JUST BEFORE the worker is started. Connecting here — before ``.start()``
        — means an emission from the worker (queued, cross-thread) cannot be
        missed even when the worker errors the instant ``run()`` begins. We
        connect to BOTH ``result_ready`` and ``error`` so whichever fires is
        captured; the matching payload is read in :meth:`wait_for_result` once
        the event loop is spun.
        """
        self._result = None
        self._error = None

        def _on_result(result: Any) -> None:
            self._result = result

        def _on_error(message: Any) -> None:
            self._error = str(message)

        worker.result_ready.connect(_on_result)
        worker.error.connect(_on_error)

    def click_preview(self) -> None:
        """Click the real Preview button (preview mode).

        Capture is armed by ``worker_created`` (see :meth:`_on_worker_created`),
        emitted before the worker starts — not by the click itself.
        """
        self.tab.preview_button.click()

    def click_process(self) -> None:
        """Click the real Process button (card creation).

        Capture is armed by ``worker_created`` (see :meth:`_on_worker_created`),
        emitted before the worker starts — not by the click itself.
        """
        self.tab.process_button.click()

    def click_cancel(self) -> None:
        """Click the real Cancel button."""
        self.tab.cancel_button.click()

    def wait_for_result(self, timeout_s: float = 120) -> ProcessingResult:
        """Spin the event loop until the worker reports a result or errors.

        Args:
            timeout_s: Wait budget. On expiry a screenshot is saved and
                :class:`E2ETimeout` is raised.

        Returns:
            The captured :class:`ProcessingResult`.

        Raises:
            E2ETimeout: No result/error within ``timeout_s``.
            E2EMiningError: The worker emitted its ``error`` signal.
        """
        timeout_ms = int(timeout_s * 1000)
        captured = _drain_until(
            lambda: self._result is not None or self._error is not None,
            timeout_ms=timeout_ms,
        )
        if not captured:
            self.screenshot("timeout")
            self.run_dir.save_json("timeout", {"timeout_s": timeout_s, "log": self.log_text()})
            raise E2ETimeout(f"mining did not finish within {timeout_s}s")

        # Join the worker now that it has emitted; safe even if it already
        # finished. wait() returns promptly once run() has returned.
        worker = self.tab.worker_thread
        if worker is not None:
            worker.wait(_WORKER_JOIN_TIMEOUT_MS)

        if self._error is not None:
            self.screenshot("error")
            raise E2EMiningError(self._error)

        assert self._result is not None  # captured implies one of the two
        return self._result

    # ----- widget readers ------------------------------------------------

    def progress_text(self) -> str:
        """Current progress status-label text (real ``ProgressWidget`` attribute)."""
        return self.tab.progress_widget.status_label.text()

    def progress_value(self) -> int:
        """Current progress-bar value (real ``ProgressWidget`` attribute)."""
        return self.tab.progress_widget.progress_bar.value()

    def log_text(self) -> str:
        """Full plain-text contents of the activity log (real ``LogWidget``)."""
        return self.tab.log_widget.text_edit.toPlainText()

    def screenshot(self, name: str) -> Path:
        """Grab the whole tab to an ordered PNG in the run dir."""
        return self.run_dir.save_png(name, self.tab)

    # ----- button-state predicates ---------------------------------------

    def process_button_enabled(self) -> bool:
        """Return whether the process button is not hidden (idle state).

        Uses ``isHidden()`` rather than ``isVisible()`` because ``isVisible()``
        walks the full parent chain — when the tab itself has never been
        ``show()``-n (offscreen tests) every child reports ``isVisible()=False``
        regardless of the button's own hidden state.  ``isHidden()`` reflects
        only the button's OWN hidden flag, set by ``hide()``/``show()`` in
        ``_start_processing`` / ``_restore_buttons``.
        """
        return self.tab.process_button.isEnabled() and not self.tab.process_button.isHidden()

    def cancel_button_visible(self) -> bool:
        """Return whether the cancel button is not hidden (running state).

        Uses ``not isHidden()`` for the same reason as :meth:`process_button_enabled`.
        """
        return not self.tab.cancel_button.isHidden()

    def buttons_idle(self) -> bool:
        """True when the tab is in idle state: process button shown+enabled, cancel hidden.

        This is the expected state AFTER a run completes (or errors). The tab
        shows preview/process/timing/tracks buttons and hides cancel via
        ``_restore_buttons``, which is connected to ``worker_thread.finished``.
        """
        return self.process_button_enabled() and not self.cancel_button_visible()

    def buttons_running(self) -> bool:
        """True when the tab is in running state: cancel shown, process hidden.

        The tab hides preview/process/timing/tracks and shows cancel inside
        ``_start_processing``; ``_restore_buttons`` reverses this when the
        worker finishes.
        """
        return self.cancel_button_visible() and self.tab.process_button.isHidden()

    # ----- teardown ------------------------------------------------------

    def teardown(self) -> None:
        """Stop/join the current worker, mirroring ``MiningTabBase._teardown_previous_run``.

        Safe to call when no run ever started. Cancels the worker, bounded-joins
        it, and only on a successful join closes its processor (so no stale
        sqlite handle / ``requests.Session`` survives) — closing under a still-
        running worker would be a concurrent-sqlite-close. The tab's own
        ``release_dictionary_resources`` is invoked too (a no-op while running)
        to release any lookup handles cached by a finished worker.
        """
        worker = self.tab.worker_thread
        if worker is None:
            return
        worker.cancel()
        joined = worker.wait(_WORKER_JOIN_TIMEOUT_MS)
        if joined:
            processor = worker.curation_processor
            if processor is not None:
                # Teardown must never raise (mirrors MiningTabBase's suppress).
                with contextlib.suppress(Exception):
                    processor.close()
        # Best-effort: also release any dict handles the tab's facade tracks.
        with contextlib.suppress(Exception):
            self.tab.release_dictionary_resources()

    def dispose(self) -> None:
        """FINAL cleanup: stop the worker AND release the tab QWidget.

        Distinct from :meth:`teardown`:

        * :meth:`teardown` = between-runs worker cleanup ONLY. The SAME tab is
          reused for the next session in the in-process soak loop, so teardown
          must NEVER delete it.
        * :meth:`dispose` = final disposal after the LAST session. It runs the
          worker teardown and THEN schedules the tab widget for C++ destruction
          via ``close()`` + ``deleteLater()`` so the conftest ``_drain_qt_deletes``
          net can actually flush the object instead of leaking an untracked
          top-level widget (the documented pytest-qt segfault hazard).

        Idempotent and safe to call when no tab/worker exists.
        """
        with contextlib.suppress(Exception):
            self.teardown()
        tab = getattr(self, "tab", None)
        if tab is None:
            return
        with contextlib.suppress(Exception):
            tab.close()
        with contextlib.suppress(Exception):
            tab.deleteLater()
        # Drop our reference so a second dispose() is a no-op.
        self.tab = None  # type: ignore[assignment]
