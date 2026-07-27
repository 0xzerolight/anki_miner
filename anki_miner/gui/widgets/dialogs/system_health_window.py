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
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from anki_miner.gui.resources.styles import FONT_SIZES, SPACING
from anki_miner.gui.widgets.base import EnhancedDialog, StatusBadge
from anki_miner.models import ValidationResult
from anki_miner.utils.i18n import tr_format

__all__ = [
    "HEALTH_FAIL",
    "HEALTH_FIX_ANCHORS",
    "HEALTH_GROUPS",
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

#: Row order, grouped exactly as D26 names the groups. The group keys are
#: stable; their titles are translated at render time.
HEALTH_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("destination", ("anki.connect", "anki.deck", "anki.note_type", "anki.fields")),
    ("media", ("tools.ffmpeg", "tools.ffprobe")),
    ("language", ("resources.dictionary",)),
    ("optional", ("tools.ytdlp", "tools.alass")),
    ("updates", ("app.updates",)),
)

HEALTH_KEYS: tuple[str, ...] = tuple(key for _group, keys in HEALTH_GROUPS for key in keys)

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
    "yt-dlp": "tools.ytdlp",
    "alass": "tools.alass",
}

#: Row state → ``StatusBadge`` status. ``unknown`` reuses the badge's neutral
#: "checking" look, which is what the status bar already paints for a probe that
#: has not reported.
_BADGE_STATUS: dict[str, str] = {
    HEALTH_UNKNOWN: "checking",
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

        column = QVBoxLayout(self)
        column.setContentsMargins(0, SPACING.xs, 0, SPACING.xs)
        column.setSpacing(SPACING.xxs)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(SPACING.sm)

        self.badge = StatusBadge("", status="checking", clickable=False)
        self.badge.setObjectName("status-indicator")
        top.addWidget(self.badge)

        self.label = QLabel(label)
        label_font = QFont()
        label_font.setWeight(QFont.Weight.Medium)
        self.label.setFont(label_font)
        top.addWidget(self.label, 1)

        self.checked_label = QLabel("")
        self.checked_label.setObjectName("caption")
        caption_font = QFont()
        caption_font.setPixelSize(FONT_SIZES.caption)
        self.checked_label.setFont(caption_font)
        top.addWidget(self.checked_label)

        from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

        self.fix_button = ModernButton(self.tr("Fix"), variant="secondary")
        self.fix_button.clicked.connect(lambda: self.fix_requested.emit(self._key))
        self.fix_button.hide()
        top.addWidget(self.fix_button)

        column.addLayout(top)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("helper-text")
        self.detail_label.setWordWrap(True)
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.hide()
        column.addWidget(self.detail_label)

    def apply_check(self, check: HealthCheck, *, state_text: str, checked_text: str) -> None:
        """Repaint from one fact. Never stores it: the report is the truth."""
        self.badge.set_name(state_text)
        self.badge.set_status(_BADGE_STATUS.get(check.state, "checking"))
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
        fix_requested: Emitted with the stable setting-anchor id to reveal.
    """

    recheck_requested = pyqtSignal()
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
        self.add_content(self._build_rows(), 1)

        self.recheck_button = self.add_button(self.tr("Re-check now"), "secondary", self.recheck_requested.emit)
        self.add_close_button()

        self.show_health(HealthReport.unknown())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_rows(self) -> QWidget:
        """One scrollable column of titled groups, built once."""
        container = QWidget()
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACING.md)

        labels = self._row_labels()
        for group_key, keys in HEALTH_GROUPS:
            title = QLabel(self._group_titles()[group_key])
            title.setObjectName("heading3")
            title_font = QFont()
            title_font.setWeight(QFont.Weight.Bold)
            title.setFont(title_font)
            column.addWidget(title)
            for key in keys:
                row = _HealthRow(key, labels[key])
                row.fix_requested.connect(self._on_fix_requested)
                self._rows[key] = row
                column.addWidget(row)
        column.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(container)
        return scroll

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
        return tr_format(self.tr("Checked %1"), checked_at.strftime("%H:%M"))

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
            "tools.ytdlp": self.tr("yt-dlp (YouTube mining)"),
            "tools.alass": self.tr("alass (subtitle retiming)"),
            "app.updates": self.tr("Anki Miner updates"),
        }
