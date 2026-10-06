"""EpisodeProcessor seams the --api runs read: the collapse record, word_on_line, definition_viable."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from anki_miner.models import TokenizedWord
from anki_miner.orchestration.episode_processor import _Phase2Counts
from tests.conftest import build_processor


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
