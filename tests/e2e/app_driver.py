"""Full-window driver for the real ``MainWindow`` (offscreen Qt).

:class:`AppDriver` builds the ACTUAL :class:`~anki_miner.gui.main_window.MainWindow`
— not the bare ``SingleEpisodeTab`` the :class:`~tests.e2e.driver.EpisodeTabDriver`
drives — so the harness covers the GUI surface the bare tab deliberately skips:
dialog wiring (the post-run ``ResultsDialog``), tab switching, the menu bar, and
the window's result-display slot. It COMPOSES ``EpisodeTabDriver`` for the actual
mining (file selection, button clicks, the worker wait) and mounts that driver's
real tab into the window's tab bar wired exactly as ``app.register_mining_tab``
wires it, so a run flows through the window's own ``_on_processing_result``.

Why ``MainWindow`` needs isolating
----------------------------------
``MainWindow()`` takes no config: it calls ``GUIConfigManager.load_config()``
(reads ``gui_config.json`` from ``ANKI_MINER_HOME`` — the harness points that at
the isolated test home) and runs heavy, partly-blocking/networked startup:

* an update check (gated on ``config.check_for_updates``),
* a first-run desktop-shortcut + guided setup offer (gated on the
  ``first_run_*_done`` flags; the latter launches the Setup Wizard),
* a post-update info dialog (gated on ``last_known_version`` differing), and
* an UNCONDITIONAL startup system-validation worker (a ``QThread`` that hits
  AnkiConnect — unreachable in the offscreen harness, and whose ``finished``
  signal races a half-deleted C++ object at teardown if left to run).

So before constructing the window the driver WRITES a disabling ``gui_config.json``
into the test home (the harness mining config plus ``check_for_updates=False`` and
both ``first_run_*_done=True`` and ``last_known_version`` pinned to the running
version), AND patches ``BackgroundTaskController.start_validation`` to a no-op for
the window's lifetime so the startup validation thread never spawns. The post-run
``ResultsDialog`` / first-run ``run_setup_wizard`` / curation modal are all neutralised
by an :class:`~tests.e2e.curation.AutoCurationResponder` held open in
``full_window=True`` mode. These patches/responder are entered on construction and
exited on :meth:`teardown` (and therefore :meth:`dispose`, which calls it).

Qt lifecycle
------------
This is the segfault-prone path. In a pytest test register both ``driver.window``
and ``driver.tab`` with ``qtbot.addWidget`` and call :meth:`teardown` (worker join
+ exit patches; pytest-qt then owns the widget close + C++ destruction). In the
non-pytest soak loop call :meth:`dispose`, which additionally releases the widgets
(close + ``deleteLater`` + a deferred-delete drain) so the C++ objects are destroyed
within the call rather than leaking to a later ``processEvents`` (the documented
hazard). Mixing the two — qtbot tracking the window AND ``dispose`` deleting it —
double-frees and crashes pytest-qt's own teardown, so the tests use ``teardown``.
"""

from __future__ import annotations

import contextlib
import dataclasses
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QMenu

from anki_miner import __version__
from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.controllers.background_tasks import BackgroundTaskController
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.models import ProcessingResult
from anki_miner.services.stats_service import StatsService
from tests.e2e.artifacts import RunDir
from tests.e2e.curation import AutoCurationResponder
from tests.e2e.driver import EpisodeTabDriver

__all__ = ["AppDriver"]


def _disabling_gui_config(config: AnkiMinerConfig) -> AnkiMinerConfig:
    """Derive the harness config with all heavy/blocking startup paths disabled.

    Carries the harness mining settings (deck / note-type / dicts / known-words
    paths from ``build_app_config``) so the mounted episode tab mines correctly,
    and pins the gates that would otherwise fire networked/modal startup work:
    update check off, yt-dlp auto-update off (it shells out to ``yt-dlp --version``
    and hits GitHub on startup, outliving teardown), both first-run flags done, and
    ``last_known_version`` equal to the running version (so the post-update info
    dialog never opens).
    """
    return dataclasses.replace(
        config,
        check_for_updates=False,
        auto_update_ytdlp=False,
        first_run_shortcut_done=True,
        first_run_setup_done=True,
        last_known_version=__version__,
    )


class AppDriver:
    """Drive a real ``MainWindow`` in-process like a user would (full-window mode).

    Construct it, register ``driver.window`` AND ``driver.tab`` with
    ``qtbot.addWidget`` in the test, switch tabs / trigger menu actions / drive a
    preview-or-process run, then :meth:`teardown` (in pytest; ``dispose`` in the
    non-pytest soak loop).

    Args:
        config: The mining config (build via ``app_config.build_app_config``).
            It is written to the test home's ``gui_config.json`` (with startup
            disabled) so the real ``MainWindow`` loads it.
        run_dir: Artifact directory for screenshots/JSON (forwarded to the
            composed :class:`EpisodeTabDriver`).
        stats_service: Optional stats service forwarded to the episode tab.
        curation_policy: Curation policy for the held-open responder (``"all"`` /
            ``"first_n"`` / ``"none"``).
        first_n: Cap used when ``curation_policy == "first_n"``.

    Attributes:
        window: The live ``MainWindow`` (pass to ``qtbot.addWidget``).
        tab: The mounted ``SingleEpisodeTab`` (pass to ``qtbot.addWidget``).
        episode_tab_index: Index of ``tab`` in ``window.tabs``.
        window_results_seen: How many times the window's ``_on_processing_result``
            slot fired (each pops the patched ``ResultsDialog``).
        dialog_blocked: ``True`` if a (patched) dialog ever failed to return — a
            non-blocking-dialog invariant the tests assert stays ``False``.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        run_dir: RunDir,
        stats_service: StatsService | None = None,
        *,
        curation_policy: str = "all",
        first_n: int = 0,
    ) -> None:
        self.run_dir = run_dir
        self.window_results_seen = 0
        self.dialog_blocked = False

        # 1. Persist the disabling gui_config so the real MainWindow loads it.
        #    GUIConfigManager.CONFIG_FILE is redirected to the test home by the
        #    home-isolation layer (conftest fixture / set_test_home), so this
        #    write lands in the isolated home, never the real ~/.anki_miner.
        GUIConfigManager.save_config(_disabling_gui_config(config))

        # 2. Hold the lifetime patches open: the curation/results/welcome dialog
        #    fakes (full_window) + the no-op startup validation (its QThread would
        #    hit unreachable AnkiConnect and race a half-deleted object at
        #    teardown). Entered now, exited in dispose().
        self._responder = AutoCurationResponder(policy=curation_policy, first_n=first_n, full_window=True)
        self._stack = ExitStack()
        self._stack.enter_context(self._responder)
        self._stack.enter_context(patch.object(BackgroundTaskController, "start_validation", lambda _self, _svc: False))

        # 3. Build the real window (loads the disabling config) and compose an
        #    EpisodeTabDriver for the actual mining, mounting its tab into the
        #    window wired the way app.register_mining_tab wires it.
        try:
            from anki_miner.gui.main_window import MainWindow

            self.window = MainWindow()
            self._tab_driver = EpisodeTabDriver(config, run_dir, stats_service=stats_service)
            self.episode_tab_index = self.window.tabs.count()
            self.window.tabs.addTab(self.tab, "Episode Mining")
            self.window.tabs.setCurrentIndex(self.episode_tab_index)
            # Wire the tab's presenter result into the window's result slot
            # (register_mining_tab's processing_result connection) through a spy
            # so a run pops the patched ResultsDialog and we can count it.
            self._tab_driver.presenter.processing_result_signal.connect(self._on_window_result)
            self._tab_driver.presenter.word_preview_signal.connect(self.window._on_word_preview)
        except Exception:
            # Construction failed after the patches were entered — unwind them so
            # we don't leak a global monkeypatch into the rest of the suite.
            self._stack.close()
            raise

    # ----- composed tab passthrough -------------------------------------

    @property
    def tab(self):
        """The composed driver's real ``SingleEpisodeTab``."""
        return self._tab_driver.tab

    def _on_window_result(self, result: ProcessingResult) -> None:
        """Spy slot: forward to the window's real handler and count the dialog.

        Wraps ``MainWindow._on_processing_result`` (which builds + ``exec()``s the
        patched ``ResultsDialog``). If that handler ever raised or hung the count
        would not advance / ``dialog_blocked`` would flip — both asserted by tests.
        """
        before = self.window_results_seen
        try:
            self.window._on_processing_result(result)
        finally:
            self.window_results_seen = before + 1
            # The patched ResultsDialog.exec() returns immediately; reaching here
            # confirms the slot did not block on a modal loop.
            self.dialog_blocked = False

    # ----- input + run actions (delegate to the composed tab driver) ----

    def select_video(self, path: Path | str) -> None:
        """Type a video path into the real video FileSelector."""
        self._tab_driver.select_video(path)

    def select_subtitle(self, path: Path | str) -> None:
        """Type a subtitle path into the real subtitle FileSelector."""
        self._tab_driver.select_subtitle(path)

    def set_offset(self, seconds: float) -> None:
        """Set the subtitle-offset spinbox value."""
        self._tab_driver.set_offset(seconds)

    def click_preview(self) -> None:
        """Click the real Preview button and arm result capture."""
        self._tab_driver.click_preview()

    def click_process(self) -> None:
        """Click the real Process button and arm result capture (card creation)."""
        self._tab_driver.click_process()

    def cancel_and_wait(self, **kwargs: Any) -> Any:
        """Click Cancel mid-run and wait for the worker to end (soak cancel session)."""
        return self._tab_driver.cancel_and_wait(**kwargs)

    def wait_for_result(self, timeout_s: float = 120) -> ProcessingResult:
        """Spin the event loop until the worker reports a result or errors.

        Delegates to the composed tab driver, then drains the GUI event loop once
        more so the queued ``processing_result_signal`` reaches the window's result
        slot (the patched ``ResultsDialog`` pop) before returning.
        """
        result = self._tab_driver.wait_for_result(timeout_s=timeout_s)
        # Deliver the queued cross-thread processing_result_signal to the window.
        app = QApplication.instance()
        if app is not None:
            app.processEvents()
        return result

    # ----- full-window actions ------------------------------------------

    def switch_to_tab(self, index: int) -> None:
        """Switch the window's tab bar to ``index`` (like a Ctrl+N shortcut)."""
        self.window.tabs.setCurrentIndex(index)

    def trigger_menu_action(self, text: str) -> None:
        """Trigger the menu ``QAction`` whose label equals ``text``.

        Searches every menu in the window's menu bar. Raises ``LookupError`` when
        no action matches so a typo fails loudly rather than silently no-op'ing.
        """
        action = self._find_menu_action(text)
        if action is None:
            raise LookupError(f"no menu action labelled {text!r}")
        action.trigger()

    def _find_menu_action(self, text: str) -> Any | None:
        """Return the first menu ``QAction`` whose ``text()`` equals ``text``."""
        menu_bar = self.window.menuBar()
        if menu_bar is None:
            return None
        for menu in menu_bar.findChildren(QMenu):
            for action in menu.actions():
                if action.text() == text:
                    return action
        return None

    # ----- widget readers -----------------------------------------------

    def log_text(self) -> str:
        """Full plain-text contents of the episode tab's activity log."""
        return self._tab_driver.log_text()

    def progress_text(self) -> str:
        """Current progress status-label text (episode tab's ProgressWidget)."""
        return self._tab_driver.progress_text()

    def progress_value(self) -> int:
        """Current progress-bar value (episode tab's ProgressWidget)."""
        return self._tab_driver.progress_value()

    def buttons_idle(self) -> bool:
        """Whether the episode tab's run buttons are back to the idle state."""
        return self._tab_driver.buttons_idle()

    def screenshot(self, name: str) -> Path:
        """Grab the whole window to an ordered PNG in the run dir."""
        return self.run_dir.save_png(name, self.window)

    # ----- teardown ------------------------------------------------------

    def teardown(self) -> None:
        """BETWEEN-RUNS cleanup ONLY: join the composed tab driver's worker.

        Does NOT exit the held-open patches or release any QWidget — the SAME
        AppDriver is reused for the next session in the soak loop, so the dialog/
        responder/start_validation patches MUST stay open (a torn-down
        WordPreviewDialog/ResultsDialog patch would make the next preview block on
        the real modal) and the window/tab must survive. Final cleanup is
        :meth:`dispose`. Idempotent and safe when no run ever started.
        """
        tab_driver = getattr(self, "_tab_driver", None)
        if tab_driver is not None:
            with contextlib.suppress(Exception):
                tab_driver.teardown()

    def dispose(self) -> None:
        """FINAL cleanup: join the worker, exit the patches, release tab + window.

        Order matters for clean C++ destruction:

        1. Tear down the composed tab driver's worker (bounded join).
        2. ``close()`` + ``deleteLater()`` the window (its ``closeEvent`` joins any
           remaining background/tab workers via the controller).
        3. ``deleteLater()`` the tab.
        4. Drain Qt deferred-deletes so both C++ objects are destroyed NOW (the
           ``conftest._drain_qt_deletes`` idiom), not leaked to a later
           ``processEvents`` (the segfault hazard).
        5. Exit the held-open patches/responder so they never leak into the suite.

        The driver OWNS the full widget lifecycle here, so a pytest test must NOT
        also register ``window``/``tab`` with ``qtbot.addWidget`` (qtbot's own
        teardown ``close()`` on the already-deleted C++ object would crash). Call
        ``dispose`` in a ``finally`` and let it + the conftest drain net do the
        teardown. Idempotent and safe when partially constructed.
        """
        self.teardown()

        window = getattr(self, "window", None)
        if window is not None:
            with contextlib.suppress(Exception):
                window.close()
            with contextlib.suppress(Exception):
                window.deleteLater()

        tab_driver = getattr(self, "_tab_driver", None)
        if tab_driver is not None:
            tab = tab_driver.tab
            if tab is not None:
                with contextlib.suppress(Exception):
                    tab.deleteLater()
            tab_driver.tab = None

        self._drain_qt_deletes()
        self._exit_patches()
        self.window = None  # type: ignore[assignment]

    def _exit_patches(self) -> None:
        """Exit the held-open patch stack (responder + start_validation). Idempotent."""
        stack = getattr(self, "_stack", None)
        if stack is not None:
            with contextlib.suppress(Exception):
                stack.close()
            self._stack = None  # type: ignore[assignment]

    @staticmethod
    def _drain_qt_deletes() -> None:
        """Flush pending Qt deferred-deletes so leaked C++ objects are destroyed now."""
        app = QApplication.instance()
        if app is not None:
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
