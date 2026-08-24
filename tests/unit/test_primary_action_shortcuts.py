"""Every workflow screen runs its main action from the keyboard (D48-B, D49).

Under D48-B the keyboard work is essentials only, and this is one of the four:
each screen's primary action answers ``Ctrl+Enter``. D49 fixes the shape of that
binding -- ``Ctrl+Return`` and the keypad's ``Ctrl+Enter`` together, never a bare
Return, because Return is how a Japanese input method commits kana.

Two defects are pinned here rather than described:

* Single and Batch both owned ``Ctrl+O`` and ``Ctrl+Return`` in one window with
  no context set, so the binding on the *hidden* page could win.
* Both wired ``Ctrl+Return`` straight to the worker-start slot, bypassing the
  button's enabled state -- so the shortcut could start a second run over a
  first.
"""

from __future__ import annotations

from contextlib import suppress
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.base import WorkflowActionBar
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

#: The two sequences ``primary_action_shortcut`` installs, as portable text.
PRIMARY_KEYS = {"Ctrl+Return", "Ctrl+Enter"}


def _presenter() -> MagicMock:
    presenter = MagicMock(name="Presenter")
    for signal in ("info_signal", "success_signal", "warning_signal", "error_signal"):
        getattr(presenter, signal).connect = MagicMock()
    return presenter


def _progress_callback() -> MagicMock:
    callback = MagicMock(name="ProgressCallback")
    for signal in ("stage_signal", "start_signal", "progress_signal", "complete_signal", "error_signal"):
        getattr(callback, signal).connect = MagicMock()
    return callback


def _build(name: str, config: AnkiMinerConfig) -> QWidget:
    """Construct one workflow screen with its collaborators stubbed."""
    if name == "single":
        return SingleEpisodeTab(config, _presenter(), _progress_callback())
    if name == "batch":
        return BatchProcessingTab(config, _presenter(), _progress_callback())
    if name == "youtube":
        return YouTubeTab(config, MagicMock(name="Processor"), MagicMock(name="Fetcher"), MagicMock())
    if name == "audiobook":
        return AudiobookTab(config, MagicMock(name="Processor"), MagicMock())
    if name in {"condense", "generate", "retime", "download"}:
        tool = {
            "condense": CondenseTab,
            "generate": SubtitleCreationTab,
            "retime": SubtitleRetimeTab,
            "download": DownloadTab,
        }[name]
        return tool(config)
    reading = {
        "manga": ReadingMangaTab,
        "novels": ReadingNovelsTab,
        "subtitles": ReadingSubtitlesTab,
        "text": ReadingTextTab,
    }[name]
    return reading(config, MagicMock(name="Processor"), MagicMock())


#: Every screen that pins a primary action. Deck Builder is deliberately absent:
#: under D3 it is blocked and installs no bar, so it gains no binding here.
SCREENS = [
    "single",
    "batch",
    "youtube",
    "audiobook",
    "manga",
    "novels",
    "subtitles",
    "text",
    "condense",
    "generate",
    "retime",
    "download",
]


@pytest.fixture(params=SCREENS)
def screen(request, qtbot, test_config: AnkiMinerConfig):
    with patch("anki_miner.gui.utils.service_factory.create_episode_processor", MagicMock()):
        widget = _build(request.param, test_config)
    qtbot.addWidget(widget)
    return request.param, widget


def _shortcuts(widget: QWidget) -> list[QShortcut]:
    return widget.findChildren(QShortcut)


def _keys(shortcut: QShortcut) -> str:
    return shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)


def _bar(widget: QWidget) -> WorkflowActionBar:
    bars = widget.findChildren(WorkflowActionBar)
    assert len(bars) == 1, "a screen has exactly one action host"
    return bars[0]


class TestEveryScreenBindsItsPrimaryAction:
    def test_both_return_variants_are_bound_exactly_once(self, screen):
        """Ctrl+Return and the keypad's Ctrl+Enter, one binding each."""
        _name, widget = screen
        bound = [_keys(s) for s in _shortcuts(widget) if _keys(s) in PRIMARY_KEYS]

        assert sorted(bound) == sorted(PRIMARY_KEYS), f"expected one of each, got {bound}"

    def test_the_binding_is_scoped_to_the_screen(self, screen):
        """Window scope is what lets a hidden page answer for the visible one."""
        _name, widget = screen
        for shortcut in _shortcuts(widget):
            if _keys(shortcut) in PRIMARY_KEYS:
                assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut

    def test_no_bare_return_binding_exists(self, screen):
        """Bare Return belongs to the input method, always (D49)."""
        _name, widget = screen
        bare = [_keys(s) for s in _shortcuts(widget) if _keys(s) in {"Return", "Enter"}]

        assert bare == [], f"bare Return binding would eat kana commits: {bare}"


def _isolated_primary(bar: WorkflowActionBar) -> tuple[object, list[int]]:
    """The bar's primary button, cut loose from the run it would really start.

    Every existing ``clicked`` receiver is dropped first. Without that, pressing
    the button launches the screen's actual worker -- Batch's Process Queue took
    a pytest-xdist worker down with it -- and the assertion would be paid for by
    a real run rather than by observing the press.
    """
    primary = bar.current_primary()
    assert primary is not None, "every screen in this suite pins a primary action"
    # TypeError == nothing was connected; the button is already inert.
    with suppress(TypeError):
        primary.clicked.disconnect()
    presses: list[int] = []
    primary.clicked.connect(lambda: presses.append(1))
    return primary, presses


class TestActivationGoesThroughTheButton:
    def test_it_clicks_the_screens_own_primary_button(self, screen):
        """Not the worker-start slot: the button is what knows it is enabled."""
        _name, widget = screen
        bar = _bar(widget)
        primary, presses = _isolated_primary(bar)
        primary.setEnabled(True)

        bar.trigger_primary()

        assert presses == [1]

    def test_a_disabled_primary_does_nothing(self, screen):
        """A run in flight disables the button; the shortcut must not restart it."""
        _name, widget = screen
        bar = _bar(widget)
        primary, presses = _isolated_primary(bar)
        primary.setEnabled(False)

        bar.trigger_primary()

        assert presses == []


class TestDialogsWithTextFieldsNeverConfirmOnBareReturn:
    """D49's structural floor, applied to the dialogs that carry a text field.

    Qt promotes the first button whose ``autoDefault`` is on to the dialog's
    Enter target. ``ModernButton`` already declines it for every quiet variant,
    which leaves the *primary* one -- and Export was exactly that: a path field
    beside an auto-default Export button, so committing kana in the field ran
    the export.
    """

    @pytest.fixture
    def export_dialog(self, qtbot, test_config: AnkiMinerConfig):
        from anki_miner.gui.widgets.dialogs.export_dialog import ExportDialog

        dialog = ExportDialog(words=[], config=test_config)
        qtbot.addWidget(dialog)
        return dialog

    def test_no_button_is_the_enter_target(self, export_dialog):
        from PyQt6.QtWidgets import QLineEdit, QPushButton

        assert export_dialog.findChildren(QLineEdit), "this test is only meaningful with a text field present"

        promoted = [b.text() for b in export_dialog.findChildren(QPushButton) if b.autoDefault() or b.isDefault()]

        assert promoted == [], f"bare Return would fire {promoted} while an input method is composing"

    def test_confirmation_is_ctrl_enter_instead(self, export_dialog):
        bound = {_keys(s) for s in _shortcuts(export_dialog)}

        assert bound >= PRIMARY_KEYS, f"no Ctrl+Enter confirmation to replace the default button: {bound}"

    def test_ctrl_enter_respects_the_disabled_primary(self, export_dialog, qtbot):
        """Export is disabled until a path is chosen; the shortcut must agree."""
        exported: list[int] = []
        export_dialog._export_btn.clicked.connect(lambda: exported.append(1))
        export_dialog._export_btn.setEnabled(False)
        export_dialog.show()
        qtbot.waitExposed(export_dialog)

        qtbot.keyClick(export_dialog, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)

        assert exported == []


class TestHiddenPagesStaySilent:
    """Single and Batch live in one window, and both own Ctrl+O and Ctrl+Enter.

    Scope is the whole mechanism here, so scope is what is asserted. That a
    ``WidgetWithChildrenShortcut`` really does stay silent for a hidden sibling
    is proven behaviourally, with real key events, against light widgets in
    ``tests/unit/gui/test_keyboard_shortcuts.py``; repeating it here would mean
    standing up two of the application's heaviest pages to re-test Qt.
    """

    @pytest.fixture(params=["single", "batch"])
    def video_page(self, request, qtbot, test_config: AnkiMinerConfig):
        with patch("anki_miner.gui.utils.service_factory.create_episode_processor", MagicMock()):
            widget = _build(request.param, test_config)
        qtbot.addWidget(widget)
        return request.param, widget

    @pytest.mark.parametrize("sequence", ["Ctrl+O", "Ctrl+Return", "Ctrl+Enter"])
    def test_every_shared_sequence_is_scoped_to_its_page(self, video_page, sequence):
        name, widget = video_page
        matching = [s for s in _shortcuts(widget) if _keys(s) == sequence]

        assert matching, f"{name} does not bind {sequence}"
        for shortcut in matching:
            assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut, (
                f"{sequence} on {name} is window-scoped, so it can answer "
                "while the other video page is the one on screen"
            )

    def test_the_two_pages_do_not_share_a_shortcut_owner(self, qtbot, test_config: AnkiMinerConfig):
        """Each binding is parented to its own page, which is what scopes it."""
        with patch("anki_miner.gui.utils.service_factory.create_episode_processor", MagicMock()):
            single = _build("single", test_config)
            batch = _build("batch", test_config)
        qtbot.addWidget(single)
        qtbot.addWidget(batch)

        for page in (single, batch):
            for shortcut in _shortcuts(page):
                if _keys(shortcut) in {"Ctrl+O", *PRIMARY_KEYS}:
                    owner = shortcut.parent()
                    assert owner is page or page.isAncestorOf(owner), (
                        f"{_keys(shortcut)} is parented outside its own page, "
                        "so its scope is not the page it belongs to"
                    )
