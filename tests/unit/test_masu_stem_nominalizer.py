"""Tests for MasuStemNominalizer — 連用形 stems that are really nouns."""

from unittest.mock import MagicMock

from anki_miner.services.masu_stem_nominalizer import MasuStemNominalizer
from anki_miner.services.morphology import SyntheticToken


def _tok(surface, pos1, pos2=None, lemma=None, kana=None, orth_base=None, c_form=None):
    token = MagicMock()
    token.surface = surface
    token.feature.pos1 = pos1
    token.feature.pos2 = pos2
    token.feature.lemma = lemma if lemma is not None else surface
    token.feature.kana = kana if kana is not None else surface
    token.feature.orthBase = orth_base if orth_base is not None else token.feature.lemma
    # MagicMock auto-creates a TRUTHY cForm; unidic's placeholder is "*".
    token.feature.cForm = c_form if c_form is not None else "*"
    return token


def _nominalizer(dictionary, spy=None):
    def lookup(terms):
        if spy is not None:
            spy.append(list(terms))
        return dictionary & set(terms)

    return MasuStemNominalizer(lookup)


def _sashiire():
    """差し入れ | みんな — the bug. unidic tags 差し入れ 動詞/連用形 across the
    space the cue's line break collapsed into."""
    return [
        _tok(
            "差し入れ",
            "動詞",
            "一般",
            lemma="差し入れる",
            kana="サシイレ",
            orth_base="差し入れる",
            c_form="連用形-一般",
        ),
        _tok("みんな", "名詞", "普通名詞", lemma="皆", kana="ミンナ", orth_base="みんな"),
    ]


class TestFires:
    def test_attested_stem_before_a_noun_becomes_nominal(self):
        out = _nominalizer({"差し入れ"}).rewrite_line(_sashiire())
        assert isinstance(out[0], SyntheticToken)
        assert out[0].surface == "差し入れ"
        assert out[0].feature.pos1 == "名詞"
        assert out[0].feature.pos2 == "普通名詞"

    def test_lemma_becomes_the_surface_so_mined_form_matches_the_card_front(self):
        out = _nominalizer({"差し入れ"}).rewrite_line(_sashiire())
        assert out[0].feature.lemma == "差し入れ"

    def test_reading_comes_from_the_original_token_not_a_retokenization(self):
        out = _nominalizer({"差し入れ"}).rewrite_line(_sashiire())
        assert out[0].feature.kana == "サシイレ"

    def test_neighbour_token_is_left_untouched(self):
        tokens = _sashiire()
        out = _nominalizer({"差し入れ"}).rewrite_line(tokens)
        assert out[1] is tokens[1]

    def test_every_allowed_neighbour_pos_fires(self):
        for pos1 in ("名詞", "代名詞", "副詞", "形状詞", "連体詞", "接頭辞", "感動詞"):
            tokens = _sashiire()
            tokens[1] = _tok("X", pos1)
            out = _nominalizer({"差し入れ"}).rewrite_line(tokens)
            assert out[0].feature.pos1 == "名詞", pos1


class TestBlocked:
    def _blocked(self, tokens, dictionary={"差し入れ"}):  # noqa: B006 - read-only
        out = _nominalizer(dictionary).rewrite_line(tokens)
        assert out is tokens

    def test_auxiliary_neighbour_blocks(self):
        """帰り|ました — ます only attaches to a verb stem."""
        tokens = _sashiire()
        tokens[1] = _tok("まし", "助動詞", "*", lemma="ます", kana="マシ")
        self._blocked(tokens)

    def test_particle_neighbour_blocks(self):
        """笑い|ながら — a conjunctive particle marks a verbal stem."""
        tokens = _sashiire()
        tokens[1] = _tok("ながら", "助詞", "接続助詞", kana="ナガラ")
        self._blocked(tokens)

    def test_verb_neighbour_blocks(self):
        """動き|出した — the second half of a compound verb."""
        tokens = _sashiire()
        tokens[1] = _tok("出し", "動詞", "非自立可能", lemma="出す", kana="ダシ", c_form="連用形-一般")
        self._blocked(tokens)

    def test_conjunction_neighbour_blocks(self):
        """走り|そして — 連用中止法 continues the clause."""
        tokens = _sashiire()
        tokens[1] = _tok("そして", "接続詞", "*", kana="ソシテ")
        self._blocked(tokens)

    def test_adjective_neighbour_blocks(self):
        """Proof the gate is an allow-list: 形容詞 is in no deny-list I could
        have written, and must still block."""
        tokens = _sashiire()
        tokens[1] = _tok("早い", "形容詞", "一般", kana="ハヤイ")
        self._blocked(tokens)

    def test_punctuation_neighbour_blocks(self):
        tokens = _sashiire()
        tokens[1] = _tok("。", "補助記号", "句点", kana="。")
        self._blocked(tokens)

    def test_bound_verb_stem_blocks(self):
        """非自立可能 stems (つけ, 願い, し) head no standalone noun."""
        tokens = _sashiire()
        tokens[0] = _tok(
            "つけ", "動詞", "非自立可能", lemma="付ける", kana="ツケ", orth_base="つける", c_form="連用形-一般"
        )
        self._blocked(tokens, {"つけ"})

    def test_single_character_stem_blocks(self):
        tokens = _sashiire()
        tokens[0] = _tok("見", "動詞", "一般", lemma="見る", kana="ミ", orth_base="見る", c_form="連用形-一般")
        self._blocked(tokens, {"見"})

    def test_non_continuative_form_blocks(self):
        tokens = _sashiire()
        tokens[0] = _tok(
            "食べる", "動詞", "一般", lemma="食べる", kana="タベル", orth_base="食べる", c_form="終止形-一般"
        )
        self._blocked(tokens, {"食べる"})

    def test_missing_c_form_blocks(self):
        """unidic's "*" placeholder, and synthetic tokens with no cForm at all."""
        tokens = _sashiire()
        tokens[0].feature.cForm = "*"
        self._blocked(tokens)

    def test_unattested_stem_blocks(self):
        """食べ is a 連用形 stem before a noun, but no dictionary attests it."""
        self._blocked(_sashiire(), set())

    def test_non_verb_token_blocks(self):
        """Already nominal — e.g. a compound the matcher merged first."""
        tokens = _sashiire()
        tokens[0] = _tok("差し入れ", "名詞", "普通名詞", kana="サシイレ")
        self._blocked(tokens)

    def test_final_token_never_fires(self):
        """End-of-line is a far weaker signal than a following content word:
        早く帰り must keep mining 帰る."""
        tokens = [_tok("帰り", "動詞", "一般", lemma="帰る", kana="カエリ", orth_base="帰る", c_form="連用形-一般")]
        self._blocked(tokens, {"帰り"})


class TestLookupAndPreservation:
    def test_exactly_one_lookup_call_per_line(self):
        spy: list = []
        _nominalizer({"差し入れ"}, spy=spy).rewrite_line(_sashiire())
        assert len(spy) == 1

    def test_no_lookup_when_no_structural_candidate(self):
        spy: list = []
        tokens = _sashiire()
        tokens[1] = _tok("まし", "助動詞", "*", lemma="ます", kana="マシ")
        _nominalizer({"差し入れ"}, spy=spy).rewrite_line(tokens)
        assert spy == []

    def test_input_list_never_mutated(self):
        tokens = _sashiire()
        snapshot = list(tokens)
        _nominalizer({"差し入れ"}).rewrite_line(tokens)
        assert tokens == snapshot

    def test_no_op_returns_the_same_list_object(self):
        tokens = _sashiire()
        assert _nominalizer(set()).rewrite_line(tokens) is tokens

    def test_token_without_feature_is_skipped(self):
        token = MagicMock()
        token.surface = "x"
        token.feature = None
        tokens = [token, _tok("みんな", "名詞")]
        assert _nominalizer({"x"}).rewrite_line(tokens) is tokens
