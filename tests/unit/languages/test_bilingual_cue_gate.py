"""The non-Latin spaced languages drop a bilingual cue's English line (ZH-046, KO-06)."""

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language

#: Spaced-pipeline languages whose script gate tells a native line from an English one.
_NON_LATIN_SPACED = ("yue", "el", "ru", "uk", "he", "ar", "fa", "th")


@pytest.mark.parametrize("code", _NON_LATIN_SPACED)
def test_factory_closes_the_bilingual_line_seam(code, monkeypatch):
    from anki_miner.services import subtitle_parser

    seen: dict[str, object] = {}
    monkeypatch.setattr(subtitle_parser, "SubtitleParserService", lambda config, **kwargs: seen.update(kwargs))
    get_profile(code).create_parser(switch_language(AnkiMinerConfig(), code))
    gate = seen["has_target_script"]
    assert gate is not None
    assert gate("Let's go eat tonight.") is False


def test_a_russian_bilingual_cue_keeps_its_english_line_out_of_the_sentence(tmp_path):
    parser = get_profile("ru").create_parser(switch_language(AnkiMinerConfig(), "ru"))
    srt = tmp_path / "bilingual.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nЯ сейчас иду домой\nI am going home now.\n\n", encoding="utf-8")
    assert {w.sentence for w in parser.parse_subtitle_file(srt)} == {"Я сейчас иду домой"}


def _ja_parsers():
    """Every way a ja parser is built: the profile factory and service_factory's two literal branches."""
    from anki_miner.gui.utils import service_factory
    from anki_miner.services.subtitle_parser import SubtitleParserService

    return {
        "profile": lambda config: get_profile("ja").create_parser(config),
        "run": service_factory.create_line_parser,
        "display": lambda config: service_factory.create_profile_parser(config, SubtitleParserService),
    }


@pytest.mark.parametrize("builder", ["profile", "run", "display"])
def test_a_japanese_cue_keeps_its_romaji_line_out_of_the_sentence(builder, tmp_path):
    """YouTube lyric captions put a romaji line under the Japanese one."""
    srt = tmp_path / "romaji.srt"
    srt.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\n今日も学校に行く\nkyou mo gakkou ni iku\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\n明日は休みだ\n\n"
        "3\n00:00:07,000 --> 00:00:09,000\nSee you\ntomorrow\n\n",
        encoding="utf-8",
    )
    parser = _ja_parsers()[builder](AnkiMinerConfig())
    assert [text for _, _, text in parser.parse_raw_entries(srt)] == [
        "今日も学校に行く",
        "明日は休みだ",
        "See you tomorrow",
    ]
