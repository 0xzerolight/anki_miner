"""The yue split pass: a glued token that misses every dictionary becomes the words it is made of.

Real-engine test: the pass re-tags through the pycantonese tagger. The tagger is
built ONCE per module because tests/conftest.py clears the shared tagger cache
per test. The dictionary is a set: ``attest`` answers which of its probes it holds.
"""

from __future__ import annotations

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.yue.parser import YueDecompoundPass
from anki_miner.languages.yue.pos import YUE_ALLOWED_POS
from anki_miner.languages.yue.tokenizer import build_tagger
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.morphology import iter_token_spans


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


def dictionary(*words: str):
    held = set(words)
    return lambda probes: {probe for probe in probes if probe in held}


def split(tagger, line: str, *words: str):
    return YueDecompoundPass(tagger)(tagger.parse(line), dictionary(*words), None)


def test_a_glued_miss_splits_into_its_attested_words(tagger):
    tokens = split(tagger, "我今日好忙。", "我", "今日", "好", "忙")

    assert [t.surface for t in tokens] == ["我", "今日", "好", "忙", "。"]
    assert {t.feature.lemma: t.feature.pos1 for t in tokens}["忙"] in YUE_ALLOWED_POS


def test_the_pieces_are_verbatim_slices_of_the_line(tagger):
    line = "今 日好忙"
    tokens = split(tagger, line, "好", "忙")

    assert [line[start:end] for _token, start, end in iter_token_spans(line, tokens)] == ["今 日", "好", "忙"]
    assert tokens[0].feature.lemma == "今日"


def test_the_split_line_is_retagged_in_one_call_with_the_overrides(tagger):
    calls = []

    def spy(text, **kwargs):
        calls.append(text)
        return tagger(text, **kwargs)

    tokens = YueDecompoundPass(spy)(tagger.parse("佢瞓緊覺。"), dictionary("瞓", "緊", "覺"), None)

    assert len(calls) == 1
    # 緊 on its own is a dictionary headword; the override keeps it from carding.
    assert [(t.surface, t.feature.pos1) for t in tokens][1:3] == [("瞓", "VERB"), ("緊", "PART")]
    assert tokens[0].feature.pos2 == "stopword"


def test_nothing_splits_unless_every_piece_is_attested(tagger):
    raw = tagger.parse("我今日好忙。")
    assert YueDecompoundPass(tagger)(raw, dictionary("我", "今日", "好"), None) is raw


def test_an_attested_token_stays_whole(tagger):
    raw = tagger.parse("我今日好忙。")
    assert YueDecompoundPass(tagger)(raw, dictionary("好忙", "好", "忙"), None) is raw


def test_a_token_the_lookup_ladder_attests_stays_whole(tagger):
    # No dictionary keys 甚麼 'what'; the definition lookup finds it as 什麼. Split,
    # it carded 甚 'variant of 什' and 麼 'exclamatory final particle'.
    raw = tagger.parse("你為甚麼不告訴我？")
    assert "甚麼" in [t.surface for t in raw]

    tokens = split(tagger, "你為甚麼不告訴我？", "你", "為", "什麼", "甚", "麼", "不", "告", "訴", "我")

    assert "甚麼" in [t.surface for t in tokens]


def test_a_token_the_pass_does_not_split_keeps_its_first_tag(tagger):
    # Re-tagging the re-segmented line gave the untouched 起床 PART, so it lost its card.
    raw = tagger.parse("她每天都很早起床。")
    assert [(t.surface, t.feature.pos1) for t in raw][3:5] == [("很早", "ADV"), ("起床", "VERB")]

    tokens = YueDecompoundPass(tagger)(raw, dictionary("她", "每天", "都", "很", "早", "起床"), None)

    assert [t.surface for t in tokens] == ["她", "每天", "都", "很", "早", "起床", "。"]
    assert tokens[5].feature.pos1 == "VERB"
    assert all(new is old for new, old in zip(tokens[:3] + tokens[5:], raw[:3] + raw[4:], strict=True))


@pytest.mark.parametrize(
    ("line", "pieces", "word"),
    [
        ("這件事情跟你沒有關係。", ("這", "件"), "這"),
        ("他們在公司開會。", ("他們", "在"), "他們"),
        ("他說他不會來了。", ("說", "他", "不會", "來", "了"), "了"),
    ],
)
def test_a_written_chinese_function_word_a_split_frees_is_not_mined(tagger, line, pieces, word):
    # Written-Chinese lines glue 這, 他們 and 了 onto the next word, and freed
    # they came out VERB, ADJ and VERB: each became a card.
    tokens = split(tagger, line, *pieces)

    assert {t.feature.lemma: t.feature.pos1 for t in tokens}[word] not in YUE_ALLOWED_POS


def test_a_proper_noun_is_never_split(tagger):
    raw = tagger.parse("阿明鍾意睇Netflix。")
    assert raw[0].feature.pos1 == "PROPN"
    assert YueDecompoundPass(tagger)(raw, dictionary("阿", "明"), None) is raw


def test_a_numeral_led_token_is_never_split(tagger):
    # 三杯 -> 三 杯 would card the classifier.
    raw = tagger.parse("三杯熱奶茶。")
    assert raw[0].surface == "三杯"
    assert YueDecompoundPass(tagger)(raw, dictionary("三", "杯"), None) is raw


def test_a_token_joined_across_a_space_is_never_split(tagger):
    raw = tagger.parse("今 日好開心")
    assert YueDecompoundPass(tagger)(raw, dictionary("今", "日"), None) is raw


def test_without_a_dictionary_the_pass_is_inert(tagger):
    raw = tagger.parse("我今日好忙。")
    assert YueDecompoundPass(tagger)(raw, None, None) is raw


def test_the_factory_injects_the_pass(monkeypatch):
    from anki_miner.services import subtitle_parser as module

    seen: dict[str, object] = {}
    monkeypatch.setattr(module, "SubtitleParserService", lambda config, **kwargs: seen.update(kwargs))
    get_profile("yue").create_parser(switch_language(AnkiMinerConfig(), "yue"))

    assert isinstance(seen["token_post_pass"], YueDecompoundPass)


def test_the_parser_mines_the_word_inside_a_glued_miss():
    profile = get_profile("yue")
    config = switch_language(AnkiMinerConfig(), "yue")
    parser = profile.create_parser(config, term_lookup=dictionary("佢", "好", "嬲"))

    words, _index, _counts = parser.parse_text_units([ReadingUnit(text="佢好嬲。", index=0, location_label="t")], False)

    assert "嬲" in {word.mined_form for word in words}
