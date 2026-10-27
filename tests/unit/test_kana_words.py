"""Tests for the kana word list: kana-only words the parser mines by name."""

from dataclasses import replace

import pytest

from anki_miner.models.reading import ReadingUnit
from anki_miner.services.kana_words import is_kanji_spelled, is_listed, kana_words
from anki_miner.services.subtitle_parser import SubtitleParserService


def _mine(test_config, *lines):
    """Card fronts of ``lines`` with every front attested, as a full JMdict would."""
    service = SubtitleParserService(test_config, kana_attest_lookup=lambda forms: dict.fromkeys(forms, True))
    units = [ReadingUnit(text=line, index=i, location_label="t") for i, line in enumerate(lines)]
    words, _index, _counts = service.parse_text_units(units, want_line_index=False)
    return [word.mined_form for word in words]


def _mine_attested(test_config, attested, *lines):
    """Card fronts of ``lines`` with only ``attested`` in the dictionary, so a window can miss."""
    service = SubtitleParserService(
        test_config, kana_attest_lookup=lambda forms: {form: form in attested for form in forms}
    )
    units = [ReadingUnit(text=line, index=i, location_label="t") for i, line in enumerate(lines)]
    words, _index, _counts = service.parse_text_units(units, want_line_index=False)
    return [word.mined_form for word in words]


class TestList:
    @pytest.mark.parametrize(
        ("front", "pos1"),
        [
            ("もう", "副詞"),
            ("この", "連体詞"),
            ("それ", "代名詞"),
            ("しかし", "接続詞"),
            ("こと", "名詞"),
            ("おにぎり", "名詞"),
        ],
    )
    def test_lists_a_kana_word_under_its_tag(self, front, pos1):
        assert is_listed(front, pos1, merged=False)

    @pytest.mark.parametrize(
        ("front", "pos1"),
        [
            ("その", "感動詞"),  # the filler, unlike その日's pre-noun adjective
            ("ある", "動詞"),  # the auxiliary-capable verb
            ("けど", "接続詞"),  # the particle's conjunction reading
            ("さえ", "名詞"),  # a particle a carrier sentence tags as a noun
            ("ねた", "名詞"),  # a two-kana noun off the short list (まね|た)
            ("いつも", "副詞"),  # a join: listed only as the merged token
        ],
    )
    def test_leaves_other_tags_and_joins_out(self, front, pos1):
        assert not is_listed(front, pos1, merged=False)

    def test_a_merged_token_matches_on_its_join_headword(self):
        assert is_listed("いつも", "副詞", merged=True)
        assert not is_listed("もう", "副詞", merged=True)

    def test_no_single_character_word(self):
        assert all(len(front) >= 2 for front, _pos1 in kana_words().words)

    def test_joins_carry_their_line_start_rule(self):
        joins = kana_words().joins
        assert joins[("で", "も")] == ("でも", "接続詞", True)
        assert joins[("いつ", "も")] == ("いつも", "副詞", False)

    @pytest.mark.parametrize(
        ("front", "pos1"), [("わたし", "代名詞"), ("じっと", "副詞"), ("おなか", "名詞"), ("べんとう", "名詞")]
    )
    def test_lists_the_kana_spelling_of_a_kanji_written_word(self, front, pos1):
        assert is_listed(front, pos1, merged=False)
        assert is_kanji_spelled(front, pos1)

    def test_a_kana_written_word_is_no_kanji_spelling(self):
        assert not is_kanji_spelled("もう", "副詞")

    @pytest.mark.parametrize("front", ["かげん", "したく", "しずく"])
    def test_leaves_out_a_three_mora_kana_spelling_of_a_kanji_noun(self, front):
        # As often the tail of a longer kana word (いい|かげん) or a given name.
        assert not is_listed(front, "名詞", merged=False)


class TestMining:
    def test_mines_a_listed_adverb(self, test_config):
        assert "もう" in _mine(test_config, "もう行こう")

    def test_mines_the_kana_spelling_of_a_kanji_written_word(self, test_config):
        attested = {"わたし", "本", "おなか", "空く"}
        assert {"わたし", "おなか"} <= set(_mine_attested(test_config, attested, "わたしの本", "おなかが空いた"))

    def test_a_kanji_spelling_inside_a_longer_kana_word_is_not_mined(self, test_config):
        # お|べんとう: the window おべんとう is a word, so べんとう alone is its tail.
        assert "べんとう" not in _mine_attested(test_config, {"べんとう", "おべんとう"}, "おべんとうを食べる")

    def test_joins_a_pronoun_and_its_plural(self, test_config):
        fronts = _mine(test_config, "おれたちは行く")
        assert "おれたち" in fronts
        assert "おれ" not in fronts

    def test_mines_a_pronoun_before_a_particle_it_could_join(self, test_config):
        # これは is an attested window: the lexicalized-window reject that keeps
        # すみません off a すむ card must not take これ from every これは line.
        assert "これ" in _mine(test_config, "これは本だ")

    def test_joins_a_word_the_tagger_cuts(self, test_config):
        fronts = _mine(test_config, "いつも笑う")
        assert "いつも" in fronts
        assert "いつ" not in fronts

    @pytest.mark.parametrize("line", ["でも、行く", "うん でも行く"])
    def test_joins_a_conjunction_opening_a_clause(self, test_config, line):
        assert "でも" in _mine(test_config, line)

    def test_leaves_particles_inside_a_clause(self, test_config):
        assert "でも" not in _mine(test_config, "学校でも行く")

    def test_still_drops_the_auxiliary_verb(self, test_config):
        assert "いる" not in _mine(test_config, "見ている")

    @pytest.mark.parametrize(
        ("line", "front", "pieces"),
        [("そのまま帰る", "そのまま", {"その", "まま"}), ("これから行く", "これから", {"これ"})],
    )
    def test_mines_a_kana_compound_whole(self, test_config, line, front, pieces):
        fronts = _mine(test_config, line)
        assert front in fronts
        assert not pieces & set(fronts)

    def test_drops_the_light_verb_after_a_noun(self, test_config):
        assert "する" not in _mine(test_config, "毎日勉強する")

    def test_counts_what_it_mines(self, test_config, tmp_path):
        # T-38: the curator's occurrence count and the mined cards see the same
        # fronts, across listed words, kana joins, place names and counters.
        lines = ["もう行こう", "これは本だ", "いつも笑う", "でも、行く", "雨のため中止", "4人で日本に行く"]
        srt = tmp_path / "parity.srt"
        srt.write_text(
            "\n".join(f"{i + 1}\n00:00:0{i},000 --> 00:00:0{i},900\n{line}\n" for i, line in enumerate(lines)),
            encoding="utf-8",
        )
        service = SubtitleParserService(
            replace(test_config, mine_number_counters=True),
            kana_attest_lookup=lambda forms: dict.fromkeys(forms, True),
            term_common_lookup=lambda terms: {term: term == "日本" for term in terms},
        )
        units = [ReadingUnit(text=line, index=i, location_label="t") for i, line in enumerate(lines)]
        _words, _index, counter = service.parse_text_units(units, want_line_index=False)
        assert {"もう", "これ", "いつも", "でも", "ため", "4人", "日本"} <= set(counter)
        assert service.count_fronts(srt) == counter

    def test_an_unattested_listed_word_is_not_mined(self, test_config):
        service = SubtitleParserService(test_config, kana_attest_lookup=lambda forms: dict.fromkeys(forms, False))
        words, _index, _counts = service.parse_text_units(
            [ReadingUnit(text="もう行こう", index=0, location_label="t")], want_line_index=False
        )
        assert "もう" not in [word.mined_form for word in words]
