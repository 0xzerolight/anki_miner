"""Word-level pinyin readings with tone marks (spec 9.1: never sandhi-adjusted)."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.token import LanguageToken
from anki_miner.languages.zh.reading import (
    _ER_IS_A_SYLLABLE,
    ZhReadingSupport,
    pinyin_syllables,
    reconcile_reading,
    syllable_tone,
    word_pinyin,
)
from anki_miner.languages.zh.render import ZhToneColorHook


class TestWordPinyin:
    def test_syllables_are_space_separated_with_tone_marks(self) -> None:
        assert word_pinyin("中国") == "zhōng guó"

    @pytest.mark.parametrize(("word", "expected"), [("重要", "zhòng yào"), ("重复", "chóng fù")])
    def test_polyphones_resolve_from_the_phrase_dictionary(self, word: str, expected: str) -> None:
        # The whole jieba segment is handed to pypinyin, never one char at a
        # time — that phrase lookup is the only thing that separates 重 chóng
        # from 重 zhòng.
        assert word_pinyin(word) == expected

    def test_non_hanzi_yields_an_empty_reading(self) -> None:
        assert word_pinyin("ok!") == ""

    @pytest.mark.parametrize(
        ("word", "expected"), [("銀行", "yín háng"), ("重複", "chóng fù"), ("音樂", "yīn yuè"), ("會計", "kuài jì")]
    )
    def test_traditional_polyphones_read_like_their_simplified_twin(self, word: str, expected: str) -> None:
        # pypinyin's phrase dictionary is simplified-only; a traditional word
        # would otherwise be read one character at a time (銀行 yín xíng).
        pytest.importorskip("opencc")
        assert word_pinyin(word) == expected


class TestErhua:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("这儿", "zhèr"),
            ("哪儿", "nǎr"),
            ("那儿", "nàr"),
            ("玩儿", "wánr"),
            ("会儿", "huìr"),
            ("一点儿", "yī diǎnr"),
            ("一会儿", "yī huìr"),
            ("有点儿", "yǒu diǎnr"),
        ],
    )
    def test_a_final_er_colours_the_previous_syllable(self, word: str, expected: str) -> None:
        # pypinyin has no erhua rule, so it returns 儿 as a syllable of its own
        # (zhè ér) — a pronunciation that does not exist.
        assert word_pinyin(word) == expected

    @pytest.mark.parametrize(("word", "expected"), [("女儿", "nǚ ér"), ("婴儿", "yīng ér"), ("新生儿", "xīn shēng ér")])
    def test_er_as_the_noun_head_keeps_its_own_syllable(self, word: str, expected: str) -> None:
        assert word_pinyin(word) == expected

    @pytest.mark.parametrize("word", sorted(_ER_IS_A_SYLLABLE))
    def test_every_excepted_word_really_ends_in_a_full_er(self, word: str) -> None:
        # A member pypinyin reads some other way would be an exception that
        # excepts nothing.
        assert word_pinyin(word).endswith(" ér")

    @pytest.mark.parametrize(("word", "expected"), [("儿子", "ér zi"), ("儿童", "ér tóng")])
    def test_a_non_final_er_is_untouched(self, word: str, expected: str) -> None:
        assert word_pinyin(word) == expected

    def test_a_mixed_token_merges_on_the_last_hanzi(self) -> None:
        # The Latin run is a syllable of its own, so the merge has to align on
        # the hanzi, not on the character before 儿.
        assert word_pinyin("T恤儿") == "T xùr"

    def test_a_traditional_word_erhuas_like_its_simplified_twin(self) -> None:
        pytest.importorskip("opencc")
        assert word_pinyin("這兒") == "zhèr"


class TestNonHanziRuns:
    """A word holding a Latin or digit run keeps it, and never leaks a hanzi."""

    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("T恤", "T xù"),
            ("AA制", "AA zhì"),
            ("卡拉OK", "kǎ lā OK"),
            ("X光", "X guāng"),
            ("U盘", "U pán"),
            ("WIFI密码", "WIFI mì mǎ"),
        ],
    )
    def test_a_non_hanzi_run_is_carried_through_verbatim(self, word: str, expected: str) -> None:
        # pypinyin returns one row per non-hanzi RUN, so the rows no longer line
        # up one-to-one with the characters they came from.
        assert word_pinyin(word) == expected

    @pytest.mark.parametrize("word", ["3D", "ok!", "PM2.5", ""])
    def test_a_word_with_no_hanzi_still_reads_as_nothing(self, word: str) -> None:
        assert word_pinyin(word) == ""

    def test_a_character_pypinyin_cannot_read_emits_nothing_not_itself(self) -> None:
        # errors="default" hands back the ORIGINAL CHARACTER for an unreadable
        # hanzi; emitting that verbatim would put a raw glyph in the reading.
        pytest.importorskip("opencc")
        assert word_pinyin("㘓哰") == "láo"


class TestCitationTones:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("一个", "yī gè"),
            ("一起", "yī qǐ"),
            ("一定", "yī dìng"),
            ("一般", "yī bān"),
            ("一样", "yī yàng"),
            ("一次", "yī cì"),
            ("一点", "yī diǎn"),
            ("不是", "bù shì"),
            ("不要", "bù yào"),
            ("不错", "bù cuò"),
        ],
    )
    def test_yi_and_bu_keep_their_citation_tone(self, word: str, expected: str) -> None:
        # pypinyin's phrase dictionary bakes the spoken sandhi into some rows
        # (一个 yí gè) and not others (一样 yī yàng), so cards disagreed with
        # each other and with CC-CEDICT.
        assert word_pinyin(word) == expected

    def test_a_lexical_neutral_bu_is_left_alone(self) -> None:
        # 差不多 chàbuduō is CC-CEDICT's own reading, not sandhi.
        assert word_pinyin("差不多") == "chà bu duō"

    def test_a_traditional_word_gets_the_citation_tone_too(self) -> None:
        pytest.importorskip("opencc")
        assert word_pinyin("一個") == "yī gè"


class TestSyllableTone:
    @pytest.mark.parametrize(
        ("syllable", "tone"), [("zhōng", 1), ("guó", 2), ("nǐ", 3), ("yào", 4), ("le", 5), ("", 5)]
    )
    def test_tone_comes_from_the_diacritic(self, syllable: str, tone: int) -> None:
        assert syllable_tone(syllable) == tone

    def test_an_erhua_syllable_carries_the_tone_of_its_vowel(self) -> None:
        assert syllable_tone("zhèr") == 4


class TestPinyinSyllables:
    def test_pairs_each_syllable_with_its_tone(self) -> None:
        assert pinyin_syllables("中国") == [("zhōng", 1), ("guó", 2)]

    def test_a_traditional_word_gets_its_simplified_twin_tones(self) -> None:
        pytest.importorskip("opencc")
        assert pinyin_syllables("銀行") == [("yín", 2), ("háng", 2)]

    def test_an_erhua_word_yields_one_pair_per_real_syllable(self) -> None:
        assert pinyin_syllables("这儿") == [("zhèr", 4)]


class TestToneColourOnCorrectedReadings:
    """The colour hook paints one span per syllable ``pinyin_syllables`` returns."""

    def _render(self, form: str) -> str:
        config = dataclasses.replace(AnkiMinerConfig(), reading_tone_color=True)
        word = SimpleNamespace(mined_form=form, definition_html="")
        return ZhToneColorHook().render(word, config=config)["expression_pinyin"]

    def test_erhua_is_one_span_not_two(self) -> None:
        # The phantom 儿 used to get an orange tone-2 span of its own.
        assert self._render("这儿") == '<span style="color:#4286e5">zhèr</span>'

    def test_a_citation_tone_moves_the_span_colour(self) -> None:
        assert self._render("一个").startswith('<span style="color:#e75353">yī</span>')


class TestReconcileReading:
    """The dictionary's own reading, re-spaced onto the word's characters."""

    @pytest.mark.parametrize(
        ("word", "attested", "expected"),
        [
            ("看得见", "kàndejiàn", "kàn de jiàn"),  # 得 neutral, pypinyin says dé
            ("先生", "xiānsheng", "xiān sheng"),  # neutral tone the dictionary records
            ("学生", "xuésheng", "xué sheng"),
            ("流血", "liúxuè", "liú xuè"),  # heteronym pypinyin picks the other way
            ("这儿", "zhèr5", "zhèr"),  # erhua merge + the trailing neutral marker
            ("隻", "zhī", "zhī"),  # one character whose simplified fold reads 只 zhǐ
            ("中国", "zhōngguó", "zhōng guó"),
        ],
    )
    def test_a_single_attested_reading_is_re_spaced_onto_the_word(
        self, word: str, attested: str, expected: str
    ) -> None:
        assert reconcile_reading(word, word_pinyin(word), [attested]) == expected

    @pytest.mark.parametrize(
        ("word", "attested"),
        [
            ("嘸啥", ["m2shá"]),  # numbered pinyin: unusable outright
            ("樂亭", ["làotíng"]),  # a place-name reading pypinyin has no candidate for
            ("起来", ["qǐlái", "qilai"]),  # two attested readings: no way to choose
            ("银行", []),  # nothing attested
            ("银行", [""]),
        ],
    )
    def test_a_reading_the_walk_cannot_place_keeps_pypinyins_answer(self, word: str, attested: list[str]) -> None:
        assert reconcile_reading(word, word_pinyin(word), attested) == word_pinyin(word)

    def test_an_ambiguous_split_is_refused_rather_than_guessed(self, monkeypatch) -> None:
        """Two splits of one attested string is a parse the walk must not pick from.

        Forced with overlapping candidate sets: xi|an and xia|n both consume
        ``xian``. A first-match walk would silently emit one of them.
        """
        stub = {"西": ("xi", "xia"), "安": ("an", "n")}
        monkeypatch.setattr(
            "anki_miner.languages.zh.reading._char_candidates",
            lambda char: stub.get(char, ()),
        )
        assert reconcile_reading("西安", "xī ān", ["xian"]) == "xī ān"

    @pytest.mark.parametrize(
        ("word", "attested", "expected"),
        [
            ("賠不是", "péibúshi", "péi bù shi"),  # sandhi 不 restored to its citation tone
            ("看不见", "kànbujiàn", "kàn bu jiàn"),  # lexical neutral 不 left alone
        ],
    )
    def test_the_citation_tone_policy_still_applies(self, word: str, attested: str, expected: str) -> None:
        assert reconcile_reading(word, word_pinyin(word), [attested]) == expected

    def test_the_emitted_syllables_rejoin_to_the_attested_string(self) -> None:
        """The kill-rule invariant: a reading that matched the dictionary still does."""
        for word, attested in (("看得见", "kàndejiàn"), ("学生", "xuésheng"), ("中国", "zhōngguó")):
            assert reconcile_reading(word, word_pinyin(word), [attested]).replace(" ", "") == attested

    def test_a_latin_run_keeps_its_source_spelling(self) -> None:
        # The walk consumes a casefolded target but emits the source slice.
        assert reconcile_reading("T恤", "T xù", ["T xù"]) == "T xù"


class TestZhReadingSupport:
    def test_word_reading_reads_the_token_surface(self) -> None:
        token = LanguageToken(surface="电影", pos1="n", lemma="电影")
        assert ZhReadingSupport().word_reading(token) == "diàn yǐng"

    def test_reconcile_is_offered_beside_word_reading(self) -> None:
        # The parser seam is a getattr probe, so the method's presence IS the gate.
        assert ZhReadingSupport().reconcile("先生", "xiān shēng", ["xiānsheng"]) == "xiān sheng"
