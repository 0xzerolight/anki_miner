"""Theme contrast is *measured and reported*, never corrected (decision D43-A).

The owner's ruling: a theme renders exactly as its author wrote it. The app may
say "this is hard to read, here is the number"; it may not derive, substitute or
reject a single colour, and it may not grow ``REQUIRED_COLOR_KEYS``.

So these tests pin three things:

1. ``assess_theme_contrast`` is a pure measurement over synthetic colours with
   known ratios — thresholds are proven by straddling them, not by counting how
   many shipped themes happen to fail today.
2. Nothing the assessment or the live preview touches mutates a colour: the
   dictionaries ``Theme.get_colors`` hands out still match the JSON on disk.
3. All 29 shipped themes still load, compile and render.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import (
    CONTRAST_ROLE_MUTED_TEXT,
    CONTRAST_ROLE_PRIMARY_LABEL,
    CONTRAST_ROLE_SURFACE_EDGE,
    READABLE_CONTRAST_RATIO,
    REQUIRED_COLOR_KEYS,
    SURFACE_SEPARATION_RATIO,
    ContrastIssue,
    Theme,
    assess_theme_contrast,
)
from anki_miner.gui.widgets.panels.ui_settings_panel import UISettingsPanel

# Ratios computed from the WCAG relative-luminance formula, independent of the
# implementation under test, so a broken formula cannot agree with itself.
RATIO_GREY_ON_WHITE_FAIL = 4.4781  # #777777 — just below 4.5
RATIO_SURFACE_FAIL = 1.0119  # #212121 on #202020 — flat card
# Straddling values with no assertion of their own: #767676 measures 4.5422 and
# #2a2a2a measures 1.1351, so each "not reported" test sits just past the line.


def _colors(**overrides: str) -> dict[str, str]:
    """A complete colour mapping: readable defaults plus the keys under test."""
    base = dict.fromkeys(REQUIRED_COLOR_KEYS, "#ffffff")
    base.update(
        {
            "background": "#ffffff",
            "surface": "#d0d0d0",
            "text-muted": "#595959",
            "primary": "#0000cc",
            "text-on-primary": "#ffffff",
        }
    )
    base.update(overrides)
    return base


def _roles(issues: tuple[ContrastIssue, ...]) -> set[str]:
    return {issue.role for issue in issues}


def _issue(issues: tuple[ContrastIssue, ...], role: str) -> ContrastIssue:
    match = [i for i in issues if i.role == role]
    assert match, f"no issue reported for {role!r}"
    return match[0]


class TestAssessThemeContrast:
    """Synthetic colours with known ratios pin the measurement and thresholds."""

    def test_readable_theme_reports_nothing(self):
        assert assess_theme_contrast(_colors()) == ()

    def test_primary_label_below_threshold_is_reported_with_its_ratio(self):
        issues = assess_theme_contrast(_colors(**{"text-on-primary": "#777777", "primary": "#ffffff"}))

        issue = _issue(issues, CONTRAST_ROLE_PRIMARY_LABEL)
        assert issue.ratio == pytest.approx(RATIO_GREY_ON_WHITE_FAIL, abs=0.01)

    def test_primary_label_just_above_threshold_is_not_reported(self):
        issues = assess_theme_contrast(_colors(**{"text-on-primary": "#767676", "primary": "#ffffff"}))

        assert CONTRAST_ROLE_PRIMARY_LABEL not in _roles(issues)

    def test_muted_text_below_threshold_is_reported_with_its_ratio(self):
        issues = assess_theme_contrast(_colors(**{"text-muted": "#777777", "background": "#ffffff"}))

        assert _issue(issues, CONTRAST_ROLE_MUTED_TEXT).ratio == pytest.approx(RATIO_GREY_ON_WHITE_FAIL, abs=0.01)

    def test_muted_text_just_above_threshold_is_not_reported(self):
        issues = assess_theme_contrast(_colors(**{"text-muted": "#767676", "background": "#ffffff"}))

        assert CONTRAST_ROLE_MUTED_TEXT not in _roles(issues)

    def test_flat_card_surface_is_reported_with_its_ratio(self):
        issues = assess_theme_contrast(_colors(**{"surface": "#212121", "background": "#202020"}))

        assert _issue(issues, CONTRAST_ROLE_SURFACE_EDGE).ratio == pytest.approx(RATIO_SURFACE_FAIL, abs=0.01)

    def test_separated_card_surface_is_not_reported(self):
        issues = assess_theme_contrast(_colors(**{"surface": "#2a2a2a", "background": "#202020"}))

        assert CONTRAST_ROLE_SURFACE_EDGE not in _roles(issues)

    def test_every_role_can_be_reported_at_once(self):
        issues = assess_theme_contrast(
            _colors(
                **{
                    "background": "#202020",
                    "surface": "#212121",
                    "text-muted": "#303030",
                    "primary": "#808080",
                    "text-on-primary": "#999999",
                }
            )
        )

        assert _roles(issues) == {
            CONTRAST_ROLE_PRIMARY_LABEL,
            CONTRAST_ROLE_MUTED_TEXT,
            CONTRAST_ROLE_SURFACE_EDGE,
        }

    def test_thresholds_are_the_published_ones(self):
        assert READABLE_CONTRAST_RATIO == 4.5
        assert SURFACE_SEPARATION_RATIO == 1.10


class TestUnmeasurableColours:
    """An unparseable colour is reported as unmeasurable — never replaced."""

    def test_invalid_colour_reports_ratio_none(self):
        issues = assess_theme_contrast(_colors(**{"text-on-primary": "not-a-colour"}))

        assert _issue(issues, CONTRAST_ROLE_PRIMARY_LABEL).ratio is None

    def test_missing_key_reports_ratio_none(self):
        colors = _colors()
        del colors["text-muted"]

        assert _issue(assess_theme_contrast(colors), CONTRAST_ROLE_MUTED_TEXT).ratio is None

    def test_invalid_colour_is_neither_rejected_nor_substituted(self):
        colors = _colors(**{"text-on-primary": "not-a-colour"})

        assess_theme_contrast(colors)

        assert colors["text-on-primary"] == "not-a-colour"

    def test_assessment_never_mutates_its_input(self):
        colors = _colors(**{"surface": "#212121", "background": "#202020"})
        before = dict(colors)

        assess_theme_contrast(colors)

        assert colors == before


# --------------------------------------------------------------------------
# Shipped themes: all 29 still load, compile, render, and stay author-exact.
# --------------------------------------------------------------------------


def _shipped_theme_files() -> list[Path]:
    files = sorted((get_resource_dir() / "styles" / "themes").glob("*.json"))
    assert len(files) == 29, f"expected 29 shipped themes, found {len(files)}"
    return files


SHIPPED_KEYS = [p.stem for p in _shipped_theme_files()]


@pytest.fixture
def shipped_themes() -> Iterator[None]:
    """Force the real shipped theme directory (conftest resets Theme per test)."""
    Theme.initialize(active="light", shipped_dir=None)
    yield


class TestShippedThemesStayAuthorExact:
    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_get_colors_matches_the_file_on_disk(self, shipped_themes, key: str):
        on_disk = json.loads((get_resource_dir() / "styles" / "themes" / f"{key}.json").read_text(encoding="utf-8"))

        assert Theme.get_colors(key) == on_disk["colors"]

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_assessment_leaves_get_colors_byte_for_byte_unchanged(self, shipped_themes, key: str):
        before = dict(Theme.get_colors(key))

        assess_theme_contrast(Theme.get_colors(key))

        assert Theme.get_colors(key) == before

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_only_known_roles_are_reported(self, shipped_themes, key: str):
        roles = _roles(assess_theme_contrast(Theme.get_colors(key)))

        assert roles <= {
            CONTRAST_ROLE_PRIMARY_LABEL,
            CONTRAST_ROLE_MUTED_TEXT,
            CONTRAST_ROLE_SURFACE_EDGE,
        }

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_every_shipped_colour_is_measurable(self, shipped_themes, key: str):
        """No shipped theme should trip the "unable to measure" path."""
        assert all(issue.ratio is not None for issue in assess_theme_contrast(Theme.get_colors(key)))

    def test_low_contrast_themes_are_still_shipped_unchanged(self, shipped_themes):
        """D43-A in one assertion: hard-to-read themes stay hard to read."""
        flagged = [key for key in SHIPPED_KEYS if assess_theme_contrast(Theme.get_colors(key))]

        assert flagged, "expected the shipped set to contain low-contrast themes"
        for key in flagged:
            on_disk = json.loads((get_resource_dir() / "styles" / "themes" / f"{key}.json").read_text(encoding="utf-8"))
            assert Theme.get_colors(key) == on_disk["colors"]


class TestShippedThemesRender:
    """Compile + polish + paint every shipped theme."""

    @pytest.fixture
    def sample_tree(self, qtbot) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        for widget in (QPushButton("Mine"), QLabel("Ready"), QLineEdit(), QComboBox(), QTextEdit(), QProgressBar()):
            layout.addWidget(widget)
        host.findChild(QLineEdit).setPlaceholderText("Search")
        qtbot.addWidget(host)
        return host

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_stylesheet_has_no_unresolved_variables(self, shipped_themes, key: str):
        # The file header documents the ${…} syntax in a comment; strip comments
        # before looking for placeholders the substitution failed to resolve.
        rules = re.sub(r"/\*.*?\*/", "", Theme.get_stylesheet(key), flags=re.DOTALL)

        assert "${" not in rules

    @pytest.mark.parametrize("key", SHIPPED_KEYS)
    def test_theme_polishes_and_paints_without_qt_complaints(self, shipped_themes, key: str, sample_tree: QWidget):
        app = QApplication.instance()
        assert isinstance(app, QApplication)
        messages: list[str] = []

        def handler(mode: QtMsgType, context, message: str) -> None:
            if mode in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg):
                messages.append(message)

        previous = qInstallMessageHandler(handler)
        try:
            app.setStyleSheet(Theme.get_stylesheet(key))
            sample_tree.ensurePolished()
            for child in sample_tree.findChildren(QWidget):
                child.ensurePolished()
            sample_tree.resize(320, 260)
            image = sample_tree.grab().toImage()
        finally:
            qInstallMessageHandler(previous)
            app.setStyleSheet("")

        assert not image.isNull()
        assert messages == []


# --------------------------------------------------------------------------
# The preview warning
# --------------------------------------------------------------------------

CLEAR_THEME = {
    "background": "#ffffff",
    "surface": "#d0d0d0",
    "text-muted": "#595959",
    "primary": "#0000cc",
    "text-on-primary": "#ffffff",
}
MURKY_THEME = {
    "background": "#202020",
    "surface": "#212121",
    "input-bg": "#232323",
    "text": "#242424",
    "text-disabled": "#252525",
    "input-disabled-bg": "#262626",
    "disabled": "#272727",
    "text-muted": "#303030",
    "primary": "#808080",
    "text-on-primary": "#999999",
}


def _theme_file(path: Path, name: str, colors: dict[str, str]) -> None:
    data = {"name": name, "colors": dict.fromkeys(REQUIRED_COLOR_KEYS, "#000000") | colors}
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def themes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "themes"
    d.mkdir()
    _theme_file(d / "clear.json", "Clear", CLEAR_THEME)
    _theme_file(d / "murky.json", "Murky", MURKY_THEME)
    _theme_file(d / "broken.json", "Broken", CLEAR_THEME | {"text-muted": "not-a-colour"})
    return d


@pytest.fixture
def panel(qapp, qtbot, themes_dir: Path) -> UISettingsPanel:
    Theme.initialize(active="clear", favorites=("clear",), shipped_dir=themes_dir)
    p = UISettingsPanel(themes_dir)
    qtbot.addWidget(p)
    return p


def _card(panel: UISettingsPanel, key: str):
    card = panel.gallery.card(key)
    assert card is not None, f"no card for {key!r}"
    return card


class TestContrastWarningLabel:
    def test_hidden_for_a_readable_theme(self, panel: UISettingsPanel):
        assert panel.contrast_warning.text() == ""
        assert panel.contrast_warning.isHidden()

    def test_shown_with_the_measured_ratio_on_populate(self, qapp, qtbot, themes_dir: Path):
        Theme.initialize(active="murky", favorites=(), shipped_dir=themes_dir)
        p = UISettingsPanel(themes_dir)
        qtbot.addWidget(p)

        assert not p.contrast_warning.isHidden()
        assert "1.0" in p.contrast_warning.text()

    def test_updates_when_a_row_is_previewed(self, panel: UISettingsPanel):
        _card(panel, "murky").click()

        assert not panel.contrast_warning.isHidden()
        assert "1.0" in panel.contrast_warning.text()

    def test_clears_again_when_a_readable_theme_is_previewed(self, panel: UISettingsPanel):
        _card(panel, "murky").click()
        _card(panel, "clear").click()

        assert panel.contrast_warning.text() == ""
        assert panel.contrast_warning.isHidden()

    def test_selecting_the_already_active_row_still_refreshes(self, panel: UISettingsPanel):
        """The early return in ``_on_theme_activated`` must not skip the warning."""
        Theme.set_mode("murky")
        panel.contrast_warning.setText("")

        _card(panel, "murky").click()

        assert not panel.contrast_warning.isHidden()

    def test_updates_after_revert(self, panel: UISettingsPanel):
        panel.reset_baseline()  # baseline = "clear"
        _card(panel, "murky").click()
        assert not panel.contrast_warning.isHidden()

        panel._revert_preview()

        assert panel.contrast_warning.text() == ""

    def test_updates_after_load_from_config(self, panel: UISettingsPanel, test_config):
        Theme.set_mode("murky")

        panel.load_from_config(replace(test_config, theme="murky"))

        assert not panel.contrast_warning.isHidden()

    def test_unmeasurable_theme_is_reported_not_rejected(self, qapp, qtbot, themes_dir: Path):
        Theme.initialize(active="broken", favorites=(), shipped_dir=themes_dir)
        p = UISettingsPanel(themes_dir)
        qtbot.addWidget(p)

        assert not p.contrast_warning.isHidden()
        assert Theme.get_colors("broken")["text-muted"] == "not-a-colour"

    def test_preview_leaves_the_theme_colours_untouched(self, panel: UISettingsPanel, themes_dir: Path):
        on_disk = json.loads((themes_dir / "murky.json").read_text(encoding="utf-8"))["colors"]

        _card(panel, "murky").click()

        assert Theme.get_colors("murky") == on_disk

    def test_warning_is_wrapped_and_sits_under_the_gallery(self, panel: UISettingsPanel):
        assert panel.contrast_warning.wordWrap() is True
        layout = panel.layout()
        gallery_index = layout.indexOf(panel.gallery)
        assert layout.indexOf(panel.contrast_warning) == gallery_index + 1


class TestPreviewDoesNotRepaintColours:
    """Applying a theme writes the author's colours into the palette verbatim."""

    def test_palette_matches_the_authored_values(self, panel: UISettingsPanel, qapp):
        _card(panel, "murky").click()

        palette = qapp.palette()
        assert palette.color(QPalette.ColorRole.Window).name() == MURKY_THEME["background"]
        assert palette.color(QPalette.ColorRole.Base).name() == MURKY_THEME["input-bg"]
        assert palette.color(QPalette.ColorRole.Button).name() == MURKY_THEME["surface"]
        assert palette.color(QPalette.ColorRole.Accent).name() == MURKY_THEME["primary"]
        qapp.setStyleSheet("")

    def test_the_disabled_group_uses_the_theme_s_own_disabled_tokens(self, panel: UISettingsPanel, qapp):
        """Not a dimmed copy of the enabled colours — the author wrote these."""
        _card(panel, "murky").click()

        palette = qapp.palette()
        disabled = QPalette.ColorGroup.Disabled
        assert palette.color(disabled, QPalette.ColorRole.Base).name() == MURKY_THEME["input-disabled-bg"]
        assert palette.color(disabled, QPalette.ColorRole.Button).name() == MURKY_THEME["disabled"]
        assert palette.color(disabled, QPalette.ColorRole.Text).name() == MURKY_THEME["text-disabled"]
        qapp.setStyleSheet("")

    def test_inactive_reads_the_same_as_active(self, panel: UISettingsPanel, qapp):
        """An unfocused window is the same window; Qt's default dimming is not."""
        _card(panel, "murky").click()

        palette = qapp.palette()
        for role in (
            QPalette.ColorRole.Window,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Base,
            QPalette.ColorRole.Highlight,
        ):
            assert palette.color(QPalette.ColorGroup.Inactive, role) == palette.color(QPalette.ColorGroup.Active, role)
        qapp.setStyleSheet("")
