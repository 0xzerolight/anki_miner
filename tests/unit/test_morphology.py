"""Direct unit tests for morphology.mining_base and extract_lemma.

Uses SimpleNamespace-shaped tokens (like morphology's own SyntheticToken),
NOT MagicMock — auto-created Mock attributes are truthy and would silently
exercise mining_base's isinstance guard instead of the intended branch.
"""

from types import SimpleNamespace

import pytest

from anki_miner.services.morphology import SyntheticToken, mining_base


def _token(surface, pos1, lemma, orth_base=None, l_form=None, kana_base=None):
    return SimpleNamespace(
        surface=surface,
        feature=SimpleNamespace(
            pos1=pos1,
            pos2="一般",
            lemma=lemma,
            kana=surface,
            orthBase=orth_base if orth_base is not None else lemma,
            lForm=l_form,
            kanaBase=kana_base,
        ),
    )


class TestMiningBaseFold:
    """Fold matrix: derived sub-lemmas collapse onto the parent lemma."""

    @pytest.mark.parametrize(
        ("orth_base", "lemma", "l_form", "kana_base"),
        [
            ("保てる", "保つ", "タモツ", "タモテル"),
            ("読める", "読む", "ヨム", "ヨメル"),
            ("書ける", "書く", "カク", "カケル"),
            ("泳げる", "泳ぐ", "オヨグ", "オヨゲル"),
            ("話せる", "話す", "ハナス", "ハナセル"),
            ("死ねる", "死ぬ", "シヌ", "シネル"),
            ("呼べる", "呼ぶ", "ヨブ", "ヨベル"),
            ("掴める", "掴む", "ツカム", "ツカメル"),
            ("買える", "買う", "カウ", "カエル"),
            # ら抜き
            ("見れる", "見る", "ミル", "ミレル"),
            ("食べれる", "食べる", "タベル", "タベレル"),
            ("来れる", "来る", "クル", "コレル"),
            # compound + katakana verbs
            ("取り戻せる", "取り戻す", "トリモドス", "トリモドセル"),
            ("サボれる", "サボる", "サボル", "サボレル"),
        ],
    )
    def test_folds_verb(self, orth_base, lemma, l_form, kana_base):
        token = _token(orth_base, "動詞", lemma, orth_base, l_form, kana_base)
        assert mining_base(token) == lemma

    @pytest.mark.parametrize(
        ("orth_base", "lemma", "l_form", "kana_base"),
        [
            ("良し", "良い", "ヨイ", "ヨシ"),
            ("多し", "多い", "オオイ", "オオシ"),
            ("少なし", "少ない", "スクナイ", "スクナシ"),
        ],
    )
    def test_folds_adjective_ku_form(self, orth_base, lemma, l_form, kana_base):
        token = _token(orth_base, "形容詞", lemma, orth_base, l_form, kana_base)
        assert mining_base(token) == lemma


class TestMiningBaseGuard:
    """Suffix-pair guard: unidic lemma canonicalization never leaks."""

    @pytest.mark.parametrize(
        ("orth_base", "lemma", "l_form", "kana_base"),
        [
            # kanji swap (leading and non-leading position)
            ("帰れる", "返る", "カエル", "カエレル"),
            ("混ぜれる", "交ぜる", "マゼル", "マゼレル"),
            ("出逢える", "出会う", "デアウ", "デアエル"),
            ("巡り合える", "巡り会う", "メグリアウ", "メグリアエル"),
            # okurigana variant (same kanji — invisible to kanji-based guards)
            ("表せる", "表わす", "アラワス", "アラワセル"),
            ("行なえる", "行う", "オコナウ", "オコナエル"),
            ("落せる", "落とす", "オトス", "オトセル"),
            # modern→archaic じる/ずる
            ("信じる", "信ずる", "シンズル", "シンジル"),
            ("感じる", "感ずる", "カンズル", "カンジル"),
        ],
    )
    def test_blocks_canonicalizing_lemma(self, orth_base, lemma, l_form, kana_base):
        token = _token(orth_base, "動詞", lemma, orth_base, l_form, kana_base)
        assert mining_base(token) == orth_base

    def test_polyphonic_same_string_stays(self):
        """言う: readings diverge but lemma == orthBase — output identical."""
        token = _token("言う", "動詞", "言う", "言う", "イウ", "ユウ")
        assert mining_base(token) == "言う"


class TestMiningBaseNoTrigger:
    """No-fold branches: readings equal / missing, POS gate, synthetics."""

    def test_readings_equal_keeps_orth_base_variant(self):
        """乞う: orthBase keeps the source kanji variant of lemma 請う."""
        token = _token("乞わ", "動詞", "請う", "乞う", "コウ", "コウ")
        assert mining_base(token) == "乞う"

    @pytest.mark.parametrize("lemma", ["見える", "聞こえる"])
    def test_lexicalized_potentials_keep_own_lemma(self, lemma):
        token = _token(lemma, "動詞", lemma, lemma, "ヨミ", "ヨミ")
        assert mining_base(token) == lemma

    def test_pos_gate_excludes_nouns(self):
        token = _token("山", "名詞", "別", "山", "ベツ", "ヤマ")
        assert mining_base(token) == "山"

    @pytest.mark.parametrize(
        ("l_form", "kana_base"),
        [(None, None), ("*", "*"), ("タモツ", None), (None, "タモテル")],
    )
    def test_missing_or_placeholder_readings_never_fold(self, l_form, kana_base):
        token = _token("保てる", "動詞", "保つ", "保てる", l_form, kana_base)
        assert mining_base(token) == "保てる"

    def test_synthetic_token_never_raises(self):
        """SyntheticToken features carry no orthBase/lForm/kanaBase — must
        fall back to extract_lemma and never crash."""
        token = SyntheticToken("走り出す", "動詞", "一般", "走り出す", "ハシリダス")
        assert mining_base(token) == "走り出す"
