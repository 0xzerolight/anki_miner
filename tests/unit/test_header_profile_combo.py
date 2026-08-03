"""Header settings-profile combo: population, snap-back, and the wheel guard.

The re-entrancy tests here are load-bearing rather than decorative.
``ProfileController.switch_to`` calls ``set_profiles`` from a ``finally`` on
every terminal path, the success path included, so a ``currentIndexChanged``
escaping the rebuild re-enters ``switch_to`` before the first call has returned.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QFont, QWheelEvent
from PyQt6.QtWidgets import QApplication, QComboBox

from anki_miner.gui.resources.styles.theme import Theme
from anki_miner.gui.utils.profile_store import Profile
from anki_miner.gui.widgets.header_widget import MANAGE_PROFILES_SENTINEL, HeaderWidget

ANIME = Profile(id="anime", name="Anime")
NOVELS = Profile(id="novels", name="Novels")
MANGA = Profile(id="manga", name="Manga")


@pytest.fixture(autouse=True)
def reset_theme_state():
    """Pin the global Qt/Theme state the width assertions measure against.

    The combo's ``sizeHint`` is font-driven, and an app stylesheet left behind by
    any earlier test file in the same xdist worker changes the font of a
    *polished* widget — which is every visible one. Clearing it for the duration
    (and putting it back) keeps the widths reproducible whatever ran before.
    """
    Theme.initialize(active="light", favorites=("light", "dark"), user_dir=None, state_listener=None)
    app = QApplication.instance()
    previous = app.styleSheet() if isinstance(app, QApplication) else None
    if previous is not None:
        app.setStyleSheet("")
    yield
    if previous is not None:
        app.setStyleSheet(previous)


def _header(qtbot) -> HeaderWidget:
    header = HeaderWidget()
    qtbot.addWidget(header)
    return header


def _wheel_event(widget: QComboBox, degrees: int = -120) -> QWheelEvent:
    """Build a real QWheelEvent aimed at ``widget``'s centre.

    PyQt6 requires the real class here — a stub raises a SIP ``TypeError``.
    """
    pos = QPointF(widget.rect().center())
    return QWheelEvent(
        pos,
        widget.mapToGlobal(widget.rect().center()).toPointF(),
        QPoint(0, 0),
        QPoint(0, degrees),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


# ----------------------------------------------------------------------
# Visibility
# ----------------------------------------------------------------------


def test_a_fresh_header_hides_the_profile_block(qtbot):
    """Nothing has populated the combo yet, so the header looks unchanged."""
    header = _header(qtbot)

    assert header.profile_label.isHidden()
    assert header.profile_combo.isHidden()


def test_one_profile_shows_the_block(qtbot):
    """The picker is how the feature is discovered, so one profile is enough.

    ``bootstrap`` adopts the live config as a single "Default" profile, so under
    the old two-profile rule a user who never hand-created a second one never saw
    the picker at all.
    """
    header = _header(qtbot)

    header.set_profiles([ANIME], "anime")

    assert not header.profile_label.isHidden()
    assert not header.profile_combo.isHidden()


def test_two_profiles_show_the_block(qtbot):
    header = _header(qtbot)

    header.set_profiles([ANIME, NOVELS], "anime")

    assert not header.profile_label.isHidden()
    assert not header.profile_combo.isHidden()


def test_dropping_back_to_one_profile_keeps_the_block(qtbot):
    """Idempotent both ways: deleting the second profile leaves the picker up."""
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")

    header.set_profiles([ANIME], "anime")

    assert not header.profile_label.isHidden()
    assert not header.profile_combo.isHidden()


def test_an_empty_sequence_is_safe(qtbot):
    """Empty means the store could not be enumerated, not "one profile"."""
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")

    header.set_profiles((), None)

    assert header.profile_label.isHidden()
    assert header.profile_combo.isHidden()
    # Only the sentinel is left, and it is never reported as a profile change.
    assert header.profile_combo.count() == 1
    assert header.profile_combo.itemData(0) == MANAGE_PROFILES_SENTINEL


# ----------------------------------------------------------------------
# Population
# ----------------------------------------------------------------------


def test_items_carry_names_as_text_and_ids_as_data(qtbot):
    header = _header(qtbot)

    header.set_profiles([ANIME, NOVELS], "anime")
    combo = header.profile_combo

    assert combo.itemText(0) == "Anime"
    assert combo.itemData(0) == "anime"
    assert combo.itemText(1) == "Novels"
    assert combo.itemData(1) == "novels"
    # Sentinel last, after the real profiles.
    assert combo.itemData(combo.count() - 1) == MANAGE_PROFILES_SENTINEL


def test_set_profiles_selects_the_active_id(qtbot):
    header = _header(qtbot)

    header.set_profiles([ANIME, NOVELS], "novels")

    assert header.profile_combo.currentData() == "novels"


@pytest.mark.parametrize(
    "active_id",
    [
        pytest.param(None, id="no-active-id"),
        pytest.param("deleted-outside-the-app", id="id-matching-no-item"),
    ],
)
def test_an_unattributable_session_selects_nothing(qtbot, active_id):
    """Neither case may DISPLAY a profile as active.

    Both are reachable: ``ProfileController._reconcile`` returns
    ``(profiles, None)`` when the live config cannot be attributed and the
    recovery create fails (e.g. at MAX_PROFILES), and any ``_sync_header`` after
    the active profile's file is deleted outside the app carries an id matching
    no item.
    """
    header = _header(qtbot)

    header.set_profiles([ANIME, NOVELS], active_id)

    assert header.profile_combo.currentIndex() == -1
    assert header.profile_combo.currentData() is None
    assert header.profile_combo.currentText() == ""


@pytest.mark.parametrize("active_id", [None, "deleted-outside-the-app"])
def test_an_unattributable_session_leaves_every_profile_reachable(qtbot, active_id):
    """The regression: a DISPLAYED-but-not-active profile is unclickable.

    Landing on index 0 put the combo on the profile it claimed was active, so
    clicking that entry changed no index, emitted no ``currentIndexChanged``,
    and the controller was never asked to switch — the one profile the header
    named was the one profile the user could not select.
    """
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], active_id)

    with qtbot.waitSignal(header.profile_changed) as blocker:
        header.profile_combo.setCurrentIndex(0)

    assert blocker.args == ["anime"]


# ----------------------------------------------------------------------
# Signals
# ----------------------------------------------------------------------


def test_selecting_a_profile_emits_profile_changed_with_its_id(qtbot):
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")

    with qtbot.waitSignal(header.profile_changed) as blocker:
        header.profile_combo.setCurrentIndex(1)

    assert blocker.args == ["novels"]


def test_selecting_the_sentinel_opens_the_manager_without_a_profile_change(qtbot):
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")
    combo = header.profile_combo
    sentinel_index = combo.findData(MANAGE_PROFILES_SENTINEL)

    with qtbot.assertNotEmitted(header.profile_changed), qtbot.waitSignal(header.open_profile_manager):
        combo.setCurrentIndex(sentinel_index)

    # Snapped back, so the sentinel never sits "selected" in the closed combo.
    assert combo.currentData() == "anime"


def test_set_profiles_emits_nothing_even_when_it_moves_the_selection(qtbot):
    """The re-entrancy contract ProfileController's ``finally`` depends on."""
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")
    assert header.profile_combo.currentIndex() == 0

    with qtbot.assertNotEmitted(header.profile_changed), qtbot.assertNotEmitted(header.open_profile_manager):
        header.set_profiles([ANIME, NOVELS], "novels")

    # Vacuity guard: the call really did move the selection.
    assert header.profile_combo.currentIndex() == 1


def test_set_profiles_snaps_the_combo_back_after_a_refused_switch(qtbot):
    """``currentIndexChanged`` has already moved the combo when a switch is refused."""
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")
    header.profile_combo.setCurrentIndex(1)
    assert header.profile_combo.currentData() == "novels"

    # The controller refused and re-syncs the header to what is actually live.
    with qtbot.assertNotEmitted(header.profile_changed):
        header.set_profiles([ANIME, NOVELS], "anime")

    assert header.profile_combo.currentIndex() == 0
    assert header.profile_combo.currentData() == "anime"


def test_the_sentinel_snaps_back_to_the_live_profile_not_the_clicked_one(qtbot):
    """A selection is a request, not an outcome — ``set_profiles`` is the truth."""
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")
    # User picks Novels; nothing applied it (a refusal, or no wiring yet).
    header.profile_combo.setCurrentIndex(1)

    with qtbot.waitSignal(header.open_profile_manager):
        header.profile_combo.setCurrentIndex(header.profile_combo.findData(MANAGE_PROFILES_SENTINEL))

    assert header.profile_combo.currentData() == "anime"


# ----------------------------------------------------------------------
# Accessibility
# ----------------------------------------------------------------------


def test_the_profile_combo_is_named_for_screen_readers(qtbot):
    header = _header(qtbot)

    assert header.profile_combo.accessibleName()
    assert header.profile_combo.accessibleDescription()
    assert header.profile_label.buddy() is header.profile_combo


def test_the_theme_combo_is_named_for_screen_readers(qtbot):
    header = _header(qtbot)

    assert header.theme_combo.accessibleName()


# ----------------------------------------------------------------------
# Long names
# ----------------------------------------------------------------------


def test_a_long_profile_name_does_not_widen_the_combo(qtbot):
    header = _header(qtbot)
    long_name = "X" * 200
    combo = header.profile_combo

    # Baseline taken in the SAME visibility state: a hidden combo is unpolished,
    # so under an app stylesheet it reports a different (stale-font) hint.
    header.set_profiles([ANIME, NOVELS], "anime")
    baseline = combo.sizeHint().width()

    header.set_profiles([Profile(id="long", name=long_name), NOVELS], "long")

    # Font-independent, so it holds at every ui_font_scale: the hint does not
    # depend on the items at all.
    assert combo.sizeHint().width() == baseline
    assert combo.maximumWidth() >= combo.sizeHint().width()


def test_the_width_cap_tracks_the_ui_font_instead_of_clamping_it(qtbot):
    """The cap is a CHARACTER budget measured in the combo's current font.

    Measured with the flat 220px it replaces: the combo's own 12-character hint
    is 160px at ui_font_scale 1.0 but 256px at 2.0, so the cap clamped the combo
    below the width it was sized for exactly when the user asked for bigger text.
    """
    header = _header(qtbot)
    combo = header.profile_combo
    font = QFont(combo.font())

    font.setPixelSize(12)
    combo.setFont(font)
    header.set_profiles([ANIME, NOVELS], "anime")
    small_hint, small_cap = combo.sizeHint().width(), combo.maximumWidth()

    font.setPixelSize(28)
    combo.setFont(font)
    header.set_profiles([ANIME, NOVELS], "anime")
    large_hint, large_cap = combo.sizeHint().width(), combo.maximumWidth()

    # Vacuity guard: the bigger font really did move the combo's own demand.
    assert large_hint > small_hint
    assert large_cap > small_cap
    # The property the flat cap violated: it never clamps the combo's own hint.
    assert small_cap >= small_hint
    assert large_cap >= large_hint


def test_a_long_profile_name_is_elided_but_kept_whole_in_the_tooltip(qtbot):
    header = _header(qtbot)
    long_name = "X" * 200

    header.set_profiles([Profile(id="long", name=long_name), NOVELS], "long")
    combo = header.profile_combo

    assert combo.itemText(0) != long_name
    assert len(combo.itemText(0)) < len(long_name)
    # Nothing is lost: the full name on the tooltip, the id in itemData.
    assert combo.itemData(0, Qt.ItemDataRole.ToolTipRole) == long_name
    assert combo.itemData(0) == "long"


def test_the_closed_combo_tooltip_leads_with_the_active_profiles_full_name(qtbot):
    """ToolTipRole only surfaces in the popup, so the widget tooltip is the
    only place a long active name is readable without opening the drop-down."""
    header = _header(qtbot)
    long_name = "X" * 200

    header.set_profiles([Profile(id="long", name=long_name), NOVELS], "long")

    tooltip = header.profile_combo.toolTip()
    assert tooltip.startswith(long_name)
    # The generic explanation is kept, not replaced.
    assert "Manage profiles…" in tooltip


def test_the_tooltip_falls_back_to_the_explanation_with_nothing_active(qtbot):
    header = _header(qtbot)

    header.set_profiles([ANIME, NOVELS], None)

    assert header.profile_combo.toolTip() == header._profile_tooltip


def test_the_tooltip_follows_the_active_profile(qtbot):
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS], "anime")
    assert header.profile_combo.toolTip().startswith("Anime")

    header.set_profiles([ANIME, NOVELS], "novels")

    assert header.profile_combo.toolTip().startswith("Novels")


# ----------------------------------------------------------------------
# Wheel guard
# ----------------------------------------------------------------------


def test_wheel_over_the_unfocused_profile_combo_changes_nothing(qtbot):
    """Issue #99's hazard with the worst payload: a scroll swapping every setting."""
    header = _header(qtbot)
    header.set_profiles([ANIME, NOVELS, MANGA], "anime")
    combo = header.profile_combo

    assert not combo.hasFocus()
    before = combo.currentIndex()

    # Vacuity guard. `count() >= 2` would not be enough: if the item the wheel
    # lands on were the sentinel, the snap-back handler would restore the index
    # and emit open_profile_manager instead of profile_changed, so both
    # assertions below would pass even with the guard removed.
    assert combo.itemData(before + 1) not in (None, MANAGE_PROFILES_SENTINEL)

    with qtbot.assertNotEmitted(header.profile_changed), qtbot.assertNotEmitted(header.open_profile_manager):
        QApplication.sendEvent(combo, _wheel_event(combo))

    assert combo.currentIndex() == before


def test_the_profile_combo_is_a_child_of_the_header(qtbot):
    """``install_no_scroll_on_inputs`` sweeps ``findChildren``, which only sees
    the combo once ``setLayout`` has reparented it."""
    header = _header(qtbot)

    assert header.profile_combo in header.findChildren(QComboBox)
