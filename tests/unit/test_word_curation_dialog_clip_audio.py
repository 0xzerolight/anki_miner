"""Word Curator on an Anki-deck run: play the focused card's own audio clip.

Deck units carry ``audio_ref`` (the card's clip in collection.media). The image
pane gains a "Play card audio" button; the play/pause key plays it too.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
from anki_miner.gui.widgets.dialogs.word_curation_dialog import CurationMediaContext, WordCurationDialog
from anki_miner.models import TokenizedWord
from anki_miner.models.reading import ImageRef, ReadingUnit


def _make_word(lemma: str, unit_index: int) -> TokenizedWord:
    # Reading-path words carry the unit index as a dummy start_time.
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="よみ",
        sentence=f"{lemma}のテスト",
        start_time=float(unit_index),
        end_time=float(unit_index),
        duration=0.0,
        pos="名詞",
    )


def _context(units: dict[int, ReadingUnit]) -> CurationMediaContext:
    return CurationMediaContext(video_file=None, subtitle_entries=[], page_units=units)


def _focus_word(dialog: WordCurationDialog, index: int) -> None:
    row = dialog._visual_row_for_index(index)
    assert row is not None
    dialog.table.setCurrentCell(row, 0)
    dialog._on_row_focus_changed()
    dialog._focus_timer.stop()
    dialog._on_focus_timer_fired()


class _FakeClipPlayer:
    instances: list[_FakeClipPlayer] = []

    def __init__(self) -> None:
        self.played: list[object] = []
        self.stopped = 0
        self.released = 0
        _FakeClipPlayer.instances.append(self)

    def play(self, path) -> None:
        self.played.append(path)

    def stop(self) -> None:
        self.stopped += 1

    def release(self) -> None:
        self.released += 1


@pytest.fixture
def fake_clip_player(monkeypatch):
    _FakeClipPlayer.instances = []
    monkeypatch.setattr(wcd, "ClipAudioPlayer", _FakeClipPlayer)
    monkeypatch.setattr(wcd, "mpv_available", lambda: True)
    # The page decode is not under test; keep it off real threads.
    monkeypatch.setattr(wcd, "run_off_thread", lambda *a, **k: MagicMock())
    return _FakeClipPlayer.instances


@pytest.fixture
def deck_units(tmp_path):
    clip = tmp_path / "a.mp3"
    clip.write_bytes(b"ID3")
    units = {
        0: ReadingUnit("猫", 0, "#1", audio_ref=clip),
        1: ReadingUnit("犬", 1, "#2"),
    }
    return units, clip


def test_play_button_plays_the_focused_cards_clip(qtbot, fake_clip_player, deck_units):
    units, clip = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0), _make_word("犬", 1)], media_context=_context(units))
    qtbot.addWidget(dialog)

    _focus_word(dialog, 0)
    assert dialog.play_clip_button.isEnabled()
    dialog.play_clip_button.click()

    assert fake_clip_player[0].played == [clip]


def test_a_card_without_audio_disables_the_button_and_stops_playback(qtbot, fake_clip_player, deck_units):
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0), _make_word("犬", 1)], media_context=_context(units))
    qtbot.addWidget(dialog)
    _focus_word(dialog, 0)
    stops_before = fake_clip_player[0].stopped

    _focus_word(dialog, 1)

    assert not dialog.play_clip_button.isEnabled()
    assert fake_clip_player[0].stopped > stops_before


def test_the_play_pause_key_action_plays_the_clip(qtbot, fake_clip_player, deck_units):
    units, clip = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)
    _focus_word(dialog, 0)

    dialog._toggle_play_pause()

    assert fake_clip_player[0].played == [clip]


def test_no_clip_anywhere_means_no_button(qtbot, fake_clip_player):
    units = {0: ReadingUnit("猫", 0, "#1")}
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "play_clip_button")
    assert fake_clip_player == []


def test_without_libmpv_there_is_no_button(qtbot, fake_clip_player, deck_units, monkeypatch):
    monkeypatch.setattr(wcd, "mpv_available", lambda: False)
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)

    assert not hasattr(dialog, "play_clip_button")


def test_closing_releases_the_core(qtbot, fake_clip_player, deck_units):
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)

    dialog._stop_player()

    assert fake_clip_player[0].released == 1


def test_the_pane_keeps_its_saved_layout_key(qtbot, fake_clip_player, deck_units):
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)

    assert dialog._side_key.split("+")[0] == "image"
    assert "image" in dialog._side_key.split("+")


def test_clicking_play_leaves_the_keys_with_the_table(qtbot, fake_clip_player, deck_units):
    """Every curator key but confirm is scoped to the table: a click that took
    focus would leave include/mark-known/next-word dead until a click back."""
    units, clip = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    _focus_word(dialog, 0)
    dialog.table.setFocus()

    qtbot.mouseClick(dialog.play_clip_button, Qt.MouseButton.LeftButton)

    assert fake_clip_player[0].played == [clip]
    assert dialog.focusWidget() is dialog.table


def test_a_deck_run_names_the_play_key_on_the_hint_line(qtbot, fake_clip_player, deck_units):
    """The button no longer takes focus, so the key that plays the clip has to be findable."""
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_context(units))
    qtbot.addWidget(dialog)

    assert "play/pause" in dialog.key_hint_label.text()


def _deck_context(units: dict[int, ReadingUnit]) -> CurationMediaContext:
    return CurationMediaContext(video_file=None, subtitle_entries=[], page_units=units, page_units_are_cards=True)


def test_a_card_without_a_picture_says_so_in_card_words(qtbot, fake_clip_player, deck_units):
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0), _make_word("犬", 1)], media_context=_deck_context(units))
    qtbot.addWidget(dialog)

    _focus_word(dialog, 1)

    assert dialog.page_image_view.current_message == "This card has no picture"


def test_a_card_picture_that_fails_to_load_says_so_in_card_words(qtbot, tmp_path, monkeypatch):
    units = {0: ReadingUnit("猫", 0, "#1", image_ref=ImageRef(tmp_path / "card.jpg"))}
    monkeypatch.setattr(wcd, "run_off_thread", lambda parent, work, on_done, on_error=None, **kw: on_error("boom"))
    dialog = WordCurationDialog([_make_word("猫", 0)], media_context=_deck_context(units))
    qtbot.addWidget(dialog)

    _focus_word(dialog, 0)

    assert dialog.page_image_view.current_message == "Could not load this card's picture"


def test_a_manga_page_keeps_its_page_wording(qtbot, fake_clip_player, deck_units):
    units, _ = deck_units
    dialog = WordCurationDialog([_make_word("猫", 0), _make_word("犬", 1)], media_context=_context(units))
    qtbot.addWidget(dialog)

    _focus_word(dialog, 1)

    assert dialog.page_image_view.current_message == "No page image for this word"
