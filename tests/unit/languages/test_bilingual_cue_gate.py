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
