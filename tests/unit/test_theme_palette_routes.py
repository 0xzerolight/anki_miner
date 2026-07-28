"""The application palette is exact theme tokens, for every shipped theme.

``common.qss`` only reaches widgets it has a selector for. Combo popups, item
delegates, spin-box and scrollbar subcontrols, the disabled and inactive colour
groups and any dialog Qt builds itself read the palette instead — so a role that
is missing or wrong shows the platform's colour next to a themed widget on the
same screen.

Two things are pinned here and nowhere else:

* every role carries a value the theme author literally wrote — no blend, no
  nudge, no repair (decision D43-A);
* the routing covers all three colour groups, with Disabled reading the theme's
  own disabled tokens rather than a dimmed copy of the enabled ones.
"""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QPalette

from anki_miner.gui.resources.styles.theme import Theme

# role -> the token it must carry, in the Active/Inactive groups.
EXPECTED_SHARED = {
    QPalette.ColorRole.Window: "background",
    QPalette.ColorRole.WindowText: "text",
    QPalette.ColorRole.Base: "input-bg",
    QPalette.ColorRole.AlternateBase: "surface-alt",
    QPalette.ColorRole.Text: "text",
    QPalette.ColorRole.Button: "surface",
    QPalette.ColorRole.ButtonText: "text",
    QPalette.ColorRole.BrightText: "text-on-primary",
    QPalette.ColorRole.PlaceholderText: "text-muted",
    QPalette.ColorRole.ToolTipBase: "tooltip-bg",
    QPalette.ColorRole.ToolTipText: "tooltip-text",
    QPalette.ColorRole.Highlight: "table-selected-bg",
    QPalette.ColorRole.HighlightedText: "table-selected-text",
    QPalette.ColorRole.Accent: "primary",
}

EXPECTED_DISABLED = {
    QPalette.ColorRole.Base: "input-disabled-bg",
    QPalette.ColorRole.Button: "disabled",
    QPalette.ColorRole.Text: "text-disabled",
    QPalette.ColorRole.WindowText: "text-disabled",
    QPalette.ColorRole.ButtonText: "text-disabled",
    QPalette.ColorRole.PlaceholderText: "text-disabled",
}


@pytest.fixture(scope="module")
def shipped_keys(qapp) -> list[str]:
    Theme.initialize(active="light")
    keys = list(Theme.get_available_themes())
    assert len(keys) >= 29, f"expected the shipped gallery, got {len(keys)}"
    return keys


def _hex(colors: dict[str, str], key: str) -> str:
    from PyQt6.QtGui import QColor

    return QColor(colors[key]).name()


class TestEveryShippedTheme:
    def test_active_and_inactive_carry_the_authored_token(self, shipped_keys, qapp) -> None:
        for key in shipped_keys:
            palette = Theme.build_palette(key)
            colors = Theme.get_colors(key)
            for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
                for role, token in EXPECTED_SHARED.items():
                    assert palette.color(group, role).name() == _hex(colors, token), f"{key}: {role} should be {token}"

    def test_disabled_carries_the_theme_s_own_disabled_tokens(self, shipped_keys, qapp) -> None:
        for key in shipped_keys:
            palette = Theme.build_palette(key)
            colors = Theme.get_colors(key)
            for role, token in EXPECTED_DISABLED.items():
                assert palette.color(QPalette.ColorGroup.Disabled, role).name() == _hex(
                    colors, token
                ), f"{key}: disabled {role} should be {token}"

    def test_no_role_holds_a_colour_the_theme_never_wrote(self, shipped_keys, qapp) -> None:
        """The D43-A guard: a routed role is always one of the author's values."""
        for key in shipped_keys:
            palette = Theme.build_palette(key)
            authored = {_hex(Theme.get_colors(key), token) for token in Theme.get_colors(key)}
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                for role in EXPECTED_SHARED:
                    assert palette.color(group, role).name() in authored, f"{key}: {group}/{role} was invented"

    def test_selection_never_falls_back_to_the_platform(self, shipped_keys, qapp) -> None:
        """Decision D42: one selection colour, whichever view Qt is drawing."""
        default = QPalette()
        for key in shipped_keys:
            palette = Theme.build_palette(key)
            colors = Theme.get_colors(key)
            for group in (
                QPalette.ColorGroup.Active,
                QPalette.ColorGroup.Inactive,
                QPalette.ColorGroup.Disabled,
            ):
                assert palette.color(group, QPalette.ColorRole.Highlight).name() == _hex(colors, "table-selected-bg")
                if colors["table-selected-bg"].lower() not in {
                    default.color(group, QPalette.ColorRole.Highlight).name()
                }:
                    assert palette.color(group, QPalette.ColorRole.Highlight) != default.color(
                        group, QPalette.ColorRole.Highlight
                    )


@pytest.fixture
def app_appearance_restored(qapp):
    """Put the shared QApplication's stylesheet and palette back afterwards.

    Restore, don't re-theme. This file used to end its apply_to_app test by
    applying "light", which leaves a 38 KB themed stylesheet and its palette
    installed process-wide rather than clearing them -- so every widget a later
    test built was repainted by it. That is what made test_status_badge_motion
    read a themed surface where its own widget-scoped ``background: #ff0000``
    should have been, on whichever CI worker happened to get both files.
    """
    stylesheet = qapp.styleSheet()
    palette = QPalette(qapp.palette())
    yield
    qapp.setStyleSheet(stylesheet)
    qapp.setPalette(palette)


class TestApplyToApp:
    def test_apply_installs_the_built_palette(self, shipped_keys, qapp, app_appearance_restored) -> None:
        Theme.apply_to_app(qapp, "dark")
        expected = Theme.build_palette("dark")
        for role in EXPECTED_SHARED:
            assert qapp.palette().color(role) == expected.color(role)

    def test_a_theme_missing_an_optional_token_keeps_qt_s_value(self, qapp, monkeypatch) -> None:
        """User themes in ~/.anki_miner/themes must keep loading (D43-A note).

        A key the schema does not require is simply not routed; the app never
        substitutes a colour of its own for it.
        """
        colors = dict(Theme.get_colors("light"))
        colors.pop("tooltip-bg")
        monkeypatch.setattr(Theme, "get_colors", classmethod(lambda cls, mode=None: colors))

        palette = Theme.build_palette("light")

        assert palette.color(QPalette.ColorRole.ToolTipBase) == QPalette().color(QPalette.ColorRole.ToolTipBase)
