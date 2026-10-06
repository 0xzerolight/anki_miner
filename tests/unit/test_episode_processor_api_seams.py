"""EpisodeProcessor seams the --api runs read: the collapse record, word_on_line, definition_viable,
and a curation callback's makes_words."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import _Phase2Counts
from tests.conftest import build_processor
from tests.unit._processor_fixtures import make_media


def _noun(front: str, lemma: str | None = None) -> TokenizedWord:
    return TokenizedWord(
        surface=front,
        lemma=lemma or front,
        reading="",
        sentence=f"{front}です。",
        start_time=1.0,
        end_time=2.0,
        duration=1.0,
        mined_form_override=front,
    )


def test_the_collapse_records_each_loser_and_the_front_it_merged_into(test_config) -> None:
    definitions = MagicMock()
    definitions.offline_term_identities.side_effect = lambda pairs: {
        pair: {("辞書", 7, "でる")} for pair in pairs if pair[0] in ("出る", "出でる")
    }
    processor = build_processor(replace(test_config, allow_duplicate_cards=False), definition_service=definitions)
    kept = processor._phase2_collapse_duplicates(
        [_noun("出る"), _noun("本"), _noun("出でる"), _noun("本")], _Phase2Counts()
    )
    assert [w.mined_form for w in kept] == ["出る", "本"]
    assert [(w.mined_form, winner) for w, winner in processor.last_collapsed] == [("出でる", "出る"), ("本", "本")]


def test_no_collapse_records_nothing(test_config) -> None:
    processor = build_processor(replace(test_config, allow_duplicate_cards=True))
    processor._phase2_collapse_duplicates([_noun("本"), _noun("本")], _Phase2Counts())
    assert processor.last_collapsed == []


def test_word_on_line_is_the_filters_swap_ranked_again(test_config) -> None:
    word_filter = MagicMock()
    moved = _noun("危害")
    word_filter.word_on_line.return_value = moved
    processor = build_processor(test_config, word_filter=word_filter)
    processor._attach_frequency = MagicMock(return_value=0)
    line = (1.0, 2.0, "危害を加える")
    assert processor.word_on_line(_noun("危害"), line, (0, 2), reading="きがい") is moved
    word_filter.word_on_line.assert_called_once_with(_noun("危害"), line, (0, 2), reading="きがい")
    processor._attach_frequency.assert_called_once_with([moved])


def test_definition_viable_is_phase_twos_probe(test_config) -> None:
    definitions = MagicMock()
    definitions.has_offline_definitions.side_effect = lambda terms: {t: t != "空" for t in terms}
    definitions.offline_deinflection_terms_exist.return_value = set()
    processor = build_processor(test_config, definition_service=definitions)
    assert processor.definition_viable([_noun("本"), _noun("空")]) == [True, False]


class _Callback:
    """A curation callback that records what it was handed and hands back *hand_back*."""

    def __init__(self, hand_back: list[TokenizedWord], *, makes_words: bool) -> None:
        self.hand_back = hand_back
        self.makes_words = makes_words
        self.seen: list[list[TokenizedWord]] = []

    def __call__(self, words: list[TokenizedWord]) -> list[TokenizedWord]:
        self.seen.append(list(words))
        return list(self.hand_back)


#: The three places a run used to end before curation: the parse found no word,
#: phase 2 left none, the automatic merge's sentence-length pass dropped every one.
EMPTY_AT = ["parse", "phase2", "auto-stamp"]


def _run_empty_at(where, test_config, mock_services, tmp_path, callback):
    produced = _noun("本屋")
    parser = mock_services["subtitle_parser"]
    parser.parse_subtitle_file_with_index.return_value = ([] if where == "parse" else [produced], [])
    parser.parse_raw_entries.return_value = []
    mock_services["anki_service"].get_existing_vocabulary.return_value = set()
    mock_services["word_filter"].filter_unknown.return_value = [] if where == "phase2" else [produced]
    mock_services["definition_service"].get_definitions_batch.side_effect = lambda ws, *a, **kw: ["1. def"] * len(ws)
    mock_services["anki_service"].create_cards_batch.side_effect = lambda payloads, *a, **kw: list(
        range(1, len(payloads) + 1)
    )
    processor = build_processor(test_config, **mock_services)
    if where == "auto-stamp":
        processor._auto_stamp_line_expansions = MagicMock(return_value=[])
    return processor.process_episode(tmp_path / "v.mkv", tmp_path / "s.srt", curation_callback=callback)


@pytest.mark.parametrize("where", EMPTY_AT)
def test_a_run_left_empty_still_mines_the_words_a_callback_that_makes_words_hands_back(
    where, test_config, mock_services, tmp_path
) -> None:
    made = _noun("本")
    mock_services["media_extractor"].extract_media_batch.return_value = [(made, make_media("hon"))]
    callback = _Callback([made], makes_words=True)

    result = _run_empty_at(where, test_config, mock_services, tmp_path, callback)

    assert callback.seen == [[]]
    [extracted] = mock_services["media_extractor"].extract_media_batch.call_args.args[1]
    assert extracted.mined_form == "本"
    assert result.errors == [] and result.cards_created == 1


@pytest.mark.parametrize("where", EMPTY_AT)
def test_a_run_left_empty_whose_callback_makes_nothing_is_the_zero_card_success(
    where, test_config, mock_services, tmp_path
) -> None:
    callback = _Callback([], makes_words=True)

    result = _run_empty_at(where, test_config, mock_services, tmp_path, callback)

    assert callback.seen == [[]]
    mock_services["media_extractor"].extract_media_batch.assert_not_called()
    assert result.errors == [] and (result.cards_created, result.new_words_found) == (0, 0)


@pytest.mark.parametrize("where", EMPTY_AT)
def test_a_run_left_empty_never_reaches_any_other_callback(where, test_config, mock_services, tmp_path) -> None:
    callback = _Callback([_noun("本")], makes_words=False)

    result = _run_empty_at(where, test_config, mock_services, tmp_path, callback)

    assert callback.seen == []
    mock_services["media_extractor"].extract_media_batch.assert_not_called()
    assert result.errors == [] and (result.cards_created, result.new_words_found) == (0, 0)
