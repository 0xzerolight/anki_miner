"""subtitle_language: the mining language's tags, read against every language's."""

from pathlib import Path

from anki_miner.languages.registry import get_profile, subtitle_language
from anki_miner.utils.file_pairing import SubtitleLanguage, SubtitleTag, find_sibling_subtitle, subtitle_language_tag


def test_subtitle_language_carries_the_profile_codes():
    language = subtitle_language("ja")
    assert isinstance(language, SubtitleLanguage)
    assert language.mining == get_profile("ja").audio_track_codes


def test_known_covers_every_mining_language():
    known = subtitle_language("es").known
    assert {"ja", "en", "es", "pt", "zh"} <= known
    assert subtitle_language("es").mining <= known


def test_the_caption_codes_count_as_its_own_only_on_request():
    # yt-dlp names a yue caption file ep01.zh-HK.srt; Readability's scan keeps
    # it. Pairing reads the audio codes alone, where it is another language's.
    assert subtitle_language("yue").mining == get_profile("yue").audio_track_codes
    mining = subtitle_language("yue", with_caption_codes=True).mining
    assert {"zh-hk", "zh-hant", "zh-hant-hk"} <= mining
    assert "zh" not in mining


def test_a_zh_user_owns_the_regional_chinese_tags_but_not_cantonese():
    language = subtitle_language("zh")
    for tag in ("zh-HK", "zh-Hant"):
        assert subtitle_language_tag(Path(f"ep01.{tag}.srt"), language) is SubtitleTag.MINING
    assert subtitle_language_tag(Path("ep01.yue.srt"), language) is SubtitleTag.OTHER


def test_pairing_picks_the_yue_subtitle_over_a_caption_fallback(tmp_path):
    # A caption-code fallback must not tie with ep01.yue.srt: pairing refuses a tie.
    video = tmp_path / "ep01.mkv"
    for name in ("ep01.mkv", "ep01.yue.srt", "ep01.zh-HK.srt"):
        (tmp_path / name).touch()
    assert find_sibling_subtitle(video, language=subtitle_language("yue")) == tmp_path / "ep01.yue.srt"
