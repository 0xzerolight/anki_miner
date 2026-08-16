"""Persistent, screen-scoped reporting for recoverable problems (decision D24).

A modal-first error policy is structurally incompatible with unattended batch
work: a recoverable problem on episode 3 halts the run and is still waiting when
the user comes back hours later. So a recoverable problem is reported *on the
screen it happened on*, in a banner that stays until the problem is fixed.

Three rules shape the design.

* **The repair lives inside the banner.** "ffmpeg and ffprobe weren't found"
  followed by an *Open Media Settings* button is a repair; the same sentence in
  a box with an OK button is a dead end. A banner without a plausible repair
  simply omits the button — it does not invent one.
* **The raw diagnostic is behind Details.** Exception text, filesystem paths and
  URLs are what an issue report needs and what a reader does not. They go in the
  collapsed half; the summary stays a sentence.
* **Nothing here dismisses itself.** No timer, no dismiss-on-the-next-click. A
  banner that clears itself eight seconds after episode 14 failed at 23:47 is a
  failure the user can never find at 08:00, which is the exact thing D24 removed.
  It goes away for one of two reasons only: :meth:`ScreenIssueBanner.clear_issue`,
  which a caller invokes when the thing that failed has since succeeded or when a
  fresh attempt supersedes the last one, or the Dismiss button, which is the user
  saying they have read it. Acknowledgement is not concealment.

A modal remains correct for exactly two things: a destructive confirmation
(the user is about to lose data) and a whole-app blocker (a second instance, an
unhandled exception). ``tests/unit/test_message_box_policy.py`` is the ledger
that keeps it that way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING

#: Object name the stylesheet and the theme tests address the banner by.
BANNER_OBJECT_NAME = "screen-issue-banner"


@dataclass(frozen=True)
class ScreenIssue:
    """One recoverable problem, as the user should meet it.

    Args:
        summary: The whole sentence the user reads. No exception text, no
            paths, no URLs — those belong in ``details``.
        details: The raw diagnostic, shown only when Details is expanded.
        action_id: Stable id emitted by :attr:`ScreenIssueBanner.action_requested`
            when the repair button is pressed. Never a translated string.
        action_text: Translated label for the repair button. Empty means the
            problem has no repair the app can offer, and no button is shown.
    """

    summary: str
    details: str = ""
    action_id: str = ""
    action_text: str = ""

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("a screen issue needs a summary the user can read")


class ScreenIssueBanner(QFrame):
    """The persistent banner itself. One per screen; reused, never rebuilt.

    Signals:
        action_requested: Emitted with :attr:`ScreenIssue.action_id` when the
            repair button is pressed.
        dismissed: Emitted when the user presses Dismiss. The banner has already
            cleared itself by then; this is for a host that wants to know the
            issue was read rather than repaired.
    """

    action_requested = pyqtSignal(str)
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(BANNER_OBJECT_NAME)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        # Imported here, not at module scope: `widgets.enhanced` reaches back
        # into `widgets.base`, so a top-level import would close the cycle while
        # this package is still initialising (same reason as EnhancedDialog).
        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        self._issue: ScreenIssue | None = None
        self._action: Callable[[], None] | None = None

        layout = QVBoxLayout()
        layout.setContentsMargins(SPACING.xs, SPACING.xxs, SPACING.xs, SPACING.xxs)
        layout.setSpacing(SPACING.xxs)

        row = QHBoxLayout()
        row.setSpacing(SPACING.xs)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("screen-issue-summary")
        self.summary_label.setWordWrap(True)
        # Plain text: a summary is a sentence, and a path that happens to look
        # like markup must never be interpreted as rich text.
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.summary_label, 1)

        # Quiet roles on purpose: under D41 the accent marks the screen's one
        # task action, and a banner must not take it away from the work.
        self.details_button = ModernButton(self.tr("Details"), variant="ghost")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._on_details_toggled)
        row.addWidget(self.details_button)

        self.action_button = ModernButton("", variant="secondary")
        self.action_button.clicked.connect(self._on_action_clicked)
        row.addWidget(self.action_button)

        # Last on the row and always present, including on an issue that carries
        # a repair: a user who has read the sentence and decided to live with it
        # needs a way out, and before this the only exit was a `clear_issue` call
        # that most of the 96 reporting sites had no success path to make.
        # Glyph rather than a word, and the accessible name carries the meaning
        # (same shape as `update_banner`'s dismiss). `square` because the global
        # button padding is measured for a word and would otherwise stretch one
        # glyph to a text button's width; no `setObjectName` here, because on
        # `ModernButton` the object name *is* the variant hook in `common.qss`.
        self.dismiss_button = ModernButton("✕", variant="ghost", square=True)
        self.dismiss_button.setAccessibleName(self.tr("Dismiss"))
        self.dismiss_button.setToolTip(self.tr("Dismiss"))
        self.dismiss_button.clicked.connect(self._on_dismiss_clicked)
        row.addWidget(self.dismiss_button)

        layout.addLayout(row)

        self.details_label = QLabel()
        self.details_label.setObjectName("screen-issue-details")
        self.details_label.setWordWrap(True)
        self.details_label.setTextFormat(Qt.TextFormat.PlainText)
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details_label.hide()
        layout.addWidget(self.details_label)

        self.setLayout(layout)
        self.hide()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_issue(self) -> ScreenIssue | None:
        """The issue on display, or ``None`` when the banner is clear."""
        return self._issue

    def show_issue(self, issue: ScreenIssue, *, action: Callable[[], None] | None = None) -> None:
        """Display ``issue``, replacing whatever was shown before.

        Details always start collapsed: the previous issue's expansion says
        nothing about whether this one needs reading.
        """
        self._issue = issue
        self._action = action
        self.summary_label.setText(issue.summary)
        self.details_label.setText(issue.details)
        self.details_button.setChecked(False)
        self.details_button.setVisible(bool(issue.details))
        self.details_label.hide()
        self.action_button.setText(issue.action_text)
        self.action_button.setVisible(bool(issue.action_text))
        self.show()

    def clear_issue(self) -> None:
        """Hide the banner. Callers do this when the failure has since succeeded."""
        self._issue = None
        self._action = None
        self.details_button.setChecked(False)
        self.details_label.hide()
        self.hide()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_details_toggled(self, expanded: bool) -> None:
        self.details_label.setVisible(expanded and bool(self.details_label.text()))

    def _on_action_clicked(self) -> None:
        issue = self._issue
        if issue is None:
            return
        if self._action is not None:
            self._action()
        self.action_requested.emit(issue.action_id)

    def _on_dismiss_clicked(self) -> None:
        """Clear first, then announce: a slot on ``dismissed`` reads a clear banner."""
        if self._issue is None:
            return
        self.clear_issue()
        self.dismissed.emit()


class ScreenIssueHost:
    """Mixed into a screen that can report recoverable problems.

    Mixed in *before* the Qt base class (``class AnalyticsTab(ScreenIssueHost,
    QWidget)``), like :class:`~anki_miner.gui.widgets.base.setting_anchor.SettingAnchorHost`.
    It defines no ``__init__`` and allocates on first use, so ``super().__init__``
    still lands on the Qt base.

    A host that never calls :meth:`install_issue_banner` is inert rather than
    broken: reporting is a no-op. That matters because the screens are migrated
    one at a time, and a controller that reports into a not-yet-migrated screen
    must not crash the app.
    """

    def install_issue_banner(self, layout: QBoxLayout, index: int = 0) -> ScreenIssueBanner:
        """Create this screen's banner and insert it at ``index`` of ``layout``.

        Index 0 by default: a problem with the screen belongs above the screen.
        """
        banner = ScreenIssueBanner()
        layout.insertWidget(index, banner)
        self._screen_issue_banner = banner
        return banner

    def issue_banner(self) -> ScreenIssueBanner | None:
        """This screen's banner, or ``None`` if it never installed one."""
        return getattr(self, "_screen_issue_banner", None)

    def show_screen_issue(self, issue: ScreenIssue, *, action: Callable[[], None] | None = None) -> None:
        """Report ``issue`` on this screen. No-op if no banner is installed."""
        banner = self.issue_banner()
        if banner is not None:
            banner.show_issue(issue, action=action)

    def clear_screen_issue(self) -> None:
        """Clear this screen's banner. No-op if no banner is installed."""
        banner = self.issue_banner()
        if banner is not None:
            banner.clear_issue()


def _find_host(origin: QWidget | None) -> ScreenIssueHost | None:
    """Walk up from ``origin`` to the nearest ancestor with an installed banner.

    Nearest, not outermost: a dialog that hosts its own banner keeps its own
    failures, and only a screen with a banner actually installed can take them.

    The walk is typed at every step, not just at entry. Controllers hand this
    whatever parent they were constructed with, and a test double answers
    ``parentWidget()`` with another double forever — an untyped loop hangs the
    process rather than failing. A non-widget simply has no screen.
    """
    widget: object = origin
    while isinstance(widget, QWidget):
        if isinstance(widget, ScreenIssueHost) and widget.issue_banner() is not None:
            return widget
        widget = widget.parentWidget()
    return None


def report_screen_issue(
    origin: QWidget | None,
    issue: ScreenIssue,
    *,
    action: Callable[[], None] | None = None,
) -> bool:
    """Report ``issue`` on the screen that owns ``origin``.

    The escape hatch for controllers, which hold a parent widget rather than
    being one. Returns whether a host took the issue; a caller that must not
    lose the failure logs it either way.
    """
    host = _find_host(origin)
    if host is None:
        return False
    host.show_screen_issue(issue, action=action)
    return True


def clear_reported_issue(origin: QWidget | None) -> bool:
    """Clear the banner :func:`report_screen_issue` would have written to."""
    host = _find_host(origin)
    if host is None:
        return False
    host.clear_screen_issue()
    return True
