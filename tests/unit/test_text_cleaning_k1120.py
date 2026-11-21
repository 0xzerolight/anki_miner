"""Text cleaning and cue gating on streaming captions, caption-rip ASS and fansub ASS (session K1120).

Every line here is hand-written in the shape the corpus files have.
"""

from __future__ import annotations

import pysubs2
import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import _create_subtitle_parser
from anki_miner.languages.registry import get_profile
from anki_miner.services.subtitle_cleaner import mining_events
from anki_miner.utils.ja_normalize import strip_sound_labels
from anki_miner.utils.text_utils import clean_subtitle_text


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


needs_tagger = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")
#: The two places the Japanese parser is built; both must clean a file the same way.
JA_BUILDERS = [_create_subtitle_parser, get_profile("ja").create_parser]


def _srt(tmp_path, *lines: str):
    path = tmp_path / "sub.srt"
    cues = "".join(f"{i}\n00:00:{i:02},000 --> 00:00:{i:02},900\n{line}\n\n" for i, line in enumerate(lines, 1))
    path.write_text(cues, encoding="utf-8")
    return path


def _listed(parser, path) -> list[str]:
    return [text for _start, _end, text in parser.parse_raw_entries(path)]


def _event(text: str, *, start: int = 0, end: int = 2000, style: str = "Default") -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(start=start, end=end, text=text, style=style)


def _mined_texts(events: list[pysubs2.SSAEvent], *, skip_non_dialogue: bool = True) -> list[str]:
    return [event.plaintext for event in mining_events(events, skip_non_dialogue=skip_non_dialogue)]


class TestRubyReadingEvents:
    """A caption rip writes a ruby reading as its own half-size event above the caption row."""

    def test_reading_row_goes_and_the_caption_row_stays(self):
        events = [
            _event(r"{\pos(272,443)\fscx50\fscy50}ぼく"),
            _event(r"{\pos(272,497)}僕の番だ{\fscx50}。"),
        ]
        assert _mined_texts(events) == ["僕の番だ。"]

    def test_several_reading_rows_over_one_caption_row(self):
        events = [
            _event(r"{\pos(432,443)\fscx50\fscy50}きょうだい"),
            _event(r"{\pos(572,443)\fscx50\fscy50}く"),
            _event(r"{\pos(172,497)}いずれ{\fscx50}　{\fscx100}ほかの兄妹も暮らす"),
        ]
        assert _mined_texts(events) == ["いずれ　ほかの兄妹も暮らす"]

    def test_dropped_without_the_opening_ending_switch(self):
        events = [_event(r"{\pos(272,443)\fscx50\fscy50}ぼく"), _event(r"{\pos(272,497)}僕の番だ")]
        assert _mined_texts(events, skip_non_dialogue=False) == ["僕の番だ"]

    def test_half_width_only_is_speech(self):
        events = [
            _event(r"{\pos(412,497)\fscx50}ターンエンド{\fscx100}です{\fscx50}。"),
            _event(r"{\pos(552,407)\fscx50}フン。", start=3000, end=4000),
        ]
        assert _mined_texts(events) == ["ターンエンドです。", "フン。"]

    def test_two_thirds_size_dialogue_stays(self):
        events = [_event(r"{\pos(960,1050)\fscx67\fscy67}それでは")]
        assert _mined_texts(events) == ["それでは"]

    def test_half_size_start_that_grows_back_is_speech(self):
        events = [_event(r"{\pos(272,497)\fscx50\fscy50}あの{\fscx100\fscy100}人は来ない")]
        assert _mined_texts(events) == ["あの人は来ない"]

    def test_scale_set_after_the_first_block_is_not_a_reading_row(self):
        events = [_event(r"{\pos(272,497)}今日は{\fscx50\fscy50}いい天気")]
        assert _mined_texts(events) == ["今日はいい天気"]


class TestDoubleParenSpeech:
    """TV captions print recalled or thought speech in a doubled parenthesis, not a sound label."""

    def test_whole_cue_is_kept_in_white_parens(self):
        assert clean_subtitle_text("((そういう提案です))") == "｟そういう提案です｠"

    def test_full_width_pair(self):
        assert clean_subtitle_text("（（そういう提案です））") == "｟そういう提案です｠"

    def test_speaker_label_before_it_still_goes(self):
        assert clean_subtitle_text("(健)((お願いだ！))") == "｟お願いだ！｠"

    def test_speech_wrapped_over_two_lines(self):
        assert clean_subtitle_text("((あの時は\\N本当にすまなかった))") == "｟あの時は 本当にすまなかった｠"

    def test_reading_inside_the_speech_is_still_stripped(self):
        assert clean_subtitle_text("((水篠(みずしの)が来た))") == "｟水篠が来た｠"

    def test_opened_here_and_closed_in_a_later_cue(self):
        assert clean_subtitle_text("((昨日の話だけど") == "｟昨日の話だけど"
        assert clean_subtitle_text("(健)((昨日の話だけど") == "｟昨日の話だけど"
        assert clean_subtitle_text("もう忘れてくれ))") == "もう忘れてくれ｠"

    def test_single_parens_are_unchanged(self):
        assert clean_subtitle_text("（ドアが開く音）") == ""
        assert clean_subtitle_text("（健）待ってくれ") == "待ってくれ"

    def test_close_of_a_nested_reading_is_not_a_double_paren(self):
        assert clean_subtitle_text("(水篠(みずしの))待て") == "待て"
        assert clean_subtitle_text("(水篠(みずしの))") == ""
        assert clean_subtitle_text("((健)の声)") == ""

    @needs_tagger
    @pytest.mark.parametrize("build", JA_BUILDERS)
    def test_speech_survives_the_default_cleanup_and_is_mined(self, tmp_path, build):
        parser = build(AnkiMinerConfig())
        path = _srt(tmp_path, "(健)((約束を守れ)) (花)((嫌だよ))", "今日は((提案があります))", "（ドアが開く音）")

        assert _listed(parser, path) == ["｟約束を守れ｠ ｟嫌だよ｠", "今日は｟提案があります｠"]
        assert {"約束", "守る", "提案"} <= {word.mined_form for word in parser.parse_subtitle_file(path)}


class TestTortoiseBracketSoundLabels:
    """A caption service writes sound descriptions as 〔…〕; the same brackets also carry speech."""

    @staticmethod
    def _clean(text: str) -> str:
        return clean_subtitle_text(text, strip_labels=strip_sound_labels)

    @pytest.mark.parametrize(
        "label",
        [
            "〔ドアが開く音〕",
            "〔銃声〕",
            "〔荒い息〕",
            "〔拍手〕",
            "〔悲鳴〕",
            "〔ノック〕",
            "〔チャイム〕",
            "〔ため息〕",
            "〔ざわめき〕",
        ],
    )
    def test_a_cue_that_is_a_sound_label_is_dropped(self, label):
        assert self._clean(label) == ""

    def test_label_beside_speech_goes_and_the_speech_stays(self):
        assert self._clean("〔鍵を掛ける音〕 待って") == "待って"
        assert self._clean("危ない！〔銃声〕") == "危ない！"

    def test_a_voice_label_goes_like_a_speaker_label(self):
        assert self._clean("〔晴子の声〕早く来て") == "早く来て"

    def test_label_wrapped_over_two_lines(self):
        assert self._clean("〔ドアが\\N開く音〕") == ""

    def test_a_paren_label_after_the_sound_label_is_still_stripped(self):
        assert self._clean("〔鍵を掛ける音〕（健）待って") == "待って"

    @pytest.mark.parametrize(
        "speech", ["〔考えろ！〕", "〔お父さん…〕", "〔もしもし 聞こえる？〕", "〔何の音？〕", "〔パン！〕"]
    )
    def test_speech_in_the_same_brackets_stays(self, speech):
        assert self._clean(speech) == speech

    def test_a_long_bracket_is_not_a_label(self):
        speech = "〔あの夜に確かに聞こえたのは彼女の声〕"
        assert self._clean(speech) == speech

    def test_shared_cleaning_keeps_the_bracket(self):
        # The rule drops text for not being Japanese speech, so only the Japanese factory passes it.
        assert clean_subtitle_text("〔ドアが開く音〕") == "〔ドアが開く音〕"

    @needs_tagger
    @pytest.mark.parametrize("build", JA_BUILDERS)
    def test_japanese_parser_mines_nothing_from_a_sound_label(self, tmp_path, build):
        parser = build(AnkiMinerConfig())
        path = _srt(tmp_path, "〔ドアが開く音〕", "〔銃声〕 危ない", "(健)〔約束を守れ！〕")

        assert _listed(parser, path) == ["危ない", "〔約束を守れ！〕"]
        mined = {word.mined_form for word in parser.parse_subtitle_file(path)}
        assert "約束" in mined
        assert not {"ドア", "開く", "音", "銃声"} & mined
