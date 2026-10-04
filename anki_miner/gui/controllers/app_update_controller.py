"""The in-place app update: the banner's Update now → download → Restart now.

The banner shows state; this controller owns the run:
- It starts the download through BackgroundTaskController, which joins the
  worker at shutdown.
- It publishes progress to the TaskRegistry, the app's one progress owner.
- On Restart now it records the relaunch and closes the window through the
  ordinary shutdown, exactly as the zoom restart does (gui/restart.py, D39b).
  For Windows the relaunch is the installer in update mode, which restarts
  the app when it is done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject

from anki_miner.gui import restart
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome
from anki_miner.gui.widgets.base.task_publisher import TaskPublisherMixin
from anki_miner.services.app_updater import StagedUpdate, relaunch_command
from anki_miner.services.update_checker import UpdateInfo
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.main_window import MainWindow
    from anki_miner.gui.widgets.update_banner import UpdateBanner


class AppUpdateController(TaskPublisherMixin, QObject):
    """Drive one :class:`UpdateBanner` through an in-place update.

    Publishes through :class:`TaskPublisherMixin` like every screen run, so
    the status strip and the mini job monitor show (and can cancel) the
    download without knowing it is not a screen.
    """

    #: One update download at a time.
    TASK_ID = "app.update"
    #: Where the update setting lives (the update-check capability's target).
    TASK_OWNER = CapabilityTarget("settings", "ui")

    def __init__(self, window: MainWindow, banner: UpdateBanner) -> None:
        super().__init__(window)
        self._window = window
        self._banner = banner
        self._version = ""
        self._result_seen = False
        self._staged: StagedUpdate | None = None
        banner.install_requested.connect(self.start)
        banner.cancel_requested.connect(self.cancel)
        banner.restart_requested.connect(self.restart)
        self.bind_task_registry(window.task_registry)

    # ------------------------------------------------------------------ download

    def start(self, info: object) -> None:
        """Begin downloading *info*'s asset, unless a download already runs."""
        if not isinstance(info, UpdateInfo):
            return
        worker = self._window.background_tasks.start_app_update(
            info,
            on_progress=self._on_progress,
            on_result=self._on_result,
            on_finished=self._on_finished,
        )
        if worker is None:
            return
        self._version = info.version
        self._result_seen = False
        self._staged = None
        self._publish_task_start(self.tr("Anki Miner update"))
        self._banner.show_downloading()

    def cancel(self) -> None:
        """Ask the running download to stop. One verb, no confirmation (D22)."""
        worker = self._window.background_tasks.app_update_worker
        if worker is None:
            return
        self._publish_task_cancelling()
        worker.cancel()

    def _cancel_published_task(self) -> None:
        """A cancel asked for from the job monitor goes through the same verb."""
        self.cancel()

    def _on_progress(self, downloaded: int, total: int) -> None:
        self._publish_task_count(
            downloaded,
            total or None,
            tr_format(self.tr("Downloading Anki Miner v%1"), self._version),
        )

    def _on_result(self, result: object) -> None:
        self._result_seen = True
        if isinstance(result, StagedUpdate):
            self._staged = result
            self._publish_task_finish(TaskOutcome.SUCCEEDED)
            self._banner.show_ready()
        else:
            self._publish_task_finish(TaskOutcome.FAILED)
            self._banner.show_failed()

    def _on_finished(self) -> None:
        # A cancelled worker emits no result; its native finish is the only word.
        if not self._result_seen:
            self._publish_task_finish(TaskOutcome.CANCELLED)
            self._banner.show_offer()

    # ------------------------------------------------------------------ restart

    def restart(self) -> None:
        """Close and relaunch into the staged update, unless other work is running.

        Closing cancels every run (MainWindow.closeEvent), so a mining run in
        flight refuses the restart instead. The AppImage is already replaced on
        disk, so an ordinary quit later also finishes that update.
        """
        staged = self._staged
        if staged is None:
            return
        if self._window.task_registry.running():
            self._window.status_bar.set_operation(
                self.tr("Finish or cancel the running task, then restart to update."), "warning"
            )
            return
        if staged.target == "windows-frozen" and not staged.path.is_file():
            # TEMP cleanup or an antivirus can remove the staged installer while
            # the banner waits. Closing now would leave no app and no installer,
            # so fall back to the browser download instead.
            self._staged = None
            self._banner.show_failed()
            return
        command = relaunch_command(staged)
        if command is None:
            if restart.resolve_relaunch_target() is None:
                self._window.status_bar.set_operation(
                    self.tr("Close and reopen Anki Miner to finish updating."), "warning"
                )
                return
            restart.request_restart()
        else:
            restart.request_restart(*command)
        # A deferred close (workers outliving the join grace) still quits; only a
        # genuine refusal forgets the intent. Same rule as the zoom restart.
        if not self._window.close() and not self._window.is_shutting_down():
            restart.clear_restart_request()
