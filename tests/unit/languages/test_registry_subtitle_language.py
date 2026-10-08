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
