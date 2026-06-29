"""Tests for the bold-target helpers in text_utils (Issue #20)."""

from unittest.mock import MagicMock, PropertyMock

from anki_miner.utils.text_utils import (
    wrap_target_furigana,
    wrap_target_furigana_from_tokens,
    wrap_target_plain,
)


def _make_mock_token(surface, kana=None, has_feature=True):
    """Mirror the helper from test_text_utils to build mock fugashi tokens."""
    token = MagicMock()
    token.surface = surface
    if has_feature and kana is not None:
        token.feature.kana = kana
    elif has_feature and kana is None:
        token.feature.kana = None
    else:
        token.feature = MagicMock(spec=[])
        type(token.feature).kana = PropertyMock(side_effect=AttributeError)
    return token


class TestWrapTargetPlain:
    """``wrap_target_plain`` escapes around a bold span."""

    def test_basic_wrap(self):
        sentence = "まだ目覚めていないということだった"
        # target = "目覚めていない" at offset 2..9
        start = sentence.index("目覚めていない")
        end = start + len("目覚めていない")
        out = wrap_target_plain(sentence, start, end)
        assert out == "まだ<b>目覚めていない</b>ということだった"

    def test_target_at_start(self):
        sentence = "目覚めていないらしい"
        end = len("目覚めていない")
        assert wrap_target_plain(sentence, 0, end) == "<b>目覚めていない</b>らしい"

    def test_target_at_end(self):
        sentence = "まだ目覚めていない"
        start = sentence.index("目覚めていない")
        assert wrap_target_plain(sentence, start, len(sentence)) == "まだ<b>目覚めていない</b>"

    def test_escapes_special_chars_outside_bold(self):
        sentence = "A & B 食べる & C"
        start = sentence.index("食べる")
        end = start + len("食べる")
        out = wrap_target_plain(sentence, start, end)
        assert out == "A &amp; B <b>食べる</b> &amp; C"

    def test_escapes_special_chars_inside_bold(self):
        sentence = "<x> & y"
        out = wrap_target_plain(sentence, 0, 3)  # "<x>"
        assert out == "<b>&lt;x&gt;</b> &amp; y"

    def test_invalid_offsets_fall_back_to_escape(self):
        sentence = "A & B"
        assert wrap_target_plain(sentence, -1, 3) == "A &amp; B"
        assert wrap_target_plain(sentence, 0, 0) == "A &amp; B"
        assert wrap_target_plain(sentence, 0, 100) == "A &amp; B"
        assert wrap_target_plain(sentence, 5, 3) == "A &amp; B"


class TestWrapTargetFurigana:
    """``wrap_target_furigana`` bolds the target token's annotation."""

    def test_single_kanji_target_with_surrounding_kana(self):
        # スウェーデンや王国です。 — target = 王国 at the matching offset
        text = "スウェーデンや王国です。"
        tokens = [
            _make_mock_token("スウェーデン", kana="スウェーデン"),
            _make_mock_token("や", kana="ヤ"),
            _make_mock_token("王国", kana="オウコク"),
            _make_mock_token("です", kana="デス"),
            _make_mock_token("。", kana="。"),
        ]
        tagger = MagicMock(return_value=tokens)
        start = text.index("王国")
        end = start + len("王国")
        out = wrap_target_furigana(text, tagger, start, end)
        # generate_furigana would emit: "スウェーデンや 王国[おうこく]です。"
        # bolded variant moves the leading space into the prefix.
        assert out == "スウェーデンや <b>王国[おうこく]</b>です。"

    def test_target_without_kanji_no_ruby(self):
        text = "りんごです"
        tokens = [
            _make_mock_token("りんご", kana="リンゴ"),
            _make_mock_token("です", kana="デス"),
        ]
        tagger = MagicMock(return_value=tokens)
        out = wrap_target_furigana(text, tagger, 0, 3)  # "りんご"
        assert out == "<b>りんご</b>です"

    def test_target_with_okurigana(self):
        text = "食べる"
        tokens = [_make_mock_token("食べる", kana="タベル")]
        tagger = MagicMock(return_value=tokens)
        out = wrap_target_furigana(text, tagger, 0, 3)
        assert out == "<b>食[た]べる</b>"

    def test_target_at_start_no_leading_space(self):
        text = "王国です"
        tokens = [
            _make_mock_token("王国", kana="オウコク"),
            _make_mock_token("です", kana="デス"),
        ]
        tagger = MagicMock(return_value=tokens)
        out = wrap_target_furigana(text, tagger, 0, 2)
        assert out == "<b>王国[おうこく]</b>です"

    def test_invalid_offsets_fall_back_to_generate_furigana(self):
        text = "王国です"
        tokens = [
            _make_mock_token("王国", kana="オウコク"),
            _make_mock_token("です", kana="デス"),
        ]
        tagger = MagicMock(return_value=tokens)
        out = wrap_target_furigana(text, tagger, -1, 2)
        assert out == "王国[おうこく]です"

    def test_html_special_in_surface_is_escaped(self):
        text = "A & B"
        tokens = [
            _make_mock_token("A", kana="A"),
            _make_mock_token(" ", kana=" "),
            _make_mock_token("&", kana="&"),
            _make_mock_token(" ", kana=" "),
            _make_mock_token("B", kana="B"),
        ]
        tagger = MagicMock(return_value=tokens)
        # bold the "&"
        start = text.index("&")
        out = wrap_target_furigana(text, tagger, start, start + 1)
        assert out == "A <b>&amp;</b> B"

    def test_target_iteration_mark_kept_in_bracket(self):
        """Bold path: 々 is treated as kanji, so 時々 keeps its whole reading."""
        tokens = [_make_mock_token("時々", kana="トキドキ")]
        tagger = MagicMock(return_value=tokens)
        out = wrap_target_furigana("時々", tagger, 0, 2)
        assert out == "<b>時々[ときどき]</b>"

    def test_html_special_in_kanji_token_is_escaped(self):
        """Bold path: html-special chars in a kanji-bearing surface are escaped
        through the okurigana formatter (whole-bracket branch here)."""
        tokens = [_make_mock_token("A&B店", kana="エービーミセ")]
        tagger = MagicMock(return_value=tokens)
        out = wrap_target_furigana("A&B店", tagger, 0, 4)
        assert out == "<b>A&amp;B店[えーびーみせ]</b>"

    def test_target_after_internal_space(self):
        """Issue #31: MeCab drops whitespace from its token stream, so a
        naive cursor walk drifts left by 1 per preceding space and bolds
        the wrong morpheme. ``str.find`` keeps token offsets aligned to
        the raw ``text``."""
        text = "なんで 素直に"
        tokens = [
            _make_mock_token("なんで", kana="ナンデ"),
            _make_mock_token("素直", kana="スナオ"),
            _make_mock_token("に", kana="ニ"),
        ]
        tagger = MagicMock(return_value=tokens)
        start = text.index("素直")
        end = start + len("素直")
        out = wrap_target_furigana(text, tagger, start, end)
        assert out == "なんで <b>素直[すなお]</b>に"

    def test_target_after_multiple_internal_spaces(self):
        """Drift would compound across multiple preceding whitespace
        characters; verify the find-based walk stays aligned."""
        text = "なんで 素直に 好き"
        tokens = [
            _make_mock_token("なんで", kana="ナンデ"),
            _make_mock_token("素直", kana="スナオ"),
            _make_mock_token("に", kana="ニ"),
            _make_mock_token("好き", kana="スキ"),
        ]
        tagger = MagicMock(return_value=tokens)
        start = text.index("好き")
        end = start + len("好き")
        out = wrap_target_furigana(text, tagger, start, end)
        assert out == "なんで 素直[すなお]に <b>好[す]き</b>"

    def test_target_before_internal_space_unchanged(self):
        """Token preceding the first space was already correct under the
        naive cursor; guard against the find-based rewrite breaking it."""
        text = "素直に 言えない"
        tokens = [
            _make_mock_token("素直", kana="スナオ"),
            _make_mock_token("に", kana="ニ"),
            _make_mock_token("言え", kana="イエ"),
            _make_mock_token("ない", kana="ナイ"),
        ]
        tagger = MagicMock(return_value=tokens)
        start = text.index("素直")
        end = start + len("素直")
        out = wrap_target_furigana(text, tagger, start, end)
        assert out == "<b>素直[すなお]</b>に 言[い]えない"

    def test_no_spaces_unchanged_after_rewrite(self):
        """Regression guard: the no-space happy path that worked before
        Issue #31 must still produce identical output after the rewrite."""
        text = "スウェーデンや王国です。"
        tokens = [
            _make_mock_token("スウェーデン", kana="スウェーデン"),
            _make_mock_token("や", kana="ヤ"),
            _make_mock_token("王国", kana="オウコク"),
            _make_mock_token("です", kana="デス"),
            _make_mock_token("。", kana="。"),
        ]
        tagger = MagicMock(return_value=tokens)
        start = text.index("王国")
        end = start + len("王国")
        out = wrap_target_furigana(text, tagger, start, end)
        assert out == "スウェーデンや <b>王国[おうこく]</b>です。"


class TestWrapTargetFuriganaFromTokensEquivalence:
    """wrap_target_furigana_from_tokens must be byte-identical to wrap_target_furigana."""

    # Each entry: (text, tokens, start, end)
    CORPUS = [
        # Valid span — kanji target mid-sentence
        (
            "スウェーデンや王国です。",
            [
                _make_mock_token("スウェーデン", kana="スウェーデン"),
                _make_mock_token("や", kana="ヤ"),
                _make_mock_token("王国", kana="オウコク"),
                _make_mock_token("です", kana="デス"),
                _make_mock_token("。", kana="。"),
            ],
            "スウェーデンや王国です。".index("王国"),
            "スウェーデンや王国です。".index("王国") + len("王国"),
        ),
        # Valid span — target at start
        (
            "王国です",
            [
                _make_mock_token("王国", kana="オウコク"),
                _make_mock_token("です", kana="デス"),
            ],
            0,
            2,
        ),
        # Valid span — sentence with internal space (Issue #31)
        (
            "なんで 素直に",
            [
                _make_mock_token("なんで", kana="ナンデ"),
                _make_mock_token("素直", kana="スナオ"),
                _make_mock_token("に", kana="ニ"),
            ],
            "なんで 素直に".index("素直"),
            "なんで 素直に".index("素直") + len("素直"),
        ),
        # Invalid span — fallback path
        (
            "王国です",
            [
                _make_mock_token("王国", kana="オウコク"),
                _make_mock_token("です", kana="デス"),
            ],
            -1,
            2,
        ),
    ]

    def test_equivalence(self):
        for text, tokens, start, end in self.CORPUS:
            tagger = MagicMock(return_value=tokens)
            expected = wrap_target_furigana(text, tagger, start, end)
            actual = wrap_target_furigana_from_tokens(text, tokens, start, end)
            assert actual == expected, f"Mismatch for {text!r} [{start}:{end}]: {actual!r} != {expected!r}"
