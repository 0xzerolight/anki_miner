"""subtitle_language: the mining language's tags, read against every language's."""

from anki_miner.languages.registry import get_profile, subtitle_language
from anki_miner.utils.file_pairing import SubtitleLanguage


def test_subtitle_language_carries_the_profile_codes():
    language = subtitle_language("ja")
    assert isinstance(language, SubtitleLanguage)
    assert language.mining == get_profile("ja").audio_track_codes


def test_known_covers_every_mining_language():
    known = subtitle_language("es").known
    assert {"ja", "en", "es", "pt", "zh"} <= known
    assert subtitle_language("es").mining <= known


def test_the_caption_codes_fetched_for_a_language_are_its_own():
    # yt-dlp names a yue caption file ep01.zh-HK.srt; zh's codes alone would
    # read that as another language's.
    mining = subtitle_language("yue").mining
    assert {"zh-hk", "zh-hant", "zh-hant-hk"} <= mining
    assert "zh" not in mining
