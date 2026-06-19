"""LanguagePanel: combo populated, set_language silent, activation emits."""

from anki_miner.gui.widgets.panels.language_panel import LanguagePanel


def test_combo_populated_from_available_languages(qtbot):
    panel = LanguagePanel("en")
    qtbot.addWidget(panel)
    codes = [panel.language_combo.itemData(i) for i in range(panel.language_combo.count())]
    assert "en" in codes


def test_set_language_does_not_emit(qtbot):
    panel = LanguagePanel("en")
    qtbot.addWidget(panel)
    with qtbot.assertNotEmitted(panel.language_changed):
        panel.set_language("en")


def test_activation_emits_code(qtbot):
    panel = LanguagePanel("en")
    qtbot.addWidget(panel)
    # Simulate the user picking the English row via the activated signal path.
    with qtbot.waitSignal(panel.language_changed) as blocker:
        panel._on_language_selected(panel.language_combo.currentIndex())
    assert blocker.args == ["en"]
