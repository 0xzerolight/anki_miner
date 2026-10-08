"""A resource list fills its Settings page (Settings batch 2026-10-08, item 10).

Dictionaries, Word Audio, Frequency and Pitch Accent share one chain editor, and
each page ended in a trailing ``add_stretch()``. The stretch won every pixel of a
tall window, so the list sat at Qt's default 192px and scrolled five rows inside
a page of empty space. The list is now the page's grower: never shorter than it
was, no maximum. While the chain is empty the list is hidden, and a page filler
takes the surplus in its place so the headings do not inflate into bands.

"Inflate" is measured against the page itself rather than against each label's
size hint: ``QFormLayout`` deliberately gives a label up to 7/4 of its hint
beside a taller field (Dictionaries' "Storage Folder:"), and a word-wrapped
label's hint guesses its line count. What must hold is that extra window height
changes nothing on the page but the list, or the filler standing in for it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QLabel, QListWidget, QScrollArea, QWidget

from anki_miner.config import AudioSourceEntry, ChainEntry, FreqEntry, PitchSourceEntry
from anki_miner.gui.widgets.base.sizing import PageWidth, configure_scrolled_page
from anki_miner.gui.widgets.panels.audio_pack_settings_panel import AudioPackSettingsPanel
from anki_miner.gui.widgets.panels.chain_settings_panel_base import ChainSettingsPanelBase
from anki_miner.gui.widgets.panels.dictionary_settings_panel import DictionarySettingsPanel
from anki_miner.gui.widgets.panels.frequency_settings_panel import FrequencySettingsPanel
from anki_miner.gui.widgets.panels.pitch_settings_panel import PitchSettingsPanel

#: Every page built on the shared chain editor, with two rows for its list.
_PAGES = {
    "dictionaries": (
        DictionarySettingsPanel,
        (ChainEntry(kind="indexed", dict_id="a"), ChainEntry(kind="indexed", dict_id="b")),
    ),
    "word_audio": (AudioPackSettingsPanel, (AudioSourceEntry(kind="jpod101"), AudioSourceEntry(kind="googletts"))),
    "frequency": (FrequencySettingsPanel, (FreqEntry(source_id="a"), FreqEntry(source_id="b"))),
    "pitch_accent": (PitchSettingsPanel, (PitchSourceEntry(source_id="a"), PitchSourceEntry(source_id="b"))),
}

_WIDTH = 1000
#: Shorter than any of the four pages, so the page has to scroll.
_SHORT = 150
#: Taller than any of the four pages, so the page has surplus to hand out.
_TALL = 1000


@pytest.fixture(params=sorted(_PAGES))
def page(request, qtbot, tmp_path: Path):
    """One chain panel wrapped the way ``SettingsTab._wrap_in_scroll_area`` wraps it."""
    panel_cls, chain = _PAGES[request.param]
    panel = panel_cls(tmp_path)
    # The lazy first-show disk scan would swap the rows for a Loading row
    # mid-test; set_chain renders synchronously without it. Not under test.
    panel._scanned = True
    scroll = QScrollArea()
    configure_scrolled_page(scroll, panel, PageWidth.PAGE)
    qtbot.addWidget(scroll)
    return scroll, panel, chain


def _todays_height(qtbot) -> int:
    """What the list rendered at before it could grow: Qt's default list hint."""
    reference = QListWidget()
    qtbot.addWidget(reference)
    return reference.sizeHint().height()


def _show_at(qtbot, scroll: QScrollArea, height: int) -> None:
    scroll.resize(_WIDTH, height)
    scroll.show()
    qtbot.waitExposed(scroll)
    QApplication.processEvents()


def _chrome(panel: ChainSettingsPanelBase) -> list[tuple[str, int]]:
    """Every visible label's height: the page chrome no surplus may reach."""
    return [(label.text(), label.height()) for label in panel.findChildren(QLabel) if label.isVisible()]


def _chrome_without_surplus(qtbot, scroll: QScrollArea, panel: ChainSettingsPanelBase) -> list[tuple[str, int]]:
    """The chrome with the page exactly as tall as it asks to be."""
    _show_at(qtbot, scroll, panel.sizeHint().height())
    return _chrome(panel)


def _filler(panel: ChainSettingsPanelBase) -> QWidget:
    filler = getattr(panel, "_page_filler", None)
    assert filler is not None, "the page has no filler to stand in for a hidden list"
    return filler


def test_a_short_window_keeps_todays_height_and_scrolls_the_page(page, qtbot):
    """The floor: a short window scrolls the page, it does not squeeze the list."""
    scroll, panel, chain = page
    panel.set_chain(chain)
    _show_at(qtbot, scroll, _SHORT)

    today = _todays_height(qtbot)
    assert panel._list.minimumHeight() == today
    assert panel._list.height() >= today
    bar = scroll.verticalScrollBar()
    assert bar is not None and bar.maximum() > 0, "the page should scroll, not squeeze the list"


def test_the_list_takes_all_of_a_tall_pages_surplus(page, qtbot):
    """No maximum, and nothing else on the page competes for the height."""
    scroll, panel, chain = page
    panel.set_chain(chain)
    _show_at(qtbot, scroll, _SHORT)
    fitted_chrome = _chrome_without_surplus(qtbot, scroll, panel)
    fitted_list = panel._list.height()

    _show_at(qtbot, scroll, _TALL)
    assert panel._list.height() > _todays_height(qtbot), "a tall page left the list at its old height"
    assert panel._list.height() - fitted_list == _TALL - panel.sizeHint().height()
    assert not _filler(panel).isVisible()
    assert _chrome(panel) == fitted_chrome, "a tall window grew the page's chrome"


def test_an_empty_chain_hands_the_surplus_to_the_filler(page, qtbot):
    """The list hides when empty; the filler, not the headings, takes its place."""
    scroll, panel, _chain = page
    panel.set_chain(())
    _show_at(qtbot, scroll, _SHORT)
    fitted_chrome = _chrome_without_surplus(qtbot, scroll, panel)

    _show_at(qtbot, scroll, _TALL)
    assert panel._list.isHidden()
    filler = _filler(panel)
    assert filler.isVisible()
    assert filler.height() > 0
    assert _chrome(panel) == fitted_chrome, "the surplus landed on the page's headings"


def test_rows_arriving_take_the_height_back_from_the_filler(page, qtbot):
    """An import fills the chain: the filler stands down instead of splitting the surplus."""
    scroll, panel, chain = page
    panel.set_chain(())
    _show_at(qtbot, scroll, _TALL)

    panel.set_chain(chain)
    QApplication.processEvents()
    assert panel._list.isVisible()
    assert _filler(panel).isHidden()
    assert panel._list.height() > _todays_height(qtbot)

    tall_chrome = _chrome(panel)
    assert _chrome_without_surplus(qtbot, scroll, panel) == tall_chrome, "the refilled page grew its chrome"
