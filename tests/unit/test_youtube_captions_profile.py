"""Caption probing, the audio selector and --sub-lang come from the profile."""

import dataclasses
import json
from unittest.mock import patch

import pytest

from anki_miner.exceptions.youtube import (
    NoJapaneseSubtitlesError,
    NoSourceSubtitlesError,
    YouTubeFetchError,
)
from anki_miner.languages.profile import CaptionLangs
from anki_miner.languages.registry import get_profile
from anki_miner.services.youtube_fetcher import YouTubeFetcherService
from anki_miner.utils.process_supervisor import SupervisedResult, SupervisedState

KO = CaptionLangs(
    primary="ko",
    codes=("ko",),
    orig_codes=("ko-orig",),
    audio_pattern="^ko(-|$)",
    bare_fallback=True,
)


@pytest.fixture()
def ko_config(monkeypatch, test_config):
    """A ko-shaped profile, registered: Stage 1B registers ja only.

    Registered rather than monkeypatched over ``get_profile``, because
    ``config_language`` degrades an unregistered code to ja before the profile
    is ever resolved.
    """
    from tests.unit.languages.stub_registry import register_stub_profile

    register_stub_profile(monkeypatch, "ko", captions=KO, english_name="Korean")
    return dataclasses.replace(test_config, language="ko")


@pytest.fixture()
def zh_config(test_config):
    """The real zh profile: a multi-code language, unlike ja and ko."""
    return dataclasses.replace(test_config, language="zh")


def _probe(config, **payload):
    """Run probe_metadata over a canned yt-dlp --dump-single-json payload."""
    data = {"id": "dQw4w9WgXcQ", "title": "Test Video", "duration": 120, **payload}
    result = SupervisedResult(SupervisedState.COMPLETED, 0, json.dumps(data), "")
    with patch("anki_miner.services.youtube_fetcher.run_supervised", return_value=result):
        return YouTubeFetcherService(config).probe_metadata("https://youtu.be/abc123")


def test_no_source_subtitles_error_is_the_same_class():
    assert NoJapaneseSubtitlesError is NoSourceSubtitlesError


def test_ja_profile_pins_the_caption_parameters():
    from anki_miner.languages.registry import get_profile

    assert get_profile("ja").captions == CaptionLangs(
        primary="ja",
        codes=("ja",),
        orig_codes=("ja-orig",),
        audio_pattern="^ja(-|$)",
        bare_fallback=True,
    )


def test_native_auto_detection_follows_the_profile_codes():
    data = {"automatic_captions": {"ko": [{}], "ko-orig": [{}], "ja": [{}]}}
    assert YouTubeFetcherService._has_native_auto_ja(data, captions=KO) is True
    # ja sees a bare "ja" with someone else's -orig key: a translation.
    assert YouTubeFetcherService._has_native_auto_ja(data) is False


def test_audio_track_pattern_is_anchored_per_language():
    data = {"formats": [{"vcodec": "none", "language": "ko-KR"}]}
    assert YouTubeFetcherService._has_ja_audio_track(data, captions=KO) is True
    assert YouTubeFetcherService._has_ja_audio_track(data) is False
    jav = {"formats": [{"vcodec": "none", "language": "jav"}]}
    assert YouTubeFetcherService._has_ja_audio_track(jav) is False


def test_fetch_cmd_uses_the_profile_sub_lang(ko_config, tmp_path):
    cmd = YouTubeFetcherService(ko_config)._build_fetch_cmd("https://y", tmp_path, "auto_dub", fallback_allowed=False)
    assert cmd[cmd.index("--sub-lang") + 1] == "ko"
    fmt = cmd[cmd.index("--format") + 1]
    assert fmt.endswith("+bestaudio[language~='^ko(-|$)']")


def test_resolved_outputs_accept_the_profile_suffix(ko_config, tmp_path):
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    (tmp_path / "abc123.ko.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    media = YouTubeFetcherService(ko_config)._resolve_outputs(tmp_path, "abc123", "auto_only")
    assert media.subtitle_file.name == "abc123.ko.srt"


def test_missing_subtitle_raises_the_shared_error(ko_config, tmp_path):
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    with pytest.raises(NoSourceSubtitlesError, match="Korean subtitle track was no longer available"):
        YouTubeFetcherService(ko_config)._resolve_outputs(tmp_path, "abc123", "auto_only")


# ---------------------------------------------------------------------------
# Every code the profile lists is a code the fetcher accepts (a Chinese track
# is filed under zh-Hans, zh-CN, zh-Hant, zh-TW or plain zh, depending on the
# uploader), so a multi-code profile exercises what ja and ko cannot.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", get_profile("zh").captions.codes)
def test_manual_probe_accepts_every_caption_code(zh_config, code):
    info = _probe(zh_config, subtitles={code: [{"ext": "vtt"}]}, automatic_captions={})
    assert info.has_manual_ja_subs is True


def test_manual_probe_still_refuses_a_translation_key(zh_config):
    info = _probe(zh_config, subtitles={"zh-Hant-en": [{"ext": "vtt"}]}, automatic_captions={})
    assert info.has_manual_ja_subs is False


def test_native_auto_detection_accepts_a_non_primary_code():
    captions = get_profile("zh").captions
    native = {"automatic_captions": {"zh-Hant": [{}], "zh-Hant-orig": [{}]}}
    assert YouTubeFetcherService._has_native_auto_ja(native, captions=captions) is True
    translated = {"automatic_captions": {"zh-Hant": [{}], "en-orig": [{}]}}
    assert YouTubeFetcherService._has_native_auto_ja(translated, captions=captions) is False
    bare = {"automatic_captions": {"zh-TW": [{}]}, "language": "zh-TW"}
    assert YouTubeFetcherService._has_native_auto_ja(bare, captions=captions) is True


def test_fetch_cmd_requests_every_caption_code(zh_config, tmp_path):
    cmd = YouTubeFetcherService(zh_config)._build_fetch_cmd("https://y", tmp_path, "manual_only")
    assert cmd[cmd.index("--sub-lang") + 1] == ",".join(get_profile("zh").captions.codes)


def test_resolve_outputs_picks_the_first_code_the_profile_lists(zh_config, tmp_path):
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    for code in ("zh-Hant", "zh-Hans", "zh"):
        (tmp_path / f"abc123.{code}.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    media = YouTubeFetcherService(zh_config)._resolve_outputs(tmp_path, "abc123", "manual_only")
    assert media.subtitle_file.name == "abc123.zh-Hans.srt"


def test_resolve_outputs_takes_a_non_primary_code_on_its_own(zh_config, tmp_path):
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    (tmp_path / "abc123.zh-TW.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    media = YouTubeFetcherService(zh_config)._resolve_outputs(tmp_path, "abc123", "manual_only")
    assert media.subtitle_file.name == "abc123.zh-TW.srt"


def test_resolve_outputs_still_rejects_two_files_for_one_code(zh_config, tmp_path):
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    (tmp_path / "abc123.zh-Hans.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    (tmp_path / "abc123.extra.zh-Hans.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nyo\n")
    with pytest.raises(YouTubeFetchError, match="more than one subtitle file"):
        YouTubeFetcherService(zh_config)._resolve_outputs(tmp_path, "abc123", "manual_only")


def test_resolve_outputs_ranks_by_code_order_not_by_manual_or_auto(zh_config, tmp_path):
    """--write-sub + --write-auto-sub prefer the manual track per code, not across codes.

    A manual zh-Hant track and an auto zh-Hans one leave two files with nothing
    on disk to tell them apart, so the profile's code order decides.
    """
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    (tmp_path / "abc123.zh-Hant.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nmanual\n")
    (tmp_path / "abc123.zh-Hans.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nauto\n")
    media = YouTubeFetcherService(zh_config)._resolve_outputs(tmp_path, "abc123", "manual_only")
    assert media.subtitle_file.name == "abc123.zh-Hans.srt"


# ---------------------------------------------------------------------------
# yue is the one profile whose ``codes`` name another language: zh-Hant under
# Cantonese audio is written Chinese (書面語), listed so a fetch can fall back
# to it. ``own_codes`` is what the acceptance gates read, so a Mandarin video
# subtitled in zh-Hant is not mistaken for a Cantonese one.
# ---------------------------------------------------------------------------


@pytest.fixture()
def yue_config(test_config):
    return dataclasses.replace(test_config, language="yue")


def test_manual_probe_refuses_written_chinese_for_cantonese(yue_config):
    info = _probe(yue_config, subtitles={"zh-Hant": [{"ext": "vtt"}]}, automatic_captions={})
    assert info.has_manual_ja_subs is False


def test_manual_probe_accepts_a_cantonese_track(yue_config):
    info = _probe(yue_config, subtitles={"yue": [{"ext": "vtt"}]}, automatic_captions={})
    assert info.has_manual_ja_subs is True


def test_native_auto_detection_refuses_written_chinese_for_cantonese():
    captions = get_profile("yue").captions
    mandarin = {"automatic_captions": {"zh-Hant": [{}]}, "language": "zh-Hant"}
    assert YouTubeFetcherService._has_native_auto_ja(mandarin, captions=captions) is False
    cantonese = {"automatic_captions": {"yue": [{}], "yue-orig": [{}]}}
    assert YouTubeFetcherService._has_native_auto_ja(cantonese, captions=captions) is True


def test_cantonese_still_requests_and_resolves_written_chinese(yue_config, tmp_path):
    """Only the gates narrow: ``codes`` stays the request and resolve order."""
    cmd = YouTubeFetcherService(yue_config)._build_fetch_cmd("https://y", tmp_path, "manual_only")
    assert cmd[cmd.index("--sub-lang") + 1] == "yue,zh-HK,zh-Hant-HK,zh-Hant"
    (tmp_path / "abc123.mp4").write_bytes(b"v")
    (tmp_path / "abc123.zh-Hant.srt").write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    media = YouTubeFetcherService(yue_config)._resolve_outputs(tmp_path, "abc123", "manual_only")
    assert media.subtitle_file.name == "abc123.zh-Hant.srt"
