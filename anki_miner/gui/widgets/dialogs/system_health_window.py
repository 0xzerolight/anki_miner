"""The System Health destination (decision D26).

A wizard establishes facts that go stale the moment it closes: Anki gets shut
down, dictionaries need reimporting after an upgrade, yt-dlp ages out. Until
now the only route back was re-running the wizard, so this screen exists
permanently instead — every readiness fact the app has, when it last checked,
and a button that jumps to the control that repairs it.

Three rules shape it.

* **A check that has not run is not a failure.** Every row starts *unknown* and
  stays unknown until something reports. This is also true *within* a sweep:
  the deck, note type and field checks are skipped entirely when AnkiConnect is
  unreachable, so painting them red would invent three failures out of one.
* **The window observes; it never owns a worker.** It renders whatever the last
  ``BackgroundTaskController`` result said and asks for a re-check by signal.
  Closing it cancels nothing, and a result that lands while it is hidden is
  still there when it reopens, because the report lives on the main window.
* **Fix is a deep link, not a re-implementation.** A row knows one stable
  setting-anchor id (D11) and emits it. Resolving that to a tab, a page, a
  scroll position and a focused widget stays entirely in ``SettingsTab``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import SPACING
from anki_miner.gui.utils.language_gate import apply_language_gate
from anki_miner.gui.widgets.base import EnhancedDialog, StatusBadge
from anki_miner.models import ValidationResult
from anki_miner.utils.i18n import tr_format

__all__ = [
    "HEALTH_FAIL",
    "HEALTH_FIX_ANCHORS",
    "HEALTH_GROUPS",
    "HEALTH_KEYS",
    "HEALTH_OK",
    "HEALTH_UNKNOWN",
    "HEALTH_WARN",
    "HealthCheck",
    "HealthReport",
    "SystemHealthWindow",
    "checks_from_validation",
]

#: Row states. ``unknown`` is a first-class value, not a stand-in for failure.
HEALTH_UNKNOWN = "unknown"
HEALTH_OK = "ok"
HEALTH_WARN = "warn"
HEALTH_FAIL = "fail"

#: Detail shown for an optional resource family the user has not set up. Rows
#: come from a static group tuple, so frequency and pitch always render; saying
#: so plainly beats a green tick for something absent or a warning for a choice.
_NOT_CONFIGURED = "Not configured (optional)"

#: Row order, grouped exactly as D26 names the groups. The group keys are
#: stable; their titles are translated at render time.
HEALTH_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("destination", ("anki.connect", "anki.deck", "anki.note_type", "anki.fields")),
    ("media", ("tools.ffmpeg", "tools.ffprobe")),
    ("language", ("resources.dictionary", "resources.frequency", "resources.pitch", "resources.audio")),
    ("optional", ("tools.ytdlp", "tools.alass")),
    ("updates", ("app.updates",)),
)

HEALTH_KEYS: tuple[str, ...] = tuple(key for _group, keys in HEALTH_GROUPS for key in keys)

#: Health rows that only exist for a language with the matching capability.
#: The keys stay in HEALTH_GROUPS: the report still carries every row, and
#: the window is the only thing that hides one.
_ROW_CAPABILITIES: dict[str, str] = {"resources.pitch": "pitch"}

#: Where a broken row is repaired, as a stable setting-anchor id (D11). Rows
#: absent from this map have no in-app control to jump to — ffmpeg and ffprobe
#: are resolved from PATH or the bundle and have no setting, and an available
#: update is taken through the update banner — so they show no Fix button
#: rather than a button that lands somewhere unrelated.
HEALTH_FIX_ANCHORS: dict[str, str] = {
    "anki.connect": "anki.ankiconnect_url_input",
    "anki.deck": "anki.deck_name",
    "anki.note_type": "anki.note_type",
    "anki.fields": "anki.expression_field_input",
    "resources.dictionary": "dictionaries.chain",
    "resources.frequency": "frequency.chain",
    "resources.pitch": "pitch.chain",
    "resources.audio": "audio.chain",
    "tools.ytdlp": "youtube.ytdlp_update",
    "tools.alass": "subtitles.alass_download",
}

#: ``ValidationIssue.component`` → row key. Components with no row here (the
#: temp folder) are still surfaced by the whole-window issue banner; this screen
#: shows the five groups D26 accepted and does not grow a sixth for them.
_COMPONENT_KEYS: dict[str, str] = {
    "AnkiConnect": "anki.connect",
    "Anki Deck": "anki.deck",
    "Note Type": "anki.note_type",
    "Field Mapping": "anki.fields",
    "ffmpeg": "tools.ffmpeg",
    "ffprobe": "tools.ffprobe",
    "Offline Dictionary": "resources.dictionary",
    "Frequency Sources": "resources.frequency",
    "Pitch Sources": "resources.pitch",
    "Audio Packs": "resources.audio",
    "yt-dlp": "tools.ytdlp",
    "alass": "tools.alass",
}

#: Row state → ``StatusBadge`` status. ``unknown`` takes the neutral "pending"
#: pill rather than the status bar's blue "checking" one: a row can be unknown
#: because nothing is running and nothing ever asked (the deck check is skipped
#: outright when Anki is unreachable), and "no information" should not look like
#: work in progress.
_BADGE_STATUS: dict[str, str] = {
    HEALTH_UNKNOWN: "pending",
    HEALTH_OK: "success",
    HEALTH_WARN: "warning",
    HEALTH_FAIL: "error",
}


@dataclass(frozen=True)
class HealthCheck:
    """One readiness fact: what it is, how it stands, and when that was learnt.

    ``detail`` is the service's own English diagnostic. It is deliberately not
    translated: it is produced by ``ValidationService`` for logs and bug
    reports, and inventing a parallel translated copy of it would let the two
    disagree. The row's *label* and *state* — the parts a user reads to decide
    whether anything is wrong — are translated by the window.
    """

    key: str
    state: str = HEALTH_UNKNOWN
    detail: str = ""
    checked_at: datetime | None = None


def checks_from_validation(result: ValidationResult, checked_at: datetime) -> dict[str, HealthCheck]:
    """Derive every validation-sourced row from one ``ValidationResult``.

    Pure, so the "what does a half-answered sweep look like?" question is
    answerable without a widget. The dependent rows are the point: validation
    skips the deck and note-type checks entirely when AnkiConnect is down, and
    skips the field check unless the note type resolved, so those rows report
    *unknown* rather than repeating one failure as four.
    """
    messages: dict[str, tuple[str, str]] = {}
    for issue in result.issues:
        key = _COMPONENT_KEYS.get(issue.component)
        if key is None or key in messages:
            continue
        messages[key] = (issue.severity, issue.message)

    def _issue_state(key: str, absent: str = HEALTH_OK) -> tuple[str, str]:
        found = messages.get(key)
        if found is None:
            return absent, ""
        severity, message = found
        return (HEALTH_FAIL if severity == "ERROR" else HEALTH_WARN), message

    def _record(key: str, state: str, detail: str) -> HealthCheck:
        return HealthCheck(key=key, state=state, detail=detail, checked_at=checked_at)

    versions = result.tool_versions
    checks: dict[str, HealthCheck] = {}

    connect_state, connect_detail = _issue_state("anki.connect")
    checks["anki.connect"] = _record("anki.connect", connect_state, connect_detail)

    for key, passed in (("anki.deck", result.deck_exists), ("anki.note_type", result.note_type_exists)):
        if not result.ankiconnect_ok:
            checks[key] = HealthCheck(key=key, state=HEALTH_UNKNOWN)
            continue
        state, detail = _issue_state(key, absent=HEALTH_OK if passed else HEALTH_UNKNOWN)
        checks[key] = _record(key, state, detail)

    if not result.ankiconnect_ok or not result.note_type_exists:
        checks["anki.fields"] = HealthCheck(key="anki.fields", state=HEALTH_UNKNOWN)
    else:
        state, detail = _issue_state("anki.fields")
        checks["anki.fields"] = _record("anki.fields", state, detail)

    for key in ("tools.ffmpeg", "tools.ffprobe", "tools.alass"):
        state, detail = _issue_state(key)
        checks[key] = _record(key, state, detail)

    dictionary_state, dictionary_detail = _issue_state("resources.dictionary")
    checks["resources.dictionary"] = _record(
        "resources.dictionary",
        dictionary_state,
        dictionary_detail or versions.get("offline-dictionary", ""),
    )

    # Frequency, pitch and audio packs are optional: an unconfigured family
    # produces no issue AND no version string, which reads as "unknown" rather
    # than a green tick for something that is not set up. Configured-and-healthy
    # fills the detail with source names and counts.
    for key, version_key in (
        ("resources.frequency", "frequency-sources"),
        ("resources.pitch", "pitch-sources"),
        ("resources.audio", "audio-packs"),
    ):
        state, detail = _issue_state(key)
        summary = versions.get(version_key, "")
        if state == HEALTH_OK and not detail and not summary:
            checks[key] = HealthCheck(key=key, state=HEALTH_UNKNOWN, detail=_NOT_CONFIGURED, checked_at=checked_at)
            continue
        checks[key] = _record(key, state, detail or summary)

    ytdlp_state, ytdlp_detail = _issue_state("tools.ytdlp")
    checks["tools.ytdlp"] = _record("tools.ytdlp", ytdlp_state, ytdlp_detail or versions.get("yt-dlp", ""))

    return checks


@dataclass(frozen=True)
class HealthReport:
    """Every row's current state, plus any failure of the sweep itself.

    Held by the main window rather than by the screen, so a result that arrives
    while System Health is closed is not lost and a re-opened window is
    immediately correct.
    """

    checks: dict[str, HealthCheck] = field(default_factory=dict)
    #: Set when the validation worker itself failed. Distinct from a row
    #: failing: nothing was learnt, so the rows go back to unknown.
    error: str = ""

    @classmethod
    def unknown(cls) -> HealthReport:
        """A report before anything has been checked."""
        return cls(checks={key: HealthCheck(key=key) for key in HEALTH_KEYS})

    def get(self, key: str) -> HealthCheck:
        """The row for ``key``, unknown if it has never been reported."""
        return self.checks.get(key, HealthCheck(key=key))

    def checking(self) -> HealthReport:
        """Return the validation-sourced rows to unknown; a probe is starting.

        The update row is untouched: it is answered by a different check and a
        validation sweep says nothing about it.
        """
        checks = dict(self.checks)
        for key in HEALTH_KEYS:
            if key != "app.updates":
                checks[key] = HealthCheck(key=key)
        return replace(self, checks=checks, error="")

    def with_validation(self, result: ValidationResult, checked_at: datetime) -> HealthReport:
        """Fold one completed validation sweep in."""
        checks = dict(self.checks)
        checks.update(checks_from_validation(result, checked_at))
        return replace(self, checks=checks, error="")

    def with_validation_error(self, message: str) -> HealthReport:
        """Record that the sweep failed, and that therefore nothing is known."""
        return replace(self.checking(), error=message)

    def with_update_check(self, *, state: str, detail: str, checked_at: datetime) -> HealthReport:
        """Fold the update check's answer in."""
        checks = dict(self.checks)
        checks["app.updates"] = HealthCheck(
            key="app.updates",
            state=state,
            detail=detail,
            checked_at=checked_at,
        )
        return replace(self, checks=checks)


class _HealthRow(QFrame):
    """One rendered row. Built once; repainted in place on every report."""

    fix_requested = pyqtSignal(str)

    def __init__(self, key: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self.setObjectName("health-row")

        # The horizontal margin is the row's own padding: without it the badge
        # sits flush against the scroll viewport's left edge and the time
        # against the scrollbar. The vertical one is deliberately small — the
        # column that owns these rows sets no spacing of its own, so this is
        # the entire gap between two sibling rows.
        column = QVBoxLayout(self)
        column.setContentsMargins(SPACING.xs, SPACING.xxs, SPACING.xs, SPACING.xxs)
        column.setSpacing(SPACING.xxs)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACING.sm)

        # Keeps StatusBadge's own object name: the status-bar's
        # ``status-indicator`` override only paints success and error, so a
        # badge renamed to it would leave "Unknown" and "Needs attention" as
        # bare text — the two states this screen exists to distinguish.
        self.badge = StatusBadge("", status="pending", clickable=False)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # The pill goes in a fixed-width cell rather than being widened itself.
        # Widening it is what made "Ready" render as a slab the width of "Needs
        # attention"; the column still lines up because the *cell* reserves
        # that width. ``AlignVCenter`` stops it stretching to the row's tallest
        # item as well — the badge's own vertical policy lets it grow, so rows
        # showing a Fix button used to get a taller pill than rows without one.
        self.badge_cell = QWidget()
        badge_row = QHBoxLayout(self.badge_cell)
        badge_row.setContentsMargins(0, 0, 0, 0)
        badge_row.setSpacing(0)
        badge_row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignVCenter)
        badge_row.addStretch()
        top.addWidget(self.badge_cell)

        self.label = QLabel(label)
        label_font = QFont()
        label_font.setWeight(QFont.Weight.Medium)
        self.label.setFont(label_font)
        top.addWidget(self.label, 1)

        self.checked_label = QLabel("")
        self.checked_label.setObjectName("row-meta")
        self.checked_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        top.addWidget(self.checked_label)

        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        self.fix_button = ModernButton(self.tr("Fix"), variant="secondary")
        self.fix_button.clicked.connect(lambda: self.fix_requested.emit(self._key))
        # Hiding the button must not give its width back: the rows that have
        # one are exactly the rows being read, and releasing the space slid the
        # time column sideways on precisely those. Retaining it also makes
        # every row the same height, button or no button.
        fix_policy = self.fix_button.sizePolicy()
        fix_policy.setRetainSizeWhenHidden(True)
        self.fix_button.setSizePolicy(fix_policy)
        self.fix_button.hide()
        top.addWidget(self.fix_button)

        column.addLayout(top)

        # `row-detail`, not `helper-text`: the two are the same style except
        # that helper text carries its own horizontal padding, which would sit
        # on top of the indent `set_detail_indent` computes and leave the line
        # 8px off the label it belongs to.
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("row-detail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.hide()
        column.addWidget(self.detail_label)

    def set_detail_indent(self, pixels: int) -> None:
        """Start the diagnostic under the label it explains, not under the pill.

        Called once from :meth:`SystemHealthWindow._align_columns`, which is the
        only thing that knows how wide the badge column came out.
        """
        self.detail_label.setContentsMargins(pixels, 0, 0, 0)

    def apply_check(self, check: HealthCheck, *, state_text: str, checked_text: str) -> None:
        """Repaint from one fact. Never stores it: the report is the truth."""
        self.badge.set_name(state_text)
        self.badge.set_status(_BADGE_STATUS.get(check.state, "pending"))
        self.checked_label.setText(checked_text)
        self.detail_label.setText(check.detail)
        self.detail_label.setVisible(bool(check.detail))
        # Nothing to repair while a row is healthy or unreported, and no route
        # to offer for a row with no in-app control behind it.
        self.fix_button.setVisible(
            check.state in (HEALTH_WARN, HEALTH_FAIL) and self._key in HEALTH_FIX_ANCHORS,
        )


class SystemHealthWindow(EnhancedDialog):
    """The permanent readiness screen, opened from the status bar (D26).

    Modeless and re-shown, never rebuilt: the owner keeps one instance and calls
    :meth:`show_health`, so closing it is hiding it and reopening it costs
    nothing. It holds no worker and cancels none.

    Signals:
        recheck_requested: The user asked for a fresh sweep.
        export_requested: The user asked to export the current diagnostics.
        fix_requested: Emitted with the stable setting-anchor id to reveal.
    """

    recheck_requested = pyqtSignal()
    export_requested = pyqtSignal()
    fix_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("System Health"))
        self.setMinimumWidth(560)
        self.set_header(
            "",
            self.tr("System Health"),
            self.tr("What Anki Miner needs in order to mine, and whether it has it."),
        )

        self.error_label = QLabel("")
        self.error_label.setObjectName("validation-status")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        self.add_content(self.error_label)

        self._rows: dict[str, _HealthRow] = {}
        #: ``(row, capability)`` pairs for ``set_capabilities``; appended to by
        #: ``_build_rows`` so a later contributor extends rather than replaces.
        self._language_gate_pairs: list[tuple[QWidget, str]] = []
        #: Set by ``_build_rows``; kept so ``_size_to_content`` can ask the list
        #: how tall it wants to be rather than the scroller how tall it settles
        #: for.
        self._health_list: QWidget
        self._health_scroll: QScrollArea
        self.add_content(self._build_rows(), 1)

        self.recheck_button = self.add_button(self.tr("Re-check now"), "secondary", self.recheck_requested.emit)
        self.export_button = self.add_button(
            self.tr("Export diagnostics…"),
            "secondary",
            self.export_requested.emit,
        )
        self.add_close_button()

        self._size_to_content()
        self.show_health(HealthReport.unknown())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_rows(self) -> QWidget:
        """One scrollable column of titled groups, built once.

        The column sets no spacing of its own. A single spacing value cannot
        separate a title from the rows it owns, two sibling rows, and one group
        from the next — giving all three the same gap is what made the five
        groups read as one flat list. Each gap is stated here instead: rows
        carry their own 4px margins, a title sits 4px above its first row, and
        groups are a full 24px apart.
        """
        container = QWidget()
        container.setObjectName("health-list")
        self._health_list = container
        column = QVBoxLayout(container)
        # Right margin only: a gutter so the time column clears the scrollbar.
        column.setContentsMargins(0, 0, SPACING.xs, 0)
        column.setSpacing(0)

        labels = self._row_labels()
        for index, (group_key, keys) in enumerate(HEALTH_GROUPS):
            if index:
                column.addSpacing(SPACING.lg)
            title = QLabel(self._group_titles()[group_key])
            title.setObjectName("heading3")
            # Matches ``_HealthRow``'s own left padding, so a heading starts at
            # the same x as the badges beneath it. ``heading3`` already carries
            # the weight; a QFont set here would not have been applied anyway,
            # because a stylesheet outranks setFont().
            title.setContentsMargins(SPACING.xs, 0, 0, 0)
            column.addWidget(title)
            column.addSpacing(SPACING.xxs)
            for key in keys:
                row = _HealthRow(key, labels[key])
                row.fix_requested.connect(self._on_fix_requested)
                self._rows[key] = row
                column.addWidget(row)
        self._language_gate_pairs.extend(
            (self._rows[key], capability) for key, capability in _ROW_CAPABILITIES.items() if key in self._rows
        )
        column.addStretch()
        self._align_columns()

        scroll = QScrollArea()
        scroll.setObjectName("health-scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(container)
        if viewport := scroll.viewport():
            # Named so the stylesheet can stop it painting itself in the
            # window-background colour inside a surface-coloured dialog.
            viewport.setObjectName("health-scroll-viewport")
        self._health_scroll = scroll
        return scroll

    def _size_to_content(self) -> None:
        """Open at a height that shows the rows, not a quarter of them.

        A readiness screen that opens showing four of its ten rows, with a group
        heading sliced in half at the bottom edge, makes the user scroll to
        finish reading the answer. A scroll area asks for very little, so the
        height is taken from what the list actually wants and the scroller's own
        modest request is subtracted back out. Derived rather than a constant,
        so adding a sixth group does not quietly re-introduce the clipping.

        Clamped to the screen, so it still opens whole on a laptop; the window
        stays resizable and still scrolls when it is made smaller.
        """
        chrome = self.sizeHint().height() - self._health_scroll.sizeHint().height()
        wanted = chrome + self._health_list.sizeHint().height()

        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen is not None else 900
        self.resize(600, min(wanted, int(available * 0.9)))

    def _align_columns(self) -> None:
        """Reserve one width per column, so every row lines up on all three.

        Measured rather than hard-coded: the state words and "Checked …" are
        translated, and a pixel width chosen against English clips the German
        ones. The badge column reserves the widest *pill*; the pill itself is
        left to hug its own word, since padding a five-letter state out to the
        width of "Needs attention" is what made these read as coloured slabs.
        """
        rows = list(self._rows.values())
        if not rows:
            return

        badge_metrics = rows[0].badge.fontMetrics()
        # The badge is a pill: its QSS padding sits outside the measured text.
        badge_width = (
            max(
                badge_metrics.horizontalAdvance(self._state_text(state))
                for state in (HEALTH_UNKNOWN, HEALTH_OK, HEALTH_WARN, HEALTH_FAIL)
            )
            + 2 * SPACING.sm
        )

        meta_metrics = rows[0].checked_label.fontMetrics()
        # Digits are not equal-width in every face, so the worst-case clock is
        # built from whichever digit measures widest rather than assumed to be
        # "00:00".
        widest_digit = max("0123456789", key=meta_metrics.horizontalAdvance)
        meta_width = max(
            meta_metrics.horizontalAdvance(self._checked_text(None)),
            meta_metrics.horizontalAdvance(self._checked_at_text(f"{widest_digit * 2}:{widest_digit * 2}")),
        )

        # The diagnostic belongs under the label, not under the pill: the badge
        # column plus the gap after it is exactly where the label starts. This
        # is arithmetic and not a stylesheet padding on purpose, so the two line
        # up whether or not a stylesheet is loaded.
        detail_indent = badge_width + SPACING.sm

        for row in rows:
            row.badge_cell.setFixedWidth(badge_width)
            # Fixed, not minimum: a minimum lets the longer of the two strings
            # claim an extra pixel and shifts that one row's column.
            row.checked_label.setFixedWidth(meta_width)
            row.set_detail_indent(detail_indent)

    def _on_fix_requested(self, key: str) -> None:
        """Translate a row into the setting id that repairs it.

        The row does not learn the anchor and the window does not learn the tab:
        the id goes to the owner, which asks Settings to reveal it. A top-level
        window cannot use the ``reveal_settings`` duck-typing helper — its own
        ``window()`` is itself, not the main window — so the route out is a
        signal.
        """
        anchor = HEALTH_FIX_ANCHORS.get(key)
        if anchor:
            self.fix_requested.emit(anchor)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def set_export_enabled(self, enabled: bool) -> None:
        """Enable both visible and programmatic diagnostics export."""
        self.export_button.setEnabled(enabled)

    def set_capabilities(self, capabilities: frozenset[str]) -> None:
        """Show only the resource rows the active mining language can use.

        ``apply_language_gate`` owns a paired row's whole visibility in both
        directions, so a switch away from pitch and back lands where it
        started. The row keeps its key either way: the report and the
        diagnostics export still carry every check.
        """
        apply_language_gate(self._language_gate_pairs, capabilities)

    def show_health(self, report: HealthReport) -> None:
        """Repaint every row from ``report``. Safe to call while hidden."""
        for key, row in self._rows.items():
            check = report.get(key)
            row.apply_check(
                check,
                state_text=self._state_text(check.state),
                checked_text=self._checked_text(check.checked_at),
            )
        self.error_label.setText(report.error)
        self.error_label.setVisible(bool(report.error))

    def _state_text(self, state: str) -> str:
        """The state as a word, so the row does not depend on its colour."""
        return {
            HEALTH_OK: self.tr("Ready"),
            HEALTH_WARN: self.tr("Needs attention"),
            HEALTH_FAIL: self.tr("Not working"),
        }.get(state, self.tr("Unknown"))

    def _checked_text(self, checked_at: datetime | None) -> str:
        if checked_at is None:
            return self.tr("Not checked yet")
        return self._checked_at_text(checked_at.strftime("%H:%M"))

    def _checked_at_text(self, clock: str) -> str:
        """The "Checked …" line for an already-formatted clock time.

        Split out so ``_align_columns`` can measure a worst-case time without
        spelling the translated string a second time.
        """
        return tr_format(self.tr("Checked %1"), clock)

    def _group_titles(self) -> dict[str, str]:
        return {
            "destination": self.tr("Where cards go"),
            "media": self.tr("Media tools"),
            "language": self.tr("Language resources"),
            "optional": self.tr("Optional features"),
            "updates": self.tr("Updates"),
        }

    def _row_labels(self) -> dict[str, str]:
        return {
            "anki.connect": self.tr("AnkiConnect"),
            "anki.deck": self.tr("Deck"),
            "anki.note_type": self.tr("Note type"),
            "anki.fields": self.tr("Field mapping"),
            "tools.ffmpeg": self.tr("ffmpeg"),
            "tools.ffprobe": self.tr("ffprobe"),
            "resources.dictionary": self.tr("Offline dictionary"),
            "resources.frequency": self.tr("Frequency lists"),
            "resources.pitch": self.tr("Pitch accent"),
            "resources.audio": self.tr("Audio packs"),
            "tools.ytdlp": self.tr("yt-dlp (YouTube mining)"),
            "tools.alass": self.tr("alass (subtitle retiming)"),
            "app.updates": self.tr("Anki Miner updates"),
        }
