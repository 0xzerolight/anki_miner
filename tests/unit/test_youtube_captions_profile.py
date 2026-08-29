"""Caption probing, the audio selector and --sub-lang come from the profile."""

import dataclasses

import pytest

from anki_miner.exceptions.youtube import NoJapaneseSubtitlesError, NoSourceSubtitlesError
from anki_miner.languages.profile import CaptionLangs
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

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

    register_stub_profile(monkeypatch, "ko", captions=KO)
    return dataclasses.replace(test_config, language="ko")


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
    with pytest.raises(NoSourceSubtitlesError, match="wrote no Korean subtitle"):
        YouTubeFetcherService(ko_config)._resolve_outputs(tmp_path, "abc123", "auto_only")
