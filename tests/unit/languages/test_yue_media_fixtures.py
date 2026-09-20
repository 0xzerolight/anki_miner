"""Cantonese audio-track and caption selection against recorded ffprobe output.

A PIN, not TDD: the profile fields it checks already exist, so there was no
honest red-first state. It is a regression net against a future edit to
``audio_track_codes``, ``captions.codes`` or ``captions.audio_pattern``, written
against recorded ffprobe output rather than against the profile it tests. It was
proven able to fail before being committed, by removing ``"yue-HK"`` from
``audio_track_codes`` and watching the first case go red.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from anki_miner.languages.registry import get_profile

PROBE = json.loads((Path(__file__).parents[2] / "fixtures" / "yue" / "dual_audio_ffprobe.json").read_text("utf-8"))


def audio_streams():
    return [stream for stream in PROBE["streams"] if stream["codec_type"] == "audio"]


def test_the_cantonese_track_is_the_one_yue_claims():
    codes = get_profile("yue").audio_track_codes
    claimed = [stream["index"] for stream in audio_streams() if stream["tags"]["language"] in codes]
    assert claimed == [1, 2]  # both HK spellings, and nothing else


def test_a_chi_track_is_never_auto_selected():
    # zh owns chi/zho/zh/cmn, and on the web they are overwhelmingly Mandarin;
    # a chi-tagged Cantonese track stays hand-selectable.
    assert "chi" not in get_profile("yue").audio_track_codes
    assert any(stream["tags"]["language"] == "chi" for stream in audio_streams())


def test_the_japanese_track_is_not_claimed_either():
    assert "jpn" not in get_profile("yue").audio_track_codes


def test_the_audio_pattern_matches_both_hk_spellings_and_nothing_mandarin():
    pattern = re.compile(get_profile("yue").captions.audio_pattern)
    assert pattern.match("yue") and pattern.match("yue-HK") and pattern.match("zh-HK")
    assert not pattern.match("zh-Hans") and not pattern.match("zh") and not pattern.match("cmn")


def test_the_caption_codes_prefer_cantonese_over_written_chinese():
    codes = get_profile("yue").captions.codes
    assert codes.index("yue") < codes.index("zh-HK") < codes.index("zh-Hant")
