"""Mining asks the video for its opening/ending chapters before it parses (F-08 step 2)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

from anki_miner.utils.ffmpeg_resolver import resolve_ffprobe
from tests.conftest import build_processor

_PROBE = "anki_miner.orchestration.episode_processor.op_ed_skip_ranges"


def _record_order(mock_services) -> list[str]:
    """Make phase 1 parse nothing, and record when the parser is told about chapters."""
    calls: list[str] = []
    parser = mock_services["subtitle_parser"]
    parser.set_skip_ranges.side_effect = lambda *args, **kwargs: calls.append("register")
    parser.parse_subtitle_file.side_effect = lambda *args, **kwargs: calls.append("parse") or []
    parser.parse_subtitle_file_with_index.side_effect = lambda *args, **kwargs: calls.append("parse") or ([], [])
    return calls


def test_chapters_are_registered_before_the_parse(test_config, mock_services, tmp_path):
    config = replace(test_config, skip_opening_ending=True)
    video, sub = tmp_path / "ep01.mkv", tmp_path / "ep01.srt"
    calls = _record_order(mock_services)
    processor = build_processor(config, **mock_services)

    with patch(_PROBE, return_value=((0.0, 90.0),)) as probe:
        processor.process_episode(video, sub, subtitle_offset=2.5)

    assert probe.call_args.args == (video, resolve_ffprobe(config))
    mock_services["subtitle_parser"].set_skip_ranges.assert_called_once_with(sub, ((0.0, 90.0),), offset=2.5)
    assert calls[:2] == ["register", "parse"]


def test_without_a_run_offset_the_configured_one_is_used(test_config, mock_services, tmp_path):
    config = replace(test_config, skip_opening_ending=True, subtitle_offset=-1.25)
    _record_order(mock_services)
    processor = build_processor(config, **mock_services)
    sub = tmp_path / "ep01.srt"

    with patch(_PROBE, return_value=()):
        processor.process_episode(tmp_path / "ep01.mkv", sub)

    mock_services["subtitle_parser"].set_skip_ranges.assert_called_once_with(sub, (), offset=-1.25)


def test_switch_off_registers_nothing_and_never_probes(test_config, mock_services, tmp_path):
    config = replace(test_config, skip_opening_ending=False)
    _record_order(mock_services)
    processor = build_processor(config, **mock_services)

    # A probe that answers, not one that raises: process_episode turns an
    # exception into a failed result, which would hide a probe made anyway.
    with patch(_PROBE, return_value=((0.0, 90.0),)) as probe:
        processor.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.srt")

    probe.assert_not_called()
    mock_services["subtitle_parser"].set_skip_ranges.assert_not_called()


def test_a_skip_is_said_in_the_activity_log(test_config, mock_services, tmp_path):
    config = replace(test_config, skip_opening_ending=True)
    _record_order(mock_services)
    presenter = MagicMock()
    processor = build_processor(config, presenter=presenter, **mock_services)

    with patch(_PROBE, return_value=((0.0, 90.0), (1300.0, 1390.0))):
        processor.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.srt")

    infos = [call.args[0] for call in presenter.show_info.call_args_list]
    assert "Skipping lines in 2 opening, ending or preview chapter(s)" in infos


def test_no_chapter_says_nothing(test_config, mock_services, tmp_path):
    config = replace(test_config, skip_opening_ending=True)
    _record_order(mock_services)
    presenter = MagicMock()
    processor = build_processor(config, presenter=presenter, **mock_services)

    with patch(_PROBE, return_value=()):
        processor.process_episode(tmp_path / "ep01.mkv", tmp_path / "ep01.srt")

    assert not any("chapter" in str(call.args[0]) for call in presenter.show_info.call_args_list)
