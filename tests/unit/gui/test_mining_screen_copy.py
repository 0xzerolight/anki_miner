"""The copy and variants the shared mining-screen builders put on each screen.

``MiningTabBase._build_offset_rows`` and ``_ListQueueMiningTabBase``'s
``_build_queue_actions`` / ``_build_progress_card`` take several same-typed
strings from each subclass. Swapping two of them type-checks and renders, so
these tests read every one back off the live widgets, per screen.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QBoxLayout, QLabel, QWidget

from anki_miner.gui.constants import SUBTITLE_OFFSET_MAX, SUBTITLE_OFFSET_MIN
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

_OFFSET_TIPS = {
    SingleEpisodeTab: "Adjust subtitle timing (positive = later, negative = earlier)",
    BatchProcessingTab: "Adjust subtitle timing for all episodes (positive = later, negative = earlier)",
    DeckBuilderTab: "Adjust subtitle timing for the whole season (positive = later, negative = earlier)",
}
_OFFSET_RANGE = (SUBTITLE_OFFSET_MIN, SUBTITLE_OFFSET_MAX, 0.5)
_TRANSLATION_TIP = "Shift the translation subtitles only (positive = later, negative = earlier)"


def _label_before(widget: QWidget) -> QLabel:
    """The field label laid out immediately before ``widget`` in its row."""
    pending = [widget.parentWidget().layout()]
    while pending:
        layout = pending.pop()
        index = layout.indexOf(widget)
        if index > 0:
            label = layout.itemAt(index - 1).widget()
            assert isinstance(label, QLabel)
            return label
        for i in range(layout.count()):
            child = layout.itemAt(i).layout()
            if isinstance(child, QBoxLayout):
                pending.append(child)
    raise AssertionError(f"no label before {widget!r}")


@pytest.mark.parametrize("cls", list(_OFFSET_TIPS), ids=lambda cls: cls.__name__)
def test_offset_rows_carry_each_screens_copy(qtbot, test_config, cls):
    tab = cls(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)

    offset = tab.offset_spinbox
    assert _label_before(offset).text() == "Subtitle Offset:"
    assert offset.suffix() == " seconds"
    assert offset.toolTip() == _OFFSET_TIPS[cls]
    assert (offset.minimum(), offset.maximum(), offset.singleStep()) == _OFFSET_RANGE
    assert offset.value() == test_config.subtitle_offset

    translation = tab.secondary_offset_spinbox
    translation_label = _label_before(translation)
    assert translation_label.text() == "Translation Offset:"
    assert translation_label.buddy() is translation
    assert translation.parentWidget() is tab.secondary_offset_row
    assert translation.suffix() == " seconds"
    assert translation.toolTip() == _TRANSLATION_TIP
    assert (translation.minimum(), translation.maximum(), translation.singleStep()) == _OFFSET_RANGE
    assert translation.value() == 0.0


def _audiobook(config):
    return AudiobookTab(config, MagicMock(), MagicMock())


def _youtube(config):
    return YouTubeTab(config, MagicMock(), MagicMock(), MagicMock())


@pytest.mark.parametrize(
    ("build", "mine_tip", "noun"),
    [
        (_audiobook, "Mine every queued item into Anki cards.", "audiobooks"),
        (_youtube, "Check every link in the box, then mine every Ready video.", "videos"),
    ],
    ids=["AudiobookTab", "YouTubeTab"],
)
def test_list_queue_controls_carry_each_screens_copy(qtbot, test_config, build, mine_tip, noun):
    tab = build(test_config)
    qtbot.addWidget(tab)

    buttons = {
        name: (button.text(), button.toolTip(), button.objectName())
        for name, button in (("mine", tab.mine_button), ("clear", tab.clear_button), ("stop", tab.stop_button))
    }
    assert buttons == {
        "mine": ("Mine", mine_tip, "primary"),
        "clear": ("Clear", "Remove every item from the queue.", "ghost"),
        "stop": ("Cancel", "Cancel the active run.", "secondary"),
    }

    card = tab.progress_widget.parentWidget()
    assert card.objectName() == "card"
    assert card.layout().itemAt(0).widget().title_label.text() == "Progress"
    assert tab._receipt_noun == noun
