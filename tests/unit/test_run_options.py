"""RunOptionsMixin: the seed guard and the persist-once contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.run_options import RunOptionsMixin


class _Screen(RunOptionsMixin, QWidget):
    run_options_changed = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.config = AnkiMinerConfig()


@pytest.fixture
def screen(qtbot):
    widget = _Screen()
    qtbot.addWidget(widget)
    return widget


def test_a_real_change_is_adopted_and_emitted_once(screen):
    seen: list[AnkiMinerConfig] = []
    screen.run_options_changed.connect(seen.append)

    assert screen.persist_run_options(review_words_before_mining=True) is True

    assert len(seen) == 1
    assert seen[0].review_words_before_mining is True
    # The screen adopts its own new config so the next compare is against it.
    assert screen.config.review_words_before_mining is True


def test_an_unchanged_value_emits_nothing(screen):
    seen: list[AnkiMinerConfig] = []
    screen.run_options_changed.connect(seen.append)

    assert screen.persist_run_options(review_words_before_mining=False) is False

    assert seen == []


def test_nothing_is_emitted_while_seeding(screen):
    """The programmatic setChecked in a re-seed must not write back."""
    seen: list[AnkiMinerConfig] = []
    screen.run_options_changed.connect(seen.append)

    with screen.seeding():
        assert screen.persist_run_options(review_words_before_mining=True) is False

    assert seen == []
    assert screen.config.review_words_before_mining is False


def test_the_seed_guard_is_released_even_when_the_body_raises(screen):
    with pytest.raises(RuntimeError), screen.seeding():
        raise RuntimeError("boom")
    assert screen._seeding is False
    assert screen.persist_run_options(review_words_before_mining=True) is True


def test_nested_seeding_stays_guarded_until_the_outermost_exit(screen):
    """update_config seeds, and the gate refresh it calls seeds again."""
    with screen.seeding():
        with screen.seeding():
            pass
        assert screen._seeding is True
    assert screen._seeding is False


def test_a_screen_whose_config_moved_underneath_still_compares_against_it(screen):
    screen.config = replace(screen.config, review_words_before_mining=True)
    assert screen.persist_run_options(review_words_before_mining=True) is False
