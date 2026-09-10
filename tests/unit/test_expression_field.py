"""The known-words scan decides each note type's expression field from its notes."""

import pytest

from anki_miner.languages.ko.script import KO_SENTENCE_RULES
from anki_miner.languages.registry import get_profile
from anki_miner.services.expression_field import (
    ExpressionFieldResolver,
    choose_expression_field,
    is_word_like,
    names_word_field,
)

_JA = get_profile("ja")


class _HangulScript:
    """ScriptSupport stub: Korean without the kiwipiepy engine (mirrors test_anki_service_script_gate)."""

    def filter_options(self):
        return ()

    def matches(self, option_id, form):
        return False

    def contains_target_script(self, text):
        return any("가" <= ch <= "힯" for ch in text)


def _fields(*pairs: tuple[str, str]) -> dict[str, dict[str, object]]:
    """A notesInfo ``fields`` dict in the given order — the shape AnkiConnect returns."""
    return {name: {"value": value, "order": i} for i, (name, value) in enumerate(pairs)}


def _choose(field_names, samples):
    return choose_expression_field(field_names, samples, script=_JA.script, sentence_rules=_JA.sentence_rules)


def _ja_word_like(text):
    return is_word_like(text, script=_JA.script, sentence_rules=_JA.sentence_rules)


class TestIsWordLike:
    @pytest.mark.parametrize("text", ["食べる", "学生", "食[た]べる", "情けは人の為ならず", "語199"])
    def test_words(self, text):
        assert _ja_word_like(text)

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "run",
            "0001",
            "彼は毎日走る。",
            "行くの？",
            "そうか…",
            "彼は毎日学校まで走って行きます",  # 15 chars, no punctuation
        ],
    )
    def test_not_words(self, text):
        assert not _ja_word_like(text)

    def test_length_boundary(self):
        assert _ja_word_like("あ" * 12)
        assert not _ja_word_like("あ" * 13)

    def test_korean_rules_count_the_ascii_period(self):
        kw = {"script": _HangulScript(), "sentence_rules": KO_SENTENCE_RULES}
        assert is_word_like("학생", **kw)
        assert not is_word_like("나는 학생이다.", **kw)


class TestNamesWordField:
    @pytest.mark.parametrize(
        "name",
        [
            "Expression",
            "Word",
            "Target Word",
            "Vocabulary-Kanji",
            "VocabKanji",
            "Term",
            "Headword",
            "単語",
            "Hanzi",
            "Simplified",
            "汉字",
            "단어",
            "target_word",
        ],
    )
    def test_word_names(self, name):
        assert names_word_field(name)

    @pytest.mark.parametrize(
        "name",
        [
            "Sentence",
            "SentKanji",
            "WordReading",
            "Expression Reading",
            "ExpressionAudio",
            "VocabFurigana",
            "Vocabulary-Kana",
            "Target Word Pitch",
            "Word Meaning",
            "VocabDef",
            "IsWordAndSentenceCard",
            "Front",
            "Back",
            "Optimized-Voc-Index",
            "YomichanWordTags",
            "Notes",
            "Picture",
            "Pinyin",
            # A bare Kanji field is a character, not a word (RTK: Keyword, Kanji).
            "Kanji",
            # Language names go both ways (Evita's Korean decks: word in one, sentence in the other).
            "Korean",
            "Japanese",
            "Chinese",
        ],
    )
    def test_other_names(self, name):
        assert not names_word_field(name)


class TestChooseExpressionField:
    def test_word_like_first_field_stays(self):
        # JP Mining Note: Key and Word both hold the word; the first field wins,
        # so a note type that already worked is untouched.
        samples = [
            _fields(("Key", "食べる"), ("Word", "食べる")),
            _fields(("Key", "走る"), ("Word", "走る")),
        ]
        assert _choose(["Key", "Word"], samples) == "Key"

    def test_sentence_first_reads_the_word_field(self):
        # Migaku Japanese.
        names = ["Sentence", "Target Word", "Definitions", "Sentence Audio", "Word Audio"]
        samples = [
            _fields(
                ("Sentence", "彼は毎日走る。"),
                ("Target Word", "走る"),
                ("Definitions", "to run"),
                ("Sentence Audio", "[sound:a.mp3]"),
                ("Word Audio", "[sound:b.mp3]"),
            ),
            _fields(
                ("Sentence", "水を飲む。"),
                ("Target Word", "飲む"),
                ("Definitions", "to drink"),
                ("Sentence Audio", ""),
                ("Word Audio", ""),
            ),
        ]
        assert _choose(names, samples) == "Target Word"

    def test_index_first_reads_the_vocab_field_not_the_sentence_named_expression(self):
        # Core 2k/6k Optimized: the sentence lives in a field called Expression.
        names = [
            "Optimized-Voc-Index",
            "Vocabulary-Kanji",
            "Vocabulary-Furigana",
            "Vocabulary-Kana",
            "Vocabulary-English",
            "Expression",
            "Reading",
        ]
        samples = [
            _fields(
                ("Optimized-Voc-Index", "0001"),
                ("Vocabulary-Kanji", "飲む"),
                ("Vocabulary-Furigana", "飲[の]む"),
                ("Vocabulary-Kana", "のむ"),
                ("Vocabulary-English", "to drink"),
                ("Expression", "水を飲む。"),
                ("Reading", "水[みず]を飲[の]む。"),
            ),
        ]
        assert _choose(names, samples) == "Vocabulary-Kanji"

    def test_reading_fields_are_skipped_by_name(self):
        names = ["Sentence", "Word Reading", "Word"]
        samples = [_fields(("Sentence", "私は学生です。"), ("Word Reading", "がくせい"), ("Word", "学生"))]
        assert _choose(names, samples) == "Word"

    def test_a_word_named_field_holding_sentences_is_not_taken(self):
        names = ["ID", "Expression"]
        samples = [_fields(("ID", "1"), ("Expression", "水を飲む。"))]
        assert _choose(names, samples) == "ID"

    def test_sentence_first_with_no_word_field_keeps_the_first_field(self):
        names = ["Front", "Back"]
        samples = [_fields(("Front", "私は学生です。"), ("Back", "I am a student."))]
        assert _choose(names, samples) == "Front"

    def test_empty_first_field_reads_the_word_field(self):
        names = ["Key", "Word"]
        samples = [_fields(("Key", ""), ("Word", "学生")), _fields(("Key", ""), ("Word", "先生"))]
        assert _choose(names, samples) == "Word"

    def test_nine_in_ten_values_must_be_words(self):
        names = ["Expression", "Word"]

        def rows(sentences: int) -> list[dict]:
            words = [_fields(("Expression", f"語{i}"), ("Word", f"語{i}")) for i in range(10 - sentences)]
            lines = [_fields(("Expression", "私は学生です。"), ("Word", "学生")) for _ in range(sentences)]
            return words + lines

        assert _choose(names, rows(sentences=1)) == "Expression"
        assert _choose(names, rows(sentences=2)) == "Word"

    # Ten realistic subtitle lines: seven are short and unpunctuated, so on
    # their own they pass is_word_like (彼は毎日走る is 6 chars); the field
    # is caught through its longer and punctuated lines — 7/10 is under the
    # nine-in-ten bar but would have passed a bare majority.
    _LINES = [
        "彼は毎日走る",
        "行こう",
        "ありがとう",
        "何してるの？",
        "私は学生です。",
        "今日は天気がいいですね",
        "本当にごめん",
        "また明日",
        "早く来て",
        "そうかもしれない…",
    ]

    def test_short_unpunctuated_lines_do_not_make_a_sentence_field_a_word_field(self):
        samples = [_fields(("Sentence", line), ("Target Word", f"語{i}")) for i, line in enumerate(self._LINES)]
        assert _choose(["Sentence", "Target Word"], samples) == "Target Word"

    def test_a_subtitle_deck_with_no_word_field_keeps_its_first_field(self):
        # subs2srs: sequence marker first, the subtitle line in a field called
        # Expression. The line field must not be taken on its name — the deck
        # keeps contributing nothing, exactly as today.
        samples = [
            _fields(("SequenceMarker", f"ep01_{i:04d}"), ("Expression", line)) for i, line in enumerate(self._LINES)
        ]
        assert _choose(["SequenceMarker", "Expression"], samples) == "SequenceMarker"

    def test_no_samples_keeps_the_first_field(self):
        assert _choose(["Sentence", "Word"], []) == "Sentence"

    def test_markup_is_stripped_before_judging(self):
        names = ["Sentence", "Word"]
        samples = [_fields(("Sentence", "<b>彼</b>は毎日走る。"), ("Word", "<b>走る</b>[sound:a.mp3]"))]
        assert _choose(names, samples) == "Word"


class TestExpressionFieldResolver:
    def _resolver(self, sample_notes=2):
        return ExpressionFieldResolver(script=_JA.script, sentence_rules=_JA.sentence_rules, sample_notes=sample_notes)

    def test_buffers_until_the_sample_fills_then_streams(self):
        resolver = self._resolver(sample_notes=2)
        a = _fields(("Sentence", "彼は毎日走る。"), ("Word", "走る"))
        b = _fields(("Sentence", "私は学生です。"), ("Word", "学生"))
        c = _fields(("Sentence", "水を飲む。"), ("Word", "飲む"))
        assert resolver.feed("Sentence First", a) == []
        assert resolver.feed("Sentence First", b) == [(a, "Word"), (b, "Word")]
        assert resolver.feed("Sentence First", c) == [(c, "Word")]
        assert resolver.flush() == []

    def test_flush_decides_an_undersampled_note_type(self):
        resolver = self._resolver(sample_notes=200)
        a = _fields(("Sentence", "彼は毎日走る。"), ("Word", "走る"))
        assert resolver.feed("Sentence First", a) == []
        assert resolver.flush() == [(a, "Word")]
        assert resolver.flush() == []

    def test_note_types_are_sampled_independently(self):
        resolver = self._resolver(sample_notes=1)
        lapis = _fields(("Expression", "走る"), ("Sentence", "彼は毎日走る。"))
        migaku = _fields(("Sentence", "彼は毎日走る。"), ("Target Word", "走る"))
        assert resolver.feed("Lapis", lapis) == [(lapis, "Expression")]
        assert resolver.feed("Migaku Japanese", migaku) == [(migaku, "Target Word")]

    def test_a_row_without_a_note_type_reads_its_first_field_at_once(self):
        resolver = self._resolver()
        a = _fields(("Expression", "走る"))
        assert resolver.feed(None, a) == [(a, "Expression")]
        assert resolver.chosen == {}

    def test_a_row_missing_the_chosen_field_is_skipped(self):
        # Every note of a note type has every field; a row without the chosen
        # one is malformed, and its first field is the sentence.
        resolver = self._resolver(sample_notes=1)
        a = _fields(("Sentence", "彼は毎日走る。"), ("Word", "走る"))
        assert resolver.feed("M", a) == [(a, "Word")]
        odd = _fields(("Sentence", "走る"))
        assert resolver.feed("M", odd) == []

    def test_overrides_list_only_note_types_read_off_their_first_field(self):
        resolver = self._resolver(sample_notes=1)
        resolver.feed("Sentence First", _fields(("Sentence", "彼は毎日走る。"), ("Word", "走る")))
        resolver.feed("Lapis", _fields(("Expression", "走る"), ("Sentence", "彼は毎日走る。")))
        assert resolver.chosen == {"Sentence First": "Word", "Lapis": "Expression"}
        assert resolver.overrides() == {"Sentence First": "Word"}
