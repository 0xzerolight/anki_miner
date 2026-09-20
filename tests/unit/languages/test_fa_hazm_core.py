"""The ported hazm core: data loading, stemming and word splitting.

Runs entirely off the committed fixtures, so it is the layer that stays green on
a machine with no language pack. Every Persian character is a \\N{NAME} escape
(LEAD-BRIEF section 3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.languages.fa._hazm import data, stemmer, tokenize

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "fa"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"
# ketab, "book", and its plural ketab-ha
KETAB = "\N{ARABIC LETTER KEHEH}\N{ARABIC LETTER TEH}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER BEH}"
HA = "\N{ARABIC LETTER HEH}\N{ARABIC LETTER ALEF}"
YEH = "\N{ARABIC LETTER FARSI YEH}"
# mi-ravam, "I go"
MI = "\N{ARABIC LETTER MEEM}" + YEH
RAVAM = "\N{ARABIC LETTER REH}\N{ARABIC LETTER WAW}\N{ARABIC LETTER MEEM}"
# mardom, "people" -> mard, "man": the stemmer is naive by design, which is why
# the mined form is the lemma the ladder chose and never a second stem() pass.
MARDOM = "\N{ARABIC LETTER MEEM}\N{ARABIC LETTER REH}\N{ARABIC LETTER DAL}\N{ARABIC LETTER MEEM}"
MARD = MARDOM[:-1]


@pytest.fixture(scope="module")
def loaded():
    return data.load(FIXTURES)


class TestLoad:
    def test_every_table_is_populated(self, loaded):
        assert len(loaded.words) == 300
        assert len(loaded.verb_lines) == 693
        assert loaded.iverb_rows and loaded.iwords and loaded.stopwords

    def test_an_untagged_row_carries_an_empty_tag_tuple(self, loaded):
        assert all(isinstance(tags, tuple) for tags in loaded.words.values())
        assert "0" not in {tag for tags in loaded.words.values() for tag in tags}
        assert any(tags == () for tags in loaded.words.values())

    def test_an_informal_verb_row_keeps_its_verb_line_and_stem(self, loaded):
        for verb_line, informal_present in loaded.iverb_rows:
            assert "#" in verb_line
            assert informal_present
            assert " " not in informal_present

    def test_a_missing_file_names_itself(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="words.dat"):
            data.load(tmp_path)

    def test_loading_twice_builds_independent_tables(self, loaded):
        assert data.load(FIXTURES).words is not loaded.words


class TestStem:
    @pytest.mark.parametrize(
        ("word", "stem"),
        [
            (KETAB + ZWNJ + HA, KETAB),
            (KETAB + HA, KETAB),
            (KETAB + YEH, KETAB),
            (KETAB, KETAB),
            (MARDOM, MARD),
        ],
    )
    def test_suffixes_come_off(self, word, stem):
        assert stemmer.stem(word) == stem

    def test_a_short_word_is_never_stemmed_away(self):
        # ma, "we": a one-character suffix comes off only when three characters
        # would be left, so this keeps its meem.
        short = "\N{ARABIC LETTER MEEM}\N{ARABIC LETTER ALEF}"
        assert stemmer.stem(short) == short
        # raqam, "number": the trailing meem stays, two characters is not a stem.
        two_left = "\N{ARABIC LETTER REH}\N{ARABIC LETTER QAF}\N{ARABIC LETTER MEEM}"
        assert stemmer.stem(two_left) == two_left

    def test_the_floor_guards_one_character_suffixes_only(self):
        # hazm's own asymmetry, ported unchanged: a two-character suffix comes
        # off however little is left, so dam ("trap") loses its -am.
        dam = "\N{ARABIC LETTER DAL}\N{ARABIC LETTER ALEF}\N{ARABIC LETTER MEEM}"
        assert stemmer.stem(dam) == "\N{ARABIC LETTER DAL}"

    def test_the_ezafe_mark_and_a_trailing_zwnj_come_off(self):
        khane = "\N{ARABIC LETTER KHAH}\N{ARABIC LETTER ALEF}" "\N{ARABIC LETTER NOON}\N{ARABIC LETTER HEH}"
        assert stemmer.stem(khane + "\N{ARABIC HAMZA ABOVE}") == khane
        assert stemmer.stem(KETAB + ZWNJ) == KETAB

    def test_the_table_is_sorted_longest_first(self):
        lengths = [len(suffix) for suffix in stemmer.SUFFIXES]
        assert lengths == sorted(lengths, reverse=True)


class TestSplit:
    def test_the_zwnj_stays_inside_a_token(self):
        assert tokenize.split_words(MI + ZWNJ + RAVAM) == [MI + ZWNJ + RAVAM]

    def test_punctuation_splits_off(self):
        line = KETAB + "\N{ARABIC COMMA} " + KETAB + "\N{ARABIC QUESTION MARK}"
        assert tokenize.split_words(line) == [
            KETAB,
            "\N{ARABIC COMMA}",
            KETAB,
            "\N{ARABIC QUESTION MARK}",
        ]

    def test_a_run_of_terminators_is_one_token(self):
        assert tokenize.split_words(KETAB + "!!!") == [KETAB, "!!!"]

    def test_a_clock_time_stays_whole(self):
        assert tokenize.split_words("12:30") == ["12:30"]

    def test_every_span_is_a_verbatim_slice(self):
        line = MI + ZWNJ + RAVAM + " " + KETAB + "\N{ARABIC QUESTION MARK}"
        spans = tokenize.iter_spans(line)
        assert [line[start:end] for start, end in spans] == tokenize.split_words(line)
        assert all(0 <= start < end <= len(line) for start, end in spans)
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False)), "spans never overlap"

    def test_an_empty_line_has_no_tokens(self):
        assert tokenize.split_words("") == []
        assert tokenize.iter_spans("   ") == []
