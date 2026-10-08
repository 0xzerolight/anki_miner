"""Tests for the Anki Deck sub-tab of the Reading tab (Issue #131).

``ReadingDeckTab`` mines an existing Anki deck (subs2srs and the like) as one
ephemeral ``ReadingQueueItem`` carrying a pathless ``kind="deck"`` ref.
Behaviour under test:

* Picking a deck inspects it off the GUI thread and pre-selects the detected
  sentence / audio / picture / translation fields; a stale inspection is ignored.
* Mine needs a deck and a sentence field, launches one deck item, and warns (but
  still mines) when the note type has nowhere to put the deck's media.
* The curation context hands the dialog the deck units (picture + clip).

Qt threads are never started: ``ReadingQueueWorker`` is patched at the base
module, and ``run_off_thread`` is replaced with a synchronous or deferred stub.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets import reading_deck_tab as tab_module
from anki_miner.gui.widgets.reading_deck_tab import ReadingDeckTab
from anki_miner.models.reading import DeckFieldMap, ReadingDocument, ReadingUnit
from anki_miner.services.deck_filter import DeckInspection

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"

_FIELDS = ("SequenceMarker", "Audio", "Snapshot", "Expression", "Meaning")


def _inspection(note_count: int = 3) -> DeckInspection:
    sample = {
        "SequenceMarker": "ep01_0001",
        "Audio": "[sound:a.mp3]",
        "Snapshot": '<img src="a.jpg">',
        "Expression": "今日はいい天気だね",
        "Meaning": "Nice weather today",
    }
    return DeckInspection(
        note_count=note_count,
        models=("subs2srs",),
        field_names=_FIELDS,
        first_field_by_model={"subs2srs": "SequenceMarker"},
        samples=(sample,) * note_count,
    )


class _FakeAnkiService:
    decks: list[str] = ["Show::Ep01", "Other"]

    def __init__(self, config) -> None:
        self.config = config

    def get_deck_names(self) -> list[str]:
        return list(type(self).decks)


def _finished_worker() -> MagicMock:
    """What run_off_thread hands back once the work has run: a stopped thread."""
    worker = MagicMock(name="SingleCallWorker")
    worker.isRunning.return_value = False
    return worker


def _sync_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
    try:
        result = work()
    except Exception as exc:  # noqa: BLE001 - mirrors SingleCallWorker's error path
        if on_error is not None:
            on_error(str(exc))
        return _finished_worker()
    on_done(result)
    return _finished_worker()


@pytest.fixture
def deck_service(monkeypatch):
    _FakeAnkiService.decks = ["Show::Ep01", "Other"]
    monkeypatch.setattr(tab_module, "AnkiService", _FakeAnkiService)
    monkeypatch.setattr(tab_module, "inspect_deck", lambda service, deck: _inspection())
    monkeypatch.setattr(tab_module, "run_off_thread", _sync_run_off_thread)
    return _FakeAnkiService


@pytest.fixture
def tab(qtbot, test_config: AnkiMinerConfig, deck_service):
    with patch(_WORKER_TARGET, autospec=False) as queue_cls:
        queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = ReadingDeckTab(
            config=test_config,
            processor=MagicMock(name="EpisodeProcessor"),
            presenter=MagicMock(name="Presenter"),
        )
        qtbot.addWidget(widget)
        try:
            yield widget
        finally:
            widget.deleteLater()


def _pick_first_deck(tab) -> None:
    tab.ensure_decks()
    tab.deck_combo.setCurrentIndex(1)


class TestDeckAndFields:
    def test_idle_tab_offers_mine(self, tab):
        assert tab.mine_button.isEnabled()
        assert tab.cancel_button.isHidden()

    def test_picking_a_deck_fills_and_preselects_the_fields(self, tab):
        _pick_first_deck(tab)

        assert tab.sentence_combo.currentText() == "Expression"
        assert tab.audio_combo.currentText() == "Audio"
        assert tab.picture_combo.currentText() == "Snapshot"
        assert tab.translation_combo.currentText() == "Meaning"
        assert "3" in tab.status_label.text()
        assert tab.mine_button.isEnabled()

    def test_the_deck_list_arrives_once(self, tab):
        tab.ensure_decks()
        tab.ensure_decks()
        assert tab.deck_combo.count() == 3  # placeholder + two decks, never doubled

    def test_a_deck_imported_later_appears_and_the_pick_survives(self, tab, deck_service):
        # The user imports a subs2srs deck into Anki while Anki Miner is open.
        _pick_first_deck(tab)
        assert tab.deck_combo.currentText() == "Show::Ep01"

        deck_service.decks = ["Just Imported", "Show::Ep01", "Other"]
        tab.ensure_decks()

        items = [tab.deck_combo.itemText(i) for i in range(1, tab.deck_combo.count())]
        assert items == ["Just Imported", "Show::Ep01", "Other"]
        assert tab.deck_combo.currentText() == "Show::Ep01"
        assert tab.sentence_combo.currentText() == "Expression"  # the field picks were not wiped
        assert tab.mine_button.isEnabled()

    def test_a_picked_deck_that_disappears_resets_the_pickers(self, tab, deck_service):
        _pick_first_deck(tab)

        deck_service.decks = ["Other"]
        tab.ensure_decks()

        assert tab.deck_combo.currentIndex() == 0
        assert tab.sentence_combo.count() == 1
        assert tab.fields_widget.isHidden()  # A17: no deck, no field rows

    def test_a_failed_refresh_keeps_the_list_it_has(self, tab, deck_service):
        _pick_first_deck(tab)

        deck_service.decks = []  # Anki closed after the list arrived
        tab.ensure_decks()

        assert tab.deck_combo.count() == 3
        assert tab.deck_combo.currentText() == "Show::Ep01"

    def test_an_empty_deck_list_says_so_and_retries_next_time(self, tab, deck_service):
        deck_service.decks = []
        tab.ensure_decks()
        assert tab.deck_combo.count() == 1
        assert tab.issue_banner().current_issue() is not None

        deck_service.decks = ["Show::Ep01"]
        tab.ensure_decks()
        assert tab.deck_combo.count() == 2
        assert tab.issue_banner().current_issue() is None

    def test_a_stale_inspection_is_ignored(self, tab, monkeypatch):
        pending: list[tuple] = []
        monkeypatch.setattr(
            tab_module,
            "run_off_thread",
            lambda parent, work, on_done, on_error=None, **kw: pending.append((work, on_done)) or MagicMock(),
        )
        tab.deck_combo.addItems(["A", "B"])
        tab.deck_combo.setCurrentIndex(1)  # inspect A (deferred)
        tab.deck_combo.setCurrentIndex(2)  # inspect B supersedes it
        work_a, done_a = pending[0]

        done_a(work_a())

        assert tab.sentence_combo.count() == 1  # A's late answer filled nothing

    def test_an_empty_deck_says_so(self, tab, monkeypatch):
        monkeypatch.setattr(tab_module, "inspect_deck", lambda service, deck: _inspection(note_count=0))
        _pick_first_deck(tab)
        assert tab.status_label.text() != ""
        tab._on_mine_clicked()
        assert tab.issue_banner().current_issue().summary == "The selected deck has no notes."

    def test_mine_without_a_sentence_field_explains_itself(self, tab):
        _pick_first_deck(tab)
        tab.sentence_combo.setCurrentIndex(0)
        tab._on_mine_clicked()
        assert tab.issue_banner().current_issue().summary == "Pick a deck and its sentence field first."


class TestMine:
    def test_mine_launches_one_deck_item(self, tab):
        _pick_first_deck(tab)
        tab._on_mine_clicked()

        assert tab.worker_thread is not None
        (item,) = tab._run_items
        assert item.kind == "deck"
        assert item.source.kind == "deck"
        assert item.source.title == "Show::Ep01"
        assert item.source.deck_fields == DeckFieldMap("Expression", "Audio", "Snapshot", "Meaning")
        assert tab.cancel_button.isVisible() or not tab.cancel_button.isHidden()

    def test_none_picks_leave_optional_fields_unused(self, tab):
        _pick_first_deck(tab)
        tab.audio_combo.setCurrentIndex(0)
        tab.translation_combo.setCurrentIndex(0)
        tab._on_mine_clicked()

        fields = tab._run_items[0].source.deck_fields
        assert (fields.audio, fields.translation) == ("", "")

    def test_unmapped_picture_field_warns_but_still_mines(self, qtbot, test_config, deck_service):
        config = dataclasses.replace(test_config, anki_fields={**dict(test_config.anki_fields), "picture": ""})
        with patch(_WORKER_TARGET, autospec=False) as queue_cls:
            queue_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
            widget = ReadingDeckTab(config=config, processor=MagicMock(), presenter=MagicMock())
            qtbot.addWidget(widget)
            widget.log_widget.append_warning = MagicMock()  # type: ignore[method-assign]
            _pick_first_deck(widget)
            widget._on_mine_clicked()

            assert widget.worker_thread is not None
            assert widget.log_widget.append_warning.call_count == 1


def _deck_document(*, with_media: bool) -> ReadingDocument:
    units = [ReadingUnit("今日は", 0, "#1"), ReadingUnit("明日", 1, "#2")]
    if with_media:
        units[0] = dataclasses.replace(units[0], audio_ref=MagicMock(name="clip"))
    return ReadingDocument(title="D", kind="deck", series="D", episode="D", units=units)


class TestCurationContext:
    def test_a_deck_run_hands_the_dialog_its_units(self, tab):
        _pick_first_deck(tab)
        tab._on_mine_clicked()
        doc = _deck_document(with_media=True)
        tab.worker_thread.curation_document = doc

        ctx, lookup_fn = tab._build_curation_context()

        assert lookup_fn is tab.worker_thread.curation_processor.offline_lookup_fn
        assert ctx is not None and ctx.video_file is None and ctx.page_units is not None
        # The curator captions a card's picture with the card's own line; the
        # note ordinal ("#1") belongs to the Position column.
        assert {i: u.location_label for i, u in ctx.page_units.items()} == {0: "今日は", 1: "明日"}
        assert ctx.page_units[0].audio_ref is doc.units[0].audio_ref
        assert doc.units[0].location_label == "#1", "the run's own units must keep the ordinal"

    def test_a_deck_without_media_is_table_only(self, tab):
        _pick_first_deck(tab)
        tab._on_mine_clicked()
        tab.worker_thread.curation_document = _deck_document(with_media=False)

        ctx, _ = tab._build_curation_context()

        assert ctx is None


class TestDeckFirst:
    """A17: the deck comes first; its field rows appear once it has been read."""

    def test_only_the_deck_is_asked_for_at_first(self, tab):
        assert tab.fields_widget.isHidden()

    def test_reading_a_deck_reveals_the_field_rows(self, tab):
        _pick_first_deck(tab)
        assert not tab.fields_widget.isHidden()

    def test_the_rows_say_where_each_part_comes_from(self, tab):
        from PyQt6.QtWidgets import QLabel

        texts = [label.text() for label in tab.fields_widget.findChildren(QLabel)]
        assert texts == ["Sentence from:", "Audio from:", "Picture from:", "Translation from:"]

    def test_the_note_count_sits_right_under_the_deck(self, tab):
        layout = tab.status_label.parentWidget().layout()
        assert layout.indexOf(tab.fields_widget) == layout.indexOf(tab.status_label) + 1

    def test_no_heading_repeats_the_tab(self, tab):
        from PyQt6.QtWidgets import QLabel

        assert "Anki Deck" not in [label.text() for label in tab.findChildren(QLabel)]

    def test_a_failed_deck_fetch_disables_the_deck_and_says_why(self, tab, deck_service):
        deck_service.decks = []
        tab.ensure_decks()

        assert not tab.deck_combo.isEnabled()
        issue = tab.issue_banner().current_issue()
        assert issue.summary == "Couldn't fetch deck names from Anki. Is Anki running?"

        deck_service.decks = ["Show::Ep01"]
        tab.ensure_decks()

        assert tab.deck_combo.isEnabled()
        assert tab.issue_banner().current_issue() is None

    def test_mine_while_the_deck_list_is_missing_keeps_the_fetch_banner(self, tab, deck_service):
        deck_service.decks = []
        tab.ensure_decks()
        fetch_issue = tab.issue_banner().current_issue()

        tab._on_mine_clicked()  # the deck picker is off; "pick a deck" would mislead
        assert tab.issue_banner().current_issue() is fetch_issue

        deck_service.decks = ["Show::Ep01"]
        tab.ensure_decks()
        assert tab.issue_banner().current_issue() is None

    def test_a_truly_empty_deck_is_named_not_its_missing_fields(self, tab, monkeypatch):
        """With no notes, inspect_deck finds no fields; the refusal names the real cause."""
        empty = DeckInspection(note_count=0, models=(), field_names=(), first_field_by_model={}, samples=())
        monkeypatch.setattr(tab_module, "inspect_deck", lambda service, deck: empty)
        _pick_first_deck(tab)

        assert tab.fields_widget.isHidden()
        tab._on_mine_clicked()
        assert tab.issue_banner().current_issue().summary == "The selected deck has no notes."

    def test_mine_is_offered_and_explains_a_missing_deck(self, tab):
        assert tab.mine_button.isEnabled()
        tab._on_mine_clicked()
        assert tab.issue_banner().current_issue().summary == "Pick a deck and its sentence field first."
