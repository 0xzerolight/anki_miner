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
from anki_miner.utils.ja_normalize import chinese_style_lines, strip_caption_labels, strip_sound_labels
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


# Lines of a made-up bilingual fansub file. is_chinese_line names the first ten
# Chinese lines (a simplified form, or 你 without kana) and not the last two.
_CHINESE_NAMED = [
    "你今天怎么样",
    "我们走吧",
    "这是什么东西",
    "他说的对吗",
    "谢谢你的帮助",
    "请问现在几点",
    "我觉得很好",
    "这个问题很难",
    "对不起我来晚了",
    "没关系别担心",
]
_CHINESE_UNNAMED = ["不要", "明白"]
_JAPANESE_KANA = [
    "今日はいい天気ですね",
    "早く学校に行こう",
    "約束を守ってください",
    "この本は面白いよ",
    "駅まで歩きましょう",
    "猫が窓から逃げた",
    "明日は雨が降るらしい",
    "静かにしてくれ",
    "荷物を運ぶのを手伝って",
    "彼女は医者になった",
]
_JAPANESE_KANA_LESS = ["了解", "先生..."]


def _ass(tmp_path, rows: list[tuple[str, str]]):
    subs = pysubs2.SSAFile()
    for style in {style for style, _text in rows}:
        subs.styles[style] = pysubs2.SSAStyle()
    for i, (style, text) in enumerate(rows):
        subs.events.append(pysubs2.SSAEvent(start=i * 2000, end=i * 2000 + 1500, text=text, style=style))
    path = tmp_path / "sub.ass"
    subs.save(str(path))
    return path


class TestChineseStyleOfABilingualFile:
    """is_chinese_line misses a short Chinese line; the style it sits in names it."""

    def test_kana_less_lines_of_a_chinese_style_are_named(self):
        chinese = [("Dial-CH", text) for text in _CHINESE_NAMED + _CHINESE_UNNAMED]
        japanese = [("Dial-JP", text) for text in _JAPANESE_KANA + _JAPANESE_KANA_LESS]
        assert chinese_style_lines(chinese + japanese) == [True] * 12 + [False] * 12

    def test_a_line_with_kana_in_a_chinese_style_stays(self):
        lines = [("Dial-CH", text) for text in [*_CHINESE_NAMED, *_CHINESE_UNNAMED, "「你好」って言ってみて"]]
        assert chinese_style_lines(lines) == [True] * 12 + [False]

    def test_a_japanese_style_with_kana_less_lines_is_not_chinese(self):
        lines = [("Default", text) for text in _JAPANESE_KANA + _JAPANESE_KANA_LESS * 4]
        assert not any(chinese_style_lines(lines))

    def test_a_style_that_holds_both_languages_is_not_chinese(self):
        lines = [("Default", text) for text in _CHINESE_NAMED + _JAPANESE_KANA + _CHINESE_UNNAMED]
        assert not any(chinese_style_lines(lines))

    def test_a_style_of_a_few_lines_is_not_judged(self):
        lines = [("Dial-CH", text) for text in _CHINESE_NAMED[:7] + _CHINESE_UNNAMED]
        assert not any(chinese_style_lines(lines))

    @needs_tagger
    @pytest.mark.parametrize("build", JA_BUILDERS)
    def test_japanese_parser_mines_nothing_from_the_chinese_style(self, tmp_path, build):
        japanese = _JAPANESE_KANA + _JAPANESE_KANA_LESS
        chinese = _CHINESE_NAMED + _CHINESE_UNNAMED
        rows = [
            row for pair in zip(japanese, chinese, strict=True) for row in (("Dial-JP", pair[0]), ("Dial-CH", pair[1]))
        ]
        parser = build(AnkiMinerConfig())
        path = _ass(tmp_path, rows)

        mined = {word.mined_form for word in parser.parse_subtitle_file(path)}
        assert {"了解", "先生", "約束"} <= mined
        assert not {"不要", "明白"} & mined
        # Still listed: previews and the line list show the file as written.
        assert "不要" in _listed(parser, path)

    @needs_tagger
    @pytest.mark.parametrize("build", JA_BUILDERS)
    def test_japanese_only_file_keeps_its_kana_less_lines(self, tmp_path, build):
        rows = [("Default", text) for text in _JAPANESE_KANA] + [("Top", text) for text in _JAPANESE_KANA_LESS * 6]
        parser = build(AnkiMinerConfig())

        assert "了解" in {word.mined_form for word in parser.parse_subtitle_file(_ass(tmp_path, rows))}


class TestSongStyleNames:
    """Song styles of fansub ASS that carry a digit or a language code glued to OP / ED."""

    @staticmethod
    def _file(style: str) -> list[pysubs2.SSAEvent]:
        dialogue = [_event("今日は学校に行く", start=i * 3000, end=i * 3000 + 2500, style="Dial-JP") for i in range(5)]
        return [*dialogue, _event(r"{\fad(400,400)\blur2.5}変わらないよ", start=20000, end=22000, style=style)]

    @pytest.mark.parametrize(
        "style",
        [
            "EDJP",
            "OPJP",
            "OPJ",
            "EDJ",
            "JPED",
            "OP1",
            "ED2-JP",
            "OPCN",
            "OPCH",
            "OPED_CH",
            "Insert",
            "IN-JP",
            "injp",
            "Music",
            "*music",
            "Lyrics",
            "歌詞",
            "OP歌词",
            "片头",
            "片尾曲",
            "主题曲",
        ],
    )
    def test_song_style_is_dropped(self, style):
        assert "変わらないよ" not in _mined_texts(self._file(style))

    @pytest.mark.parametrize(
        "style",
        [
            "Text - JP - Top",
            "JP-UP",
            "jp_ontop",
            "Dial-JP2",
            "Intro",
            "Edward",
            "Opinion",
            "Chopin",
            "Main JP",
            "music - up",
        ],
    )
    def test_dialogue_style_stays(self, style):
        assert "変わらないよ" in _mined_texts(self._file(style))

    def test_main_style_named_like_a_song_is_never_dropped(self):
        events = [_event("今日は学校に行く", start=i * 3000, end=i * 3000 + 2500, style="EDJP") for i in range(5)]
        assert len(_mined_texts(events)) == 5

    def test_kept_when_the_opening_ending_switch_is_off(self):
        assert "変わらないよ" in _mined_texts(self._file("EDJP"), skip_non_dialogue=False)


class TestWaveDashInterjections:
    """う～ん, よ～い, わ～い, せ～の: the wave dash is the word's own long vowel."""

    @pytest.mark.parametrize(
        ("raw", "clean"),
        [
            ("う～ん どうしようかな", "うーん どうしようかな"),
            ("う〜ん…", "うーん…"),
            ("ううう～～ん", "うううーん"),
            ("よ～い ドン！", "よーい ドン！"),
            ("わ～い やった", "わーい やった"),
            ("せ～の！", "せーの！"),
        ],
    )
    def test_respelled_with_the_long_mark(self, raw, clean):
        assert clean_subtitle_text(raw) == clean

    @pytest.mark.parametrize(
        "line",
        [
            "すご～い",
            "は～い 分かりました",
            "足が う～んと速いから",
            "よ～いしょ",
            "うわ～い",
            "かわ～いい",
            "違う～んだよ",
            "卑わ～い",
            "2〜3日",
        ],
    )
    def test_every_other_wave_dash_is_left_to_the_cleanup_pattern(self, line):
        assert clean_subtitle_text(line) == line

    @needs_tagger
    @pytest.mark.parametrize("build", JA_BUILDERS)
    def test_a_hesitation_is_not_mined_as_yes(self, tmp_path, build):
        parser = build(AnkiMinerConfig())
        path = _srt(tmp_path, "う～ん どうしようかな", "すご～い")

        assert _listed(parser, path) == ["うーん どうしようかな", "すごい"]
        assert "うん" not in {word.mined_form for word in parser.parse_subtitle_file(path)}


class TestSceneLabelLines:
    """A caption rip boxes 回想 (a flashback) and 心の声 (a thought) as a row of their own."""

    @staticmethod
    def _clean(text: str) -> str:
        return clean_subtitle_text(text, strip_labels=strip_caption_labels)

    @pytest.mark.parametrize("label", ["回想", "心の声", r"{\pos(300,400)}回想", "　心の声　"])
    def test_a_cue_that_is_only_the_label_is_dropped(self, label):
        assert self._clean(label) == ""

    def test_label_row_above_speech_goes_and_the_speech_stays(self):
        assert self._clean("回想\\Nあの日は雨だった") == "あの日は雨だった"
        assert self._clean("心の声\\Nどうしよう") == "どうしよう"

    @pytest.mark.parametrize(
        "line",
        ["あの日の回想にふけっていた。", "僕たち 心の声が たまに聞こえちゃうんだ", "心の声を聞けば", "回想シーン"],
    )
    def test_the_words_inside_a_sentence_stay(self, line):
        assert self._clean(line) == line

    def test_sound_labels_still_go(self):
        assert self._clean("〔銃声〕 危ない") == "危ない"

    @needs_tagger
    @pytest.mark.parametrize("build", JA_BUILDERS)
    def test_japanese_parser_lists_no_label_row(self, tmp_path, build):
        parser = build(AnkiMinerConfig())
        path = _srt(tmp_path, "回想", "心の声", "あの日の回想にふけっていた。")

        assert _listed(parser, path) == ["あの日の回想にふけっていた。"]
        assert "回想" in {word.mined_form for word in parser.parse_subtitle_file(path)}
