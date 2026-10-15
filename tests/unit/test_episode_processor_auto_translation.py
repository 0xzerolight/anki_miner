"""F-04: with no translation file chosen, the run picks one, names it, and attaches it at offset zero."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

from anki_miner.services.secondary_subtitles import AutoTranslation, TranslationCandidate
from anki_miner.utils.audio_track_detector import SubtitleStream, TrackTitle
from tests.conftest import build_processor
from tests.unit._processor_fixtures import make_media, make_word

_PICK = "anki_miner.orchestration.episode_processor.load_auto_translation"
# The word below spans 1.0-3.0. "I eat." overlaps it by 1.5 s; "You run." by 0.1 s, under the 0.2 s floor.
SECOND = [(0.5, 2.5, "I eat."), (2.9, 5.0, "You run.")]


def _wire(mock_services, words) -> None:
    parser = mock_services["subtitle_parser"]
    parser.parse_subtitle_file.return_value = words
    parser.parse_subtitle_file_with_index.return_value = (words, [])
    parser.parse_raw_entries.return_value = [(1.0, 3.0, "食べるのテスト")]
    mock_services["anki_service"].get_existing_vocabulary.return_value = set()
    mock_services["word_filter"].filter_unknown.return_value = words
    mock_services["media_extractor"].extract_media_batch.side_effect = lambda _v, ws, *a, **k: [
        (w, make_media("w")) for w in ws
    ]
    mock_services["definition_service"].get_definitions_batch.side_effect = lambda ws, *a, **k: ["def"] * len(ws)
    mock_services["anki_service"].create_cards_batch.return_value = [1]


def _sidecar_pick(path) -> AutoTranslation:
    return AutoTranslation(TranslationCandidate(0, TrackTitle.PLAIN, sidecar=path), list(SECOND))


def _extracted(mock_services):
    """The words handed to phase 3: the translation is attached by then."""
    return mock_services["media_extractor"].extract_media_batch.call_args.args[1]


def _infos(presenter) -> list[str]:
    return [call.args[0] for call in presenter.show_info.call_args_list]


def test_no_chosen_file_attaches_the_picked_track_and_names_it(test_config, mock_services, tmp_path):
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    presenter = MagicMock()
    proc = build_processor(config, presenter=presenter, **mock_services)
    with patch(_PICK, return_value=_sidecar_pick(tmp_path / "ep01.en.srt")) as pick:
        # -2.0 belongs to a chosen file; the auto-picked track sits on the video's own timeline.
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass", secondary_subtitle_offset=-2.0)
    assert pick.call_args.args[0] == tmp_path / "ep01.mkv"
    assert _extracted(mock_services)[0].sentence_translation == "I eat."
    assert any("ep01.en.srt" in message for message in _infos(presenter))
    assert proc.auto_translation_entries(tmp_path / "ep01.mkv") == SECOND


def test_an_embedded_pick_is_named_by_its_track(test_config, mock_services, tmp_path):
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    presenter = MagicMock()
    proc = build_processor(config, presenter=presenter, **mock_services)
    stream = SubtitleStream(index=3, sub_index=1, codec_name="ass", language_tag="eng", title="Full", is_text=True)
    picked = AutoTranslation(TranslationCandidate(0, TrackTitle.FULL, stream=stream), list(SECOND))
    with patch(_PICK, return_value=picked):
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
    assert any("embedded track 2 [eng] Full" in message for message in _infos(presenter))


def test_a_chosen_file_wins_and_nothing_is_probed(test_config, mock_services, tmp_path):
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    proc = build_processor(config, **mock_services)
    with patch(_PICK) as pick:
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass", secondary_subtitle_file=tmp_path / "en.srt")
    pick.assert_not_called()


def test_the_switch_off_never_probes(test_config, mock_services, tmp_path):
    # Review Focus 1: secondary_subtitle_enabled defaults to False, so an existing config mines as before.
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    proc = build_processor(test_config, **mock_services)
    with patch(_PICK) as pick:
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
    pick.assert_not_called()
    assert _extracted(mock_services)[0].sentence_translation == ""


def test_audiobook_runs_do_not_auto_pick(test_config, mock_services, tmp_path):
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    proc = build_processor(config, **mock_services)
    with patch(_PICK) as pick:
        proc.process_episode(tmp_path / "book.m4b", tmp_path / "book.srt", audio_only=True)
    pick.assert_not_called()


def test_one_pick_per_video_for_the_processor_life(test_config, mock_services, tmp_path):
    # A season pre-pass and its mine pass both run the episode: one probe, one log line.
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    presenter = MagicMock()
    proc = build_processor(config, presenter=presenter, **mock_services)
    with patch(_PICK, return_value=_sidecar_pick(tmp_path / "ep01.en.srt")) as pick:
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
    assert pick.call_count == 1
    assert sum("ep01.en.srt" in message for message in _infos(presenter)) == 1


def test_no_track_found_leaves_the_field_empty_and_says_nothing(test_config, mock_services, tmp_path):
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    presenter = MagicMock()
    proc = build_processor(config, presenter=presenter, **mock_services)
    with patch(_PICK, return_value=None):
        proc.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.ass")
    assert _extracted(mock_services)[0].sentence_translation == ""
    assert not any("picked automatically" in message for message in _infos(presenter))
    assert proc.auto_translation_entries(tmp_path / "ep01.mkv") == []


def test_a_spanish_processor_picks_with_its_own_config(test_config, mock_services, tmp_path):
    # Review Focus 3: the pick reads the run's own (non-Japanese) config, no ja branch.
    config = replace(test_config, language="es", ui_language="de", secondary_subtitle_enabled=True)
    proc = build_processor(config, **mock_services)
    picked = AutoTranslation(
        TranslationCandidate(0, TrackTitle.PLAIN, sidecar=tmp_path / "cap01.de.srt"), [(0.5, 2.5, "Ich esse.")]
    )
    with patch(_PICK, return_value=picked) as pick:
        entries = proc._auto_secondary_entries(tmp_path / "cap01.mkv", tmp_path / "tmp")
    assert entries == [(0.5, 2.5, "Ich esse.")]
    assert pick.call_args.args[1].language == "es"
    assert pick.call_args.args[2] is proc.subtitle_parser


def test_the_youtube_inner_run_does_not_auto_pick(test_config, mock_services, tmp_path):
    # process_youtube_url's inner call has no translation input of its own (ruling F04-4).
    config = replace(test_config, secondary_subtitle_enabled=True)
    _wire(mock_services, [make_word("食べる", start_time=1.0)])
    proc = build_processor(config, **mock_services)
    with patch(_PICK) as pick:
        proc.process_episode(tmp_path / "clip.mp4", tmp_path / "clip.ja.srt", _outer_kind="youtube")
    pick.assert_not_called()
