"""Shared "yt-dlp is missing -> offer a one-click download" state.

Two screens cannot do their job without yt-dlp — Utilities -> Download and
Video -> YouTube — and the app ships none: it arrives as an in-app download into
``ANKI_MINER_HOME/bin/`` (``services/ytdlp_updater.py``). Both screens therefore
say the same three things (missing, downloading, nothing) and offer the same
repair, so the state machine lives here once.

Mixed in BEFORE the Qt base class, like
:class:`~anki_miner.gui.widgets.base.screen_issue_banner.ScreenIssueHost`, which
it extends so the banner calls resolve. It defines no ``__init__``.

Two things stay with the host:

* :meth:`YtdlpAvailabilityMixin._refresh_ytdlp_state` dispatches that screen's
  OFF-THREAD availability probe. Resolving re-hashes a ~35 MB binary, so it must
  never run on the GUI thread, and the two hosts already have different
  dispatchers.
* :meth:`YtdlpAvailabilityMixin._emit_ytdlp_download_requested` emits that
  screen's own ``ytdlp_download_requested``. A ``pyqtSignal`` cannot live on a
  non-QObject mixin; ``gui/app.py`` wires both to
  ``BackgroundTaskController.start_ytdlp_update(force=True)``.

The copy is the host's too (:class:`YtdlpStrings`), so each literal stays in its
own tab's tr-context — the same rule as ``_ToolTabStrings`` and
``_QueueRunStrings``.
"""

from __future__ import annotations

from dataclasses import dataclass

from anki_miner.gui.widgets.base.screen_issue_banner import ScreenIssue, ScreenIssueHost
from anki_miner.utils.ytdlp_resolver import ytdlp_available

#: Stable id carried by the banner's repair button. Never a translated string.
YTDLP_DOWNLOAD_ACTION = "ytdlp.download"


@dataclass(frozen=True)
class YtdlpStrings:
    """One screen's yt-dlp copy, built in that screen's own tr-context."""

    missing: str
    download_action: str
    downloading: str


class YtdlpAvailabilityMixin(ScreenIssueHost):
    """Track whether yt-dlp is usable, and offer to fetch it when it is not."""

    _ytdlp_strings: YtdlpStrings | None = None
    _ytdlp_is_available: bool = False
    #: False until a probe has actually answered. A screen that has never asked
    #: must not refuse a run on the strength of an unset default.
    _ytdlp_probed: bool = False
    _ytdlp_download_pending: bool = False
    #: The updater's message from a download THIS screen asked for and did not
    #: get. Shown behind the banner's Details, cleared by the next attempt.
    _ytdlp_failure_details: str = ""

    # ------------------------------------------------------------------
    # Host hooks
    # ------------------------------------------------------------------

    def _refresh_ytdlp_state(self) -> None:
        """Dispatch this screen's off-thread availability probe."""
        raise NotImplementedError

    def _emit_ytdlp_download_requested(self) -> None:
        """Emit this screen's ``ytdlp_download_requested`` signal."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_ytdlp_available(config) -> bool:
        """Probe whether a usable yt-dlp is reachable for *config*.

        Runs the resolver, managed-binary re-hash included, so it is called only
        from a worker thread. Readers use the cached bool via :meth:`_ytdlp_ready`.
        """
        return ytdlp_available(config)

    def _ytdlp_ready(self) -> bool:
        """The cached availability. Never resolves."""
        return self._ytdlp_is_available

    def _ytdlp_known_missing(self) -> bool:
        """True only when a probe has ANSWERED that yt-dlp is unusable.

        A screen refuses a run on this, not on :meth:`_ytdlp_ready`: the cached
        default is "not available", and a screen whose probe has not landed yet
        must not turn that into a refusal.
        """
        return self._ytdlp_probed and not self._ytdlp_is_available

    def _apply_probe_result(self, result: object) -> None:
        """Adopt a probe outcome and re-render the banner."""
        self._ytdlp_is_available = bool(result)
        self._ytdlp_probed = True
        self._render_ytdlp_issue()

    def notify_ytdlp_update_result(self, result: object) -> None:
        """A yt-dlp update finished — wherever it was started — so re-probe.

        The verdict is re-derived from disk rather than read off ``result``: a
        download started from Settings -> YouTube must clear this screen's banner
        too, and a failed or deferred one must leave it standing.

        A download THIS screen asked for and did not get says so, with the
        updater's own message behind Details. Without it the click is silent
        here: the failure dialog belongs to Settings and is gated on its own
        button (``SettingsTab.set_ytdlp_status_from_result``). D24 — never a
        modal.
        """
        asked = self._ytdlp_download_pending
        self._ytdlp_download_pending = False
        if asked and getattr(result, "action", "") in ("failed", "unavailable", "deferred"):
            self._ytdlp_failure_details = str(getattr(result, "message", "") or "")
        self._refresh_ytdlp_state()

    def _on_ytdlp_download_clicked(self) -> None:
        """Start the download and show it running instead of offering it again."""
        self._ytdlp_download_pending = True
        self._ytdlp_failure_details = ""
        self._render_ytdlp_issue()
        self._emit_ytdlp_download_requested()

    def _showing_ytdlp_issue(self) -> bool:
        """True when the banner is CURRENTLY showing this state machine's issue.

        Not a flag: the banner is one slot per screen. A run failure raised after
        ours supersedes it, and Dismiss clears it without telling the host — in
        both cases the issue on screen is no longer ours to clear.
        """
        banner = self.issue_banner()
        current = None if banner is None else banner.current_issue()
        return current is not None and current.action_id == YTDLP_DOWNLOAD_ACTION

    def _render_ytdlp_issue(self) -> None:
        """Show, replace or clear this screen's yt-dlp banner."""
        strings = self._ytdlp_strings
        if strings is None:
            return
        if self._ytdlp_is_available:
            # Only ever clear our own: a run failure's issue is not ours to drop.
            if self._showing_ytdlp_issue():
                self.clear_screen_issue()
            self._ytdlp_failure_details = ""
            return
        if self._ytdlp_download_pending:
            self.show_screen_issue(ScreenIssue(summary=strings.downloading))
            return
        self.show_screen_issue(
            ScreenIssue(
                summary=strings.missing,
                details=self._ytdlp_failure_details,
                action_id=YTDLP_DOWNLOAD_ACTION,
                action_text=strings.download_action,
            ),
            action=self._on_ytdlp_download_clicked,
        )
