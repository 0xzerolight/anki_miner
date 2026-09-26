"""matches_language_tag over a language-code set, and the subtitle pick that uses it."""

import pytest

from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    SubtitleStream,
    matches_language_tag,
)

KOREAN = frozenset({"kor", "ko", "korean"})


@pytest.mark.parametrize("tag", ["kor", "ko", "KO", "ko-KR", "Korean"])
def test_korean_codes_match(tag):
    assert matches_language_tag(tag, KOREAN) is True


@pytest.mark.parametrize("tag", ["jpn", "kore", "ko2", None])
def test_korean_codes_reject_everything_else(tag):
    assert matches_language_tag(tag, KOREAN) is False


def test_pick_subtitle_stream_requires_codes():
    from anki_miner.services.audio_condenser import _pick_subtitle_stream

    streams = [
        SubtitleStream(0, 0, "subrip", "eng", None, True),
        SubtitleStream(1, 1, "subrip", "kor", None, True),
    ]
    assert _pick_subtitle_stream(streams, None, KOREAN).sub_index == 1
    assert _pick_subtitle_stream(streams, None, JAPANESE_LANGUAGE_CODES).sub_index == 0


def test_candidate_rank_prefers_the_mining_language():
    from anki_miner.services.retime_reference import _candidate_rank

    ja = SubtitleStream(1, 0, "ass", "jpn", None, True)
    ko = SubtitleStream(2, 1, "ass", "kor", None, True)
    assert sorted([ja, ko], key=lambda s: _candidate_rank(s, KOREAN))[0] is ko
    assert sorted([ja, ko], key=lambda s: _candidate_rank(s, JAPANESE_LANGUAGE_CODES))[0] is ja
