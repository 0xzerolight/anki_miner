"""Tests for word curation callback in EpisodeProcessor."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.languages.registry import get_profile
from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.presenters import NullPresenter
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import SCHEMA_VERSION, DictRow, bulk_insert, create_index, write_meta
from tests.unit._processor_fixtures import make_media as _make_media
from tests.unit._processor_fixtures import make_word as _make_word


def test_curation_search_matches_hidden_sentence_suffix(qtbot):
    sentence = "前" * 60 + "検索語"
    dialog = WordCurationDialog([replace(_make_word(), sentence=sentence)])
    qtbot.addWidget(dialog)

    dialog.search_input.setText("検索語")
    dialog._search_debounce_timer.stop()
    dialog._apply_search()

    assert "検索語" not in dialog.table.item(0, 4).text()
    assert not dialog.table.isRowHidden(0)


class TestDefinitionPaneRanksByTokenPos:
    """The dictionary pane beside the card opens on the row the card opens on:
    both rank a word's rows by the token's part of speech (WBR-gui-05)."""

    @pytest.fixture(autouse=True)
    def _lookup_inline(self, monkeypatch):
        def run_now(parent, work, on_done, on_error=None, **kwargs):
            on_done(work())
            return MagicMock()

        monkeypatch.setattr(wcd, "run_off_thread", run_now)

    @staticmethod
    def _en_word(pos: str, start_time: float = 1.0) -> TokenizedWord:
        return TokenizedWord(
            surface="watch",
            lemma="watch",
            reading="",
            sentence="I watch the news.",
            start_time=start_time,
            end_time=start_time + 2.0,
            duration=2.0,
            pos=pos,
            mined_form_override="watch",
        )

    @staticmethod
    def _show(dialog: WordCurationDialog, row: int) -> str:
        dialog.table.setCurrentCell(row, 0)
        dialog._on_row_focus_changed()
        dialog._focus_timer.stop()
        dialog._on_focus_timer_fired()
        return dialog.definition_view.toPlainText()

    def test_en_verb_token_opens_the_pane_on_the_verb_row(self, qtbot, test_config, tmp_path: Path):
        db = tmp_path / "wty-en-en.sqlite"
        create_index(db)
        # wty's shape: one row per part of speech, score 0, sequence 0, noun first.
        bulk_insert(
            db,
            [
                DictRow(term="watch", reading=None, content="<div>A portable timepiece</div>", tags="n", sequence=0),
                DictRow(term="watch", reading=None, content="<div>To look at</div>", tags="v", sequence=0),
            ],
        )
        write_meta(db, {"schema_version": str(SCHEMA_VERSION), "source_name": "wty-en-en"})
        provider = IndexedDictProvider("wty-en-en", db, keys=get_profile("en").dict_keys)
        provider.load()
        service = DefinitionService(test_config, providers=[provider])
        dialog = WordCurationDialog([self._en_word("VERB")], lookup_fn=service.lookup_all_offline)
        qtbot.addWidget(dialog)

        pane = self._show(dialog, 0)

        assert pane.index("To look at") < pane.index("A portable timepiece")

    def test_the_token_pos_is_part_of_the_cache_key(self, qtbot):
        """Two rows (or two picked sentences) share a front but not a POS: each
        gets its own ranked entry, not the other's cached one."""
        calls: list[tuple[str, str | None, str | None]] = []

        def lookup(term: str, lemma: str | None = None, pos: str | None = None) -> list[tuple[str, str]]:
            calls.append((term, lemma, pos))
            return [("wty-en-en", f"<div>{pos} row</div>")]

        dialog = WordCurationDialog([self._en_word("NOUN"), self._en_word("VERB", 5.0)], lookup_fn=lookup)
        qtbot.addWidget(dialog)

        for row in (0, 1, 0):
            pane = self._show(dialog, row)
            assert f"{dialog._pending_word.pos} row" in pane

        assert sorted(calls) == [("watch", None, "NOUN"), ("watch", None, "VERB")]


class TestCurationCallback:
    """Tests for EpisodeProcessor with curation_callback parameter."""

    @pytest.fixture
    def mock_services(self, mock_services):
        subtitle_parser = mock_services["subtitle_parser"]
        # Curation builds the line index too, so mirror parse_subtitle_file's
        # configured return through the with-index path (no candidates).
        subtitle_parser.parse_subtitle_file_with_index.side_effect = lambda f, offset=None: (
            subtitle_parser.parse_subtitle_file.return_value,
            [],
        )
        return mock_services

    @pytest.fixture
    def processor(self, test_config, mock_services):
        return EpisodeProcessor(
            config=test_config,
            presenter=NullPresenter(),
            **mock_services,
        )

    def test_curation_callback_called_with_unknown_words(self, processor, mock_services, tmp_path):
        """Curation callback should receive the filtered unknown words."""
        words = [_make_word("食べる"), _make_word("走る", start_time=5.0)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        received_words = []

        def capture_callback(word_list):
            received_words.extend(word_list)
            return word_list

        processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=capture_callback,
        )

        assert len(received_words) == 2
        assert received_words[0].lemma == "食べる"
        assert received_words[1].lemma == "走る"

    def test_curation_callback_words_carry_their_position(self, processor, mock_services, tmp_path):
        """The Position column's data reaches the dialog (Issue #129).

        Proves the call site, not the formula: a stamp fed the wrong labels —
        or none — leaves every unit test of the helper itself green.
        """
        words = [_make_word("食べる", start_time=1867.0), _make_word("走る", start_time=4364.9)]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = []

        received_words = []

        def capture_callback(word_list):
            received_words.extend(word_list)
            return word_list

        processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=capture_callback,
        )

        assert [w.position_label for w in received_words] == ["00:31:07", "01:12:44"]

    def test_curation_callback_filters_words(self, processor, mock_services, tmp_path):
        """When callback returns a subset, only those words proceed to Phase 3."""
        word1 = _make_word("食べる")
        word2 = _make_word("走る", start_time=5.0)
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = [word1, word2]
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = [word1, word2]
        mock_services["media_extractor"].extract_media_batch.return_value = [(word1, media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = [1]

        # Only select the first word
        def select_first(word_list):
            return [word_list[0]]

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=select_first,
        )

        # Media extractor should only receive the selected word
        me_args = mock_services["media_extractor"].extract_media_batch.call_args
        assert me_args[0][1] == [word1]
        assert result.cards_created == 1

    def test_curation_callback_returns_none_cancels(self, processor, mock_services, tmp_path):
        """When callback returns None (user cancelled/rejected), processing is cancelled."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=lambda w: None,  # Return None = cancel
        )

        assert result.cards_created == 0
        assert "cancelled" in result.errors[0].lower()
        # Phase 3 should not have been reached
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_curation_callback_returns_empty_completes_zero_cards(self, processor, mock_services, tmp_path):
        """When callback returns [] (confirmed, nothing selected), the run completes
        with zero cards — NOT a cancellation."""
        words = [_make_word("食べる")]
        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=lambda w: [],  # Confirmed with nothing selected
        )

        assert result.cards_created == 0
        assert result.new_words_found == 0
        assert not any("cancelled" in e.lower() for e in result.errors)
        # Phase 3 should not have been reached (no words to card)
        mock_services["media_extractor"].extract_media_batch.assert_not_called()

    def test_curation_callback_none_normal_flow(self, processor, mock_services, tmp_path):
        """When curation_callback is None, normal flow proceeds (backward compat)."""
        words = [_make_word("食べる")]
        media = _make_media("taberu")

        mock_services["subtitle_parser"].parse_subtitle_file.return_value = words
        mock_services["anki_service"].get_existing_vocabulary.return_value = set()
        mock_services["word_filter"].filter_unknown.return_value = words
        mock_services["media_extractor"].extract_media_batch.return_value = [(words[0], media)]
        mock_services["definition_service"].get_definitions_batch.return_value = ["1. to eat"]
        mock_services["anki_service"].create_cards_batch.return_value = [1]

        result = processor.process_episode(
            tmp_path / "v.mkv",
            tmp_path / "s.ass",
            curation_callback=None,
        )

        assert result.cards_created == 1
        assert result.success is True
