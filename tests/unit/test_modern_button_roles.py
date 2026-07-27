"""Accent is a scarce signal, and solid red means destruction (D41).

Before this, the accent colour painted the tab underline, the active tab's text,
*every* unmarked button, stat numbers, queue hover, queue entry counts and the
corner links — so nothing on a screen had priority and the eye had to re-read all
of it. Separately, 12 of the 14 red buttons in the app were Cancel or Stop All;
only two of them destroyed anything.

The rules pinned here:

* accent belongs to one task action per screen, the navigation indicator,
  keyboard focus, checked controls, and progress — nothing else;
* an unmarked button is quiet, and ``secondary`` is simply the name a call site
  uses to say it meant the ordinary one;
* ``danger`` is a red *outline* for reversible removals, ``critical`` the solid
  red for the two actions that cannot be undone;
* whatever Qt has made a dialog's default still renders primary, because that is
  the button Enter will press.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QDialog, QPushButton, QVBoxLayout

from anki_miner.gui.resources import get_resource_dir
from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.widgets.enhanced.modern_button import ModernButton

THEME = "dark"

#: Source root scanned by the static role tests.
GUI_ROOT = Path(__file__).resolve().parents[2] / "anki_miner" / "gui"

#: Deck Builder's fate is unresolved (D3), so its Cancel was left untouched and
#: simply inherits the new outlined ``danger`` rule instead of the solid fill.
D3_FROZEN = "deck_builder_tab.py"


@pytest.fixture(autouse=True)
def _themed(qapp):
    """Render against a real theme, and hand the app back exactly as found."""
    previous = qapp.styleSheet()
    qapp.setStyleSheet(Theme.get_stylesheet(THEME))
    yield
    qapp.setStyleSheet(previous)


@pytest.fixture(scope="module")
def colors() -> dict[str, str]:
    return Theme.get_colors(THEME)


@pytest.fixture(scope="module")
def qss() -> str:
    """``common.qss`` with comments stripped — the rules Qt actually parses."""
    raw = (get_resource_dir() / "styles" / "common.qss").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)


# --------------------------------------------------------------- rendering


class Rendered:
    """The colours a button actually paints, read back off its own pixels."""

    def __init__(self, button: QPushButton) -> None:
        image = button.grab().toImage()
        # The fill is sampled above the text baseline and the border on the flat
        # part of the left edge, so neither reading catches a glyph or a corner.
        self._fill = QColor.fromRgba(image.pixel(button.width() // 2, 3))
        self._border = QColor.fromRgba(image.pixel(0, button.height() // 2))
        self._text = button.palette().color(QPalette.ColorRole.ButtonText)

    @property
    def fill(self) -> str:
        return self._fill.name(QColor.NameFormat.HexArgb)

    @property
    def border(self) -> str:
        return self._border.name(QColor.NameFormat.HexArgb)

    @property
    def text(self) -> str:
        return self._text.name()


def _opaque(value: str) -> str:
    """A theme colour as the ARGB name a fully opaque fill renders it under."""
    color = QColor(value)
    return color.name(QColor.NameFormat.HexArgb)


TRANSPARENT = "#00000000"


def _focus(qapp, button: QPushButton) -> None:
    """Give ``button`` real keyboard focus, not just a pending request.

    Under the offscreen platform the host window is not activated by ``show()``,
    so ``setFocus()`` alone leaves ``hasFocus()`` false and the ``:focus`` rule
    never engages.
    """
    button.window().activateWindow()
    button.setFocus()
    qapp.processEvents()
    assert button.hasFocus()


@pytest.fixture
def host(qtbot):
    """Show buttons in a dialog — the only place ``:default`` exists at all.

    The fixture, not the caller, keeps the dialog alive: ``qtbot.addWidget``
    registers a *weak* reference, so a locally-scoped host is collected mid-test
    and every ``grab()`` after it raises "C++ object has been deleted".
    """
    dialogs: list[QDialog] = []

    def _show(*buttons: QPushButton) -> QDialog:
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        for button in buttons:
            button.setMinimumSize(160, 40)
            layout.addWidget(button)
        qtbot.addWidget(dialog)
        dialog.resize(220, 80 * max(1, len(buttons)))
        dialog.show()
        dialogs.append(dialog)
        return dialog

    yield _show

    for dialog in dialogs:
        dialog.close()


class TestQuietByDefault:
    """An unmarked control carries no priority, so it may not paint accent."""

    def test_an_unmarked_button_does_not_fill_with_accent(self, host, colors):
        button = QPushButton("Browse…")
        button.setAutoDefault(False)
        host(button)

        assert Rendered(button).fill != _opaque(colors["primary"])

    def test_an_unmarked_button_uses_ordinary_text_and_border(self, host, colors):
        button = QPushButton("Browse…")
        button.setAutoDefault(False)
        host(button)
        rendered = Rendered(button)

        assert rendered.text == QColor(colors["text"]).name()
        assert rendered.border == _opaque(colors["border"])

    def test_secondary_renders_exactly_like_an_unmarked_button(self, host):
        plain, secondary = QPushButton("Cancel"), ModernButton("Cancel", variant="secondary")
        plain.setAutoDefault(False)
        host(plain, secondary)

        assert Rendered(secondary).fill == Rendered(plain).fill
        assert Rendered(secondary).border == Rendered(plain).border

    def test_ghost_drops_the_border_too(self, host):
        ghost = ModernButton("Clear", variant="ghost")
        host(ghost)

        assert Rendered(ghost).border == TRANSPARENT


class TestAccentIsReserved:
    """The five things accent is still allowed to mean."""

    def test_primary_fills_with_accent(self, host, colors):
        primary = ModernButton("Mine", variant="primary")
        host(primary)

        assert Rendered(primary).fill == _opaque(colors["primary"])
        assert Rendered(primary).text == QColor(colors["text-on-primary"]).name()

    @pytest.mark.parametrize("variant", ["secondary", "ghost", "danger"])
    def test_a_legitimate_qt_default_still_renders_primary(self, host, colors, variant):
        """Enter's target must look like Enter's target, whatever it was built as."""
        button = ModernButton("Close", variant=variant)
        host(button)
        button.setDefault(True)

        assert Rendered(button).fill == _opaque(colors["primary"])

    @pytest.mark.parametrize("variant", ["secondary", "ghost"])
    def test_a_checked_toggle_carries_the_accent(self, host, colors, variant):
        toggle = ModernButton("Folder", variant=variant)
        toggle.setCheckable(True)
        toggle.setChecked(True)
        host(toggle)

        assert Rendered(toggle).fill == _opaque(colors["primary"])

    @pytest.mark.parametrize("variant", ["secondary", "ghost"])
    def test_an_unchecked_toggle_does_not(self, host, colors, variant):
        toggle = ModernButton("Folder", variant=variant)
        toggle.setCheckable(True)
        host(toggle)

        assert Rendered(toggle).fill != _opaque(colors["primary"])

    def test_focus_rings_a_quiet_button_without_resizing_it(self, qapp, host, colors):
        quiet = ModernButton("Cancel", variant="secondary")
        host(quiet)
        before = quiet.size()

        _focus(qapp, quiet)

        assert Rendered(quiet).border == _opaque(colors["border-focus"])
        assert quiet.size() == before

    def test_focus_rings_a_filled_button_against_its_fill(self, qapp, host, colors):
        """``border-focus`` equals ``primary`` in 26 of the 29 shipped themes, so
        a ring drawn in it would vanish on the accent fill it sits on."""
        primary = ModernButton("Mine", variant="primary")
        host(primary)

        _focus(qapp, primary)

        assert Rendered(primary).border == _opaque(colors["text-on-primary"])


class TestRedMeansDestruction:
    """Solid red is worth something only if it is nearly never used."""

    def test_danger_is_an_outline_not_a_fill(self, host, colors):
        danger = ModernButton("Remove", variant="danger")
        host(danger)
        rendered = Rendered(danger)

        assert rendered.border == _opaque(colors["error"])
        assert rendered.fill != _opaque(colors["error"])

    def test_critical_is_solid(self, host, colors):
        critical = ModernButton("Delete", variant="critical")
        host(critical)

        assert Rendered(critical).fill == _opaque(colors["error"])

    def test_disabled_still_reads_as_unavailable(self, host, colors):
        button = ModernButton("Cancel", variant="secondary")
        button.setEnabled(False)
        host(button)

        assert Rendered(button).fill == _opaque(colors["disabled"])


class TestDialogDefaultHierarchy:
    """Enter must land on the task action, and never on destruction."""

    def test_primary_keeps_auto_default(self, host):
        primary = ModernButton("Mine", variant="primary")
        host(primary)

        assert primary.autoDefault() is True

    @pytest.mark.parametrize("variant", ["secondary", "ghost", "danger", "critical"])
    def test_other_variants_decline_it(self, host, variant):
        button = ModernButton("Cancel", variant=variant)
        host(button)

        assert button.autoDefault() is False

    def test_enter_lands_on_the_task_action_not_the_first_button(self, host):
        """The profile dialog's real button order: Enter used to mean "New…"."""
        new = ModernButton("New from Current…", variant="secondary")
        delete = ModernButton("Delete", variant="critical")
        switch = ModernButton("Switch To", variant="primary")
        host(new, delete, switch)

        assert [b.isDefault() for b in (new, delete, switch)] == [False, False, True]


class TestCallSiteRoles:
    """The classification itself, read out of the source."""

    def test_exactly_two_call_sites_are_critical(self):
        assert _roles_by_module()["critical"] == {
            ("known_words_dialog.py", "Reset User List"),
            ("profile_manager_dialog.py", "Delete"),
        }

    def test_no_cancel_or_stop_button_is_red(self):
        red = _roles_by_module()["danger"] | _roles_by_module()["critical"]
        stops = {(module, label) for module, label in red if label in {"Cancel", "Stop All"}}

        assert stops == {(D3_FROZEN, "Cancel")}

    @pytest.mark.parametrize(
        "module",
        [
            "backfill_tab.py",
            "condense_tab.py",
            "reading_manga_tab.py",
            "reading_novels_tab.py",
            "subtitle_creation_tab.py",
            "subtitle_retime_tab.py",
        ],
    )
    def test_each_screen_offers_a_single_primary_action(self, module):
        primaries = [label for label, variant in _button_roles(GUI_ROOT / "widgets" / module) if variant == "primary"]

        assert len(primaries) == 1, primaries

    def test_no_screen_anywhere_offers_two(self):
        offenders = {
            path.name: [label for label, variant in _button_roles(path) if variant == "primary"]
            for path in sorted((GUI_ROOT / "widgets").glob("*_tab.py"))
        }

        assert {name: labels for name, labels in offenders.items() if len(labels) > 1} == {}


# ------------------------------------------------------------- QSS accent map

#: The complete list of selectors allowed to spend the accent colour (D41).
ACCENT_ROLES = frozenset(
    {
        # One task action per screen — plus whatever Qt made the dialog default,
        # because that is the button Enter will press.
        "QPushButton#primary, QPushButton:default",
        "QPushButton#ghost:default, QPushButton#danger:default",
        "QPushButton#primary:hover, QPushButton:default:hover",
        "QPushButton#primary:pressed, QPushButton:default:pressed",
        # Checked controls.
        "QPushButton:checked, QPushButton#ghost:checked",
        "QPushButton:checked:hover, QPushButton#ghost:checked:hover",
        "QCheckBox::indicator:checked, QRadioButton::indicator:checked",
        # The navigation indicator.
        "QTabBar::tab:selected",
        "QListWidget#settings-nav::item:selected",
        # Progress.
        "QProgressBar::chunk",
    }
)

_ACCENT_TOKEN = re.compile(r"\$\{color-primary(?:-[a-z]+)?\}")


#: A rule body may itself contain braces, because every theme value is spelled
#: ``${color-…}``; a naive ``[^{}]*`` body silently splits the sheet in the wrong
#: places and makes the accent audit below read empty rules.
_RULE = re.compile(r"([^{}]+)\{((?:[^{}]|\$\{[^{}]*\})*)\}")


def _blocks(qss: str) -> list[tuple[str, str]]:
    """``(selector, declarations)`` for every rule in the sheet."""
    return [(" ".join(selector.split()), body) for selector, body in _RULE.findall(qss)]


def _rules_for(qss: str, selector: str) -> str:
    """Every declaration ``selector`` carries — geometry and colour halves both."""
    return "\n".join(body for found, body in _blocks(qss) if found == selector)


class TestAccentMap:
    def test_every_accent_declaration_belongs_to_an_approved_role(self, qss):
        spenders = {selector for selector, body in _blocks(qss) if _ACCENT_TOKEN.search(body)}

        assert spenders == ACCENT_ROLES

    def test_the_active_tab_keeps_the_underline_and_gives_back_the_text(self, qss):
        body = _rules_for(qss, "QTabBar::tab:selected")

        assert "border-bottom: 3px solid ${color-primary}" in body
        assert "color: ${color-text};" in body

    @pytest.mark.parametrize(
        ("selector", "declaration"),
        [
            ("QLabel#stat-value", "color: ${color-text};"),
            ("QLabel#queue-item-stats", "color: ${color-text-muted};"),
            ("QFrame#queue-item-card:hover", "border-color: ${color-border};"),
            ("QCheckBox::indicator:hover, QRadioButton::indicator:hover", "border-color: ${color-border};"),
        ],
    )
    def test_metadata_and_hover_use_ordinary_colours(self, qss, selector, declaration):
        assert declaration in _rules_for(qss, selector)

    def test_the_unused_accent_group_box_is_gone(self, qss):
        assert "QGroupBox#accent" not in qss

    def test_the_update_banner_states_its_colours_in_theme_keys(self, qss):
        banner = [body for selector, body in _blocks(qss) if "update-banner" in selector]

        assert banner
        assert not any("rgba(" in body for body in banner)


class TestEveryThemeStillResolves:
    def test_no_shipped_theme_leaves_a_button_token_unsubstituted(self):
        """The role rules add selectors, not colour keys — every theme must still
        substitute all of them without gaining a required key (D43-A)."""
        rendered = {
            name: re.sub(r"/\*.*?\*/", "", Theme.get_stylesheet(name), flags=re.DOTALL)
            for name in Theme.get_available_themes()
        }

        assert [name for name, sheet in rendered.items() if "${" in sheet] == []


# ------------------------------------------------------------------ scanning


def _literal(node: ast.AST) -> str | None:
    """The first string literal in ``node`` — unwraps ``self.tr("…")``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Call):
        for argument in node.args:
            found = _literal(argument)
            if found is not None:
                return found
    return None


def _button_roles(path: Path) -> list[tuple[str, str]]:
    """``(label, variant)`` for every ``ModernButton(...)`` built in ``path``."""
    roles: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ModernButton"):
            continue
        variant = "primary"
        if len(node.args) > 1:
            variant = _literal(node.args[1]) or variant
        for keyword in node.keywords:
            if keyword.arg == "variant":
                variant = _literal(keyword.value) or variant
        roles.append((_literal(node.args[0]) if node.args else "", variant))
    return roles


def _roles_by_module() -> dict[str, set[tuple[str, str]]]:
    """``variant -> {(module, label)}`` across the whole GUI package."""
    found: dict[str, set[tuple[str, str]]] = {"primary": set(), "secondary": set(), "ghost": set(), "danger": set()}
    found["critical"] = set()
    for path in sorted(GUI_ROOT.rglob("*.py")):
        for label, variant in _button_roles(path):
            found.setdefault(variant, set()).add((path.name, label))
    return found
