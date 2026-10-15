"""F-10: phase 4 hands the curator's dictionary picks to the definition lookup, and only then."""

from __future__ import annotations

from dataclasses import replace

from anki_miner.models import MediaData, TokenizedWord
from anki_miner.services.word_filter import WordFilterService
from anki_miner.services.word_pool import fixed_selection
from tests.conftest import build_processor


def _word(front: str, pick: str = "", start: float = 1.0) -> TokenizedWord:
    return TokenizedWord(
        surface=front,
        lemma=front,
        reading="",
        sentence=f"{front}だ。",
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="名詞",
        dictionary_pick=pick,
    )


def test_a_pick_reaches_the_definition_batch(test_config, mock_services):
    service = mock_services["definition_service"]
    service.get_definitions_batch.return_value = ["def"]
    proc = build_processor(test_config, **mock_services)
    proc._phase4_lookup(None, [(_word("猫", "Jitendex"), MediaData())], None)
    assert service.get_definitions_batch.call_args.kwargs["preferred_dictionaries"] == {"猫": "Jitendex"}


def test_no_pick_keeps_the_call_shape(test_config, mock_services):
    # Review Focus 1: an untouched run calls the lookup exactly as before.
    service = mock_services["definition_service"]
    service.get_definitions_batch.return_value = ["def"]
    proc = build_processor(test_config, **mock_services)
    proc._phase4_lookup(None, [(_word("猫"), MediaData())], None)
    assert "preferred_dictionaries" not in service.get_definitions_batch.call_args.kwargs


def test_a_stacking_profile_ignores_the_pick(test_config, mock_services):
    service = mock_services["definition_service"]
    service.get_glossaries_batch.return_value = ["stack"]
    proc = build_processor(replace(test_config, language="yue"), **mock_services)
    proc._phase4_lookup(None, [(_word("蚊", "CC-Canto"), MediaData())], None)
    assert "preferred_dictionaries" not in service.get_glossaries_batch.call_args.kwargs
    service.get_definitions_batch.assert_not_called()


def test_the_season_mine_pass_carries_the_pick(test_config, mock_services, tmp_path):
    """The season curator's subset reaches phase 4 as the same objects, pick included."""
    word = _word("猫", "Jitendex")
    parser = mock_services["subtitle_parser"]
    parser.parse_subtitle_file.return_value = [word]
    parser.parse_subtitle_file_with_index.return_value = ([word], [])
    parser.parse_raw_entries.return_value = []
    mock_services["anki_service"].get_existing_vocabulary.return_value = set()
    mock_services["anki_service"].create_cards_batch.return_value = [1]
    definitions = mock_services["definition_service"]
    definitions.has_offline_definitions.side_effect = lambda terms: dict.fromkeys(terms, True)
    definitions.offline_term_identities.return_value = {}
    definitions.get_definitions_batch.side_effect = lambda pairs, *a, **k: ["def"] * len(pairs)
    mock_services["media_extractor"].extract_media_batch.side_effect = lambda _v, batch, *a, **k: [
        (w, MediaData()) for w in batch
    ]
    services = {**mock_services, "word_filter": WordFilterService(test_config)}
    proc = build_processor(test_config, **services)
    proc.process_episode(tmp_path / "b.mkv", tmp_path / "b.srt", curation_callback=fixed_selection([replace(word)]))
    assert definitions.get_definitions_batch.call_args.kwargs["preferred_dictionaries"] == {"猫": "Jitendex"}
