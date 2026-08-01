"""The pinned action bar and its Activity drawer (D6-B).

Every workflow screen used to put its inputs, its run button, its progress and a
permanently-expanded activity log into one scrolling page. At the owner's stated
minimum window -- 1024x768, German at 150% text -- seven screens ended up with
their run button at or below the window edge, and the log reserved roughly 200px
whether or not anything had been logged.

This module supplies the two pieces that fix it:

* :class:`WorkflowActionBar` -- a slim strip that sits *outside* the page scroll
  and therefore never leaves the screen. It carries the primary action, whatever
  quiet actions sit beside it (Cancel while running), the current stage, a thin
  progress bar, the elapsed clock and a checkable ``Activity`` control.
* :func:`install_workflow_shell` -- the page frame that puts the scroll, the
  Activity drawer and the bar in that order, with the bar sharing the scrolled
  column's width cap and centre line so the run button lines up with the form
  above it.

Two properties are load-bearing:

* **The bar renders, it does not compute.** Progress comes from
  :class:`~anki_miner.gui.controllers.task_registry.TaskSnapshot` and nowhere
  else, bound to one exact ``task_id``. ``TaskSnapshot.fraction`` is ``None``
  whenever no honest denominator exists, and the bar answers that with an
  indeterminate bar rather than a number it made up.
* **Activity opens itself exactly once per attempt.** Info and success never
  open it; the first warning or error of an attempt does. Closing it again is
  respected for the rest of that attempt, and :meth:`WorkflowActionBar.begin_attempt`
  re-arms it for the next one -- so a hidden log can never swallow a problem, and
  it also cannot keep springing open at someone who has decided to ignore it.

The bar owns no worker, no thread and no cancellation. It is handed the screen's
existing button objects and never builds a second copy of one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.utils.progress_telemetry import format_clock
from anki_miner.gui.widgets.base.eliding_label import ElidingLabel
from anki_miner.gui.widgets.base.sizing import PageWidth, configure_scrolled_page, page_width_cap
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSnapshot

#: Object name for the bar itself, so QSS and tests have one stable handle.
ACTION_BAR_OBJECT_NAME = "workflow-action-bar"
#: Object name for the bar's thin progress bar.
ACTION_BAR_PROGRESS_OBJECT_NAME = "workflow-progress"
#: Object name for the drawer that hosts the Activity log outside the scroll.
ACTIVITY_DRAWER_OBJECT_NAME = "activity-drawer"

#: Height of the thin bar, in pixels. It is a rule under the page rather than a
#: gauge to read a number off -- the counts and the clock are text beside it.
_PROGRESS_HEIGHT = SPACING.xxs

#: How much of the shell the Activity drawer takes when it first opens, as a
#: proportion of the shell's height. The page above it stays legible.
_DRAWER_OPEN_SHARE = 0.4

#: Levels that force the drawer open. Info and success never do: a run that is
#: going to plan must not keep stealing the page it is running on.
_PROBLEM_LEVELS = frozenset({"WARNING", "ERROR"})


class WorkflowActionBar(QWidget):
    """The always-visible foot of a workflow page. See the module docstring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Build an idle bar with no actions and no Activity control.

        Args:
            parent: Optional parent widget.
        """
        super().__init__(parent)
        self._registry: TaskRegistry | None = None
        self._task_id: str | None = None
        self._run_token: int | None = None
        self._drawer: QWidget | None = None
        self._splitter: QSplitter | None = None
        self._auto_open_armed = True
        # Set once the splitter has been given an opening split, so a reopen
        # keeps whatever height the user dragged the drawer to.
        self._drawer_sized = False
        self._secondary: list[QAbstractButton] = []
        self._primary: QAbstractButton | None = None
        self._setup_ui()
        self._render(None)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def set_actions(
        self,
        primary: QAbstractButton | None,
        secondary: tuple[QAbstractButton, ...] = (),
    ) -> None:
        """Move ``primary`` and ``secondary`` into the bar, in that reading order.

        The buttons are the screen's own objects: they are reparented, never
        rebuilt, so every connection, tooltip and shortcut they already carry
        keeps working. Calling this again re-orders the same objects, which is
        how Backfill promotes Apply once a preview exists without ever growing a
        second Apply.

        Args:
            primary: The screen's task action, placed last (right) where the eye
                finishes. ``None`` leaves the slot empty.
            secondary: Quieter actions shown before it, in order.
        """
        for button in (*self._secondary, self._primary):
            if button is not None:
                self._action_layout.removeWidget(button)
        self._secondary = list(secondary)
        self._primary = primary
        for button in self._secondary:
            self._action_layout.addWidget(button)
        if primary is not None:
            self._action_layout.addWidget(primary)
        self._remeasure()

    def _remeasure(self) -> None:
        """Re-cost the bar's own height after its row gained or lost a widget.

        A Qt box layout caches its size hint and only recomputes it when it is
        marked dirty -- and adding an item to a *nested* layout marks only that
        nested layout. The row and the column above it went on answering with
        the height they cached at construction, when the bar held nothing but an
        empty stage label. Anything that asked the bar how tall it was before it
        had ever been on screen -- sizing a window, ``adjustSize`` on a page --
        got that empty-bar answer, laid out short, and jumped as soon as the
        first real layout pass corrected it. Marking the whole chain dirty is
        what makes the answer true straight away.
        """
        self._action_layout.invalidate()
        self._row.invalidate()
        layout = self.layout()
        if layout is not None:
            layout.invalidate()
        self.updateGeometry()

    def current_primary(self) -> QAbstractButton | None:
        """The button the bar is showing as this screen's primary action.

        Read rather than remembered, so a screen that swaps its primary with its
        stage is answered from whatever it swapped to.
        """
        return self._primary

    def trigger_primary(self) -> None:
        """Press the current primary action, if it is there to be pressed.

        The button object, never the slot behind it. Going through ``click()``
        is what makes ``Ctrl+Enter`` mean exactly what the mouse means: Card
        Backfill's shortcut follows Scan to Apply as the stage moves, the
        Reading tabs' follows whichever verb their mode is showing, and a run
        already in flight -- which disables the button -- cannot be started a
        second time from the keyboard.

        ``isHidden`` rather than ``isVisible``: this asks about the button's own
        state, and a screen behind another tab has to answer it too.
        """
        primary = self._primary
        if primary is None or primary.isHidden() or not primary.isEnabled():
            return
        primary.click()

    # ------------------------------------------------------------------
    # Activity drawer
    # ------------------------------------------------------------------

    def attach_activity(self, log: QWidget | None, drawer: QWidget | None = None) -> None:
        """Put ``log`` behind the Activity control, or hide the control entirely.

        ``None`` is the honest answer for a screen with no activity log (Card
        Backfill): the control disappears rather than opening onto an empty
        panel that implies a log exists somewhere.

        Args:
            log: The screen's existing ``LogWidget``. Its ``problem_logged``
                signal, if it has one, drives the one-shot auto-open.
            drawer: The container whose visibility the control toggles. Defaults
                to ``log`` itself; :func:`install_workflow_shell` passes the
                splitter pane it wrapped the log in.
        """
        self._drawer = drawer if drawer is not None else log
        self.activity_button.setVisible(log is not None)
        self._remeasure()
        if log is None:
            return
        problem_logged = getattr(log, "problem_logged", None)
        if problem_logged is not None:
            problem_logged.connect(self._on_problem_logged)
        self.set_activity_open(False)

    def set_activity_open(self, is_open: bool) -> None:
        """Show or hide the Activity drawer, keeping the control in step."""
        if self._drawer is None:
            return
        self._drawer.setVisible(is_open)
        if self.activity_button.isChecked() != is_open:
            # Signals blocked: this is the state changing, not a click, and the
            # toggled handler would only set what is already being set.
            self.activity_button.blockSignals(True)
            self.activity_button.setChecked(is_open)
            self.activity_button.blockSignals(False)
        if is_open:
            self._give_drawer_room()

    def is_activity_open(self) -> bool:
        """Whether the Activity drawer is open.

        ``isHidden`` rather than ``isVisible``: this is the drawer's own state,
        and a page that has not been shown yet -- a freshly built tab behind
        another tab -- must still be able to say whether its drawer is open.
        """
        return self._drawer is not None and not self._drawer.isHidden()

    def begin_attempt(self) -> None:
        """Re-arm the one-shot auto-open for a fresh attempt at the action.

        Called before validation, not after launch: an attempt refused by a
        missing file logs its warning without a worker ever starting, and that
        warning is exactly the kind the drawer exists to surface.

        Deliberately does not close a drawer the user opened -- re-arming is
        about the next problem, not about tidying the page.
        """
        self._auto_open_armed = True

    def _on_problem_logged(self, level: str, _message: str) -> None:
        """Open the drawer for the first problem of an attempt, once."""
        if level not in _PROBLEM_LEVELS or not self._auto_open_armed:
            return
        self._auto_open_armed = False
        self.set_activity_open(True)

    def _on_activity_toggled(self, checked: bool) -> None:
        """A click on Activity is the user's decision and outranks the arming."""
        self._auto_open_armed = False
        self.set_activity_open(checked)

    def set_resize_host(self, splitter: QSplitter) -> None:
        """Name the splitter whose lower pane the drawer occupies.

        Used only to give the drawer a readable height the first time it opens;
        the bar never reads a size back out of it.
        """
        self._splitter = splitter

    def _give_drawer_room(self) -> None:
        """Open the splitter pane to a readable share of the page.

        Only on the first open: after that the split is whatever the user
        dragged it to, and resetting it would undo their sizing every time they
        closed and reopened the drawer.
        """
        splitter = self._splitter
        if splitter is None or self._drawer_sized:
            return
        height = splitter.height()
        if height <= 0:
            return
        drawer = int(height * _DRAWER_OPEN_SHARE)
        splitter.setSizes([height - drawer, drawer])
        self._drawer_sized = True

    # ------------------------------------------------------------------
    # Progress
    # ------------------------------------------------------------------

    def bind_task(self, registry: TaskRegistry, task_id: str) -> None:
        """Render whatever ``task_id`` is doing, for as long as this page lives.

        Bound by id rather than by ``(id, run_token)`` because the bar belongs to
        the screen, not to one run: the next run of the same screen is still the
        thing this bar is for. A snapshot carrying an *older* token is a trailing
        signal from a superseded run and is dropped.

        Args:
            registry: The application's task registry.
            task_id: The exact task this screen publishes.
        """
        if self._registry is not registry:
            if self._registry is not None:
                self._registry.snapshot_changed.disconnect(self._on_snapshot_changed)
            registry.snapshot_changed.connect(self._on_snapshot_changed)
            self._registry = registry
        self._task_id = task_id
        self._run_token = None
        self._refresh()

    def unbind_task(self) -> None:
        """Stop rendering any task and collapse the progress group."""
        if self._registry is not None:
            self._registry.snapshot_changed.disconnect(self._on_snapshot_changed)
            self._registry = None
        self._task_id = None
        self._run_token = None
        self._refresh()

    def _on_snapshot_changed(self, task_id: str) -> None:
        if task_id != self._task_id:
            return
        self._refresh()

    def _refresh(self) -> None:
        self._render(self._bound_snapshot())

    def _bound_snapshot(self) -> TaskSnapshot | None:
        """The bound task's snapshot, ignoring one belonging to an older run."""
        if self._registry is None or self._task_id is None:
            return None
        snapshot = self._registry.snapshot(self._task_id)
        if snapshot is None:
            return None
        if self._run_token is not None and snapshot.run_token < self._run_token:
            return None
        self._run_token = snapshot.run_token
        return snapshot

    def _render(self, snapshot: TaskSnapshot | None) -> None:
        """Repaint the stage, bar and clock, or collapse them when idle."""
        if snapshot is None or not snapshot.is_running:
            # Emptied, not hidden: the stage label is the row's only elastic
            # item, and removing it from the layout lets the buttons stretch to
            # fill the page instead of sitting at their own width.
            self.stage_label.setText("")
            self.progress_bar.hide()
            self.elapsed_label.hide()
            return

        self.stage_label.setText(self._stage_text(snapshot))
        self.elapsed_label.setText(format_clock(snapshot.elapsed_s))
        self.elapsed_label.show()

        fraction = snapshot.fraction
        if fraction is None:
            # No denominator the run can prove. An indeterminate bar says "still
            # going" without claiming a position it does not have (D18).
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(int(fraction * 100))
        self.progress_bar.show()

    def _stage_text(self, snapshot: TaskSnapshot) -> str:
        """Name the phase the run is in, using only what the snapshot knows."""
        if snapshot.cancelling:
            return self.tr("Cancelling…")
        if snapshot.stage_name and snapshot.stage_index is not None and snapshot.stage_total:
            return tr_format(
                self.tr("%1 (%2 of %3)"),
                snapshot.stage_name,
                snapshot.stage_index,
                snapshot.stage_total,
            )
        if snapshot.stage_name:
            return snapshot.stage_name
        if snapshot.detail:
            return snapshot.detail
        return snapshot.title

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        """A thin rule over one row: stage, clock, Activity, then the actions."""
        self.setObjectName(ACTION_BAR_OBJECT_NAME)
        # A plain QWidget draws neither the stylesheet's background nor its
        # border without this; the divider line above the bar is the only thing
        # separating it from the page it pins, so it has to actually paint.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName(ACTION_BAR_PROGRESS_OBJECT_NAME)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(_PROGRESS_HEIGHT)
        # The rule's strip belongs to the bar whether or not a run is drawing in
        # it. Letting the hidden bar give its height back would move the whole
        # pinned page the moment anyone pressed the primary action, and move it
        # again when the run ended -- a bar that is pinned so the run button
        # stays put (D6-B) cannot be the thing that shifts it. Hidden it still
        # does not paint; it just keeps its four pixels.
        progress_policy = self.progress_bar.sizePolicy()
        progress_policy.setRetainSizeWhenHidden(True)
        self.progress_bar.setSizePolicy(progress_policy)
        outer.addWidget(self.progress_bar)

        row = QHBoxLayout()
        row.setContentsMargins(SPACING.sm, SPACING.xs, SPACING.sm, SPACING.xs)
        row.setSpacing(SPACING.xs)

        self.stage_label = ElidingLabel()
        self.stage_label.setObjectName("workflow-stage")
        # A label costs itself out from the glyphs it is holding, so an empty
        # stage label asks for a pixel less than the same label naming a stage --
        # and having once been asked for the taller box it never asks for the
        # shorter one again. Reserving the filled line up front keeps the bar's
        # height a property of its actions rather than of whether a run has ever
        # started on this screen.
        self.stage_label.setText("X")
        self.stage_label.setMinimumHeight(self.stage_label.sizeHint().height())
        self.stage_label.setText("")
        row.addWidget(self.stage_label, 1)

        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("progress-stats")
        row.addWidget(self.elapsed_label)

        self.activity_button = QPushButton(self.tr("Activity"))
        self.activity_button.setObjectName("ghost")
        self.activity_button.setCheckable(True)
        self.activity_button.setToolTip(self.tr("Show the run log for this screen."))
        self.activity_button.toggled.connect(self._on_activity_toggled)
        self.activity_button.hide()
        row.addWidget(self.activity_button)

        self._action_layout = QHBoxLayout()
        self._action_layout.setContentsMargins(0, 0, 0, 0)
        self._action_layout.setSpacing(SPACING.xs)
        row.addLayout(self._action_layout)

        self._row = row
        outer.addLayout(row)
        self.setLayout(outer)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        font = self.elapsed_label.font()
        font.setPixelSize(FONT_SIZES.caption)
        self.elapsed_label.setFont(font)


def _column_has_vertical_absorber(column: QBoxLayout) -> bool:
    """Whether anything in ``column`` can take surplus height.

    ``stretch(i)`` alone is not the answer. ``addStretch()`` records its pull in
    Qt's private ``magic`` flag and reports a stretch of *zero*, and a card whose
    own layout expands -- a queue list, a paste box -- is expansive through its
    item even though the card's own policy is only ``Minimum``. Miss either and
    the guard below adds a second stretch that competes with the real grower,
    which drags that list back towards its size hint.

    Args:
        column: The scrolled page's content column.

    Returns:
        ``True`` if some item already takes surplus vertical space.
    """
    for index in range(column.count()):
        if column.stretch(index) > 0:
            return True
        item = column.itemAt(index)
        if item is not None and item.expandingDirections() & Qt.Orientation.Vertical:
            return True
    return False


def install_workflow_shell(
    layout: QBoxLayout,
    scroll: QScrollArea,
    content: QWidget,
    kind: PageWidth,
    *,
    log: QWidget | None = None,
) -> WorkflowActionBar:
    """Frame a page as *scroll over drawer over bar* and return the bar.

    The bar and the Activity drawer are siblings of the scroll area, never
    children of it, which is the whole point: the run button and the log stay
    put while the form above them scrolls.

    Both are capped and centred on the same column as the scrolled content, so
    the primary action lines up with the fields it acts on instead of drifting
    to the window edge on a wide monitor.

    The drawer is a splitter pane rather than a fixed panel, so the log can be
    dragged to whatever height the problem needs and dragged back.

    Args:
        layout: The page's top-level layout. The shell is appended to it.
        scroll: The page's scroll area, not yet given its widget.
        content: The column of cards, fully populated.
        kind: The page's declared ``PAGE_WIDTH``.
        log: The screen's ``LogWidget``, moved out of ``content`` into the
            drawer. ``None`` hides the Activity control. Moving it deletes its
            layout item, so if that leaves the column with nothing to absorb
            surplus height, a trailing stretch is appended.

    Returns:
        The installed :class:`WorkflowActionBar`.
    """
    configure_scrolled_page(scroll, content, kind)

    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(scroll)

    bar = WorkflowActionBar()
    bar.set_resize_host(splitter)

    drawer: QWidget | None = None
    if log is not None:
        drawer = capped_page_column(log, kind)
        drawer.setObjectName(ACTIVITY_DRAWER_OBJECT_NAME)
        splitter.addWidget(drawer)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

    # The log was the only expanding item in a pure form's column, and moving it
    # into the drawer just deleted that item. `setWidgetResizable` still stretches
    # the column to the viewport height, so with nothing left to take the surplus
    # Qt deals it to every item that merely may grow -- the section headers and
    # the cards, which centre their content and read as random empty bands that
    # "fix themselves" the moment Activity is opened and the viewport shrinks. A
    # trailing stretch gives the surplus somewhere to go. Screens that already
    # have an absorber -- a queue list, a table, their own addStretch -- are left
    # alone: a second one would compete with the real grower.
    column = content.layout()
    if isinstance(column, QBoxLayout) and not _column_has_vertical_absorber(column):
        column.addStretch()

    layout.addWidget(splitter, 1)
    # Activity is attached before the bar is capped: the cap is never allowed
    # below the bar's own minimum, and a bar that has not been told whether it
    # has an Activity control yet reports a minimum too small for that guard to
    # be worth anything.
    bar.attach_activity(log, drawer)
    layout.addWidget(capped_page_column(bar, kind))
    return bar


def capped_page_column(child: QWidget, kind: PageWidth) -> QWidget:
    """Wrap ``child`` in a full-width host that centres it on the page column.

    The host spans the window; the child inside it stops at the same cap
    :func:`configure_scrolled_page` gives the scrolled content, so everything on
    the page shares one centre line and the bar's own rule runs exactly the
    width of the column it closes. Public because anything else a page pins
    below its scroll -- Card Backfill's run status, for one -- has to line up
    with the same column.

    Args:
        child: The widget to centre and cap.
        kind: The page's declared ``PAGE_WIDTH``.

    Returns:
        The host widget to add to the page's top-level layout.
    """
    host = QWidget()
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    child.ensurePolished()
    # Never below the child's own minimum: Qt applies a maximum after a minimum,
    # so a smaller cap would clip the child rather than shrink it -- the same
    # corner `configure_scrolled_page` guards for the scrolled column.
    child.setMaximumWidth(max(page_width_cap(child, kind), child.minimumSizeHint().width()))
    # The child carries the only stretch, so it takes the width first and stops
    # at its cap; the two zero-stretch spacers then split whatever is left,
    # which is what centres it. Give the spacers stretch too and all three
    # share the row equally -- the bar ends up a third of the window wide while
    # the column above it is capped at the full measure.
    row.addStretch()
    row.addWidget(child, 1)
    row.addStretch()
    host.setLayout(row)
    host.setObjectName(f"{child.objectName()}-host" if child.objectName() else "workflow-column-host")
    return host
