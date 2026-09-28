"""ko tokenizer: duck-token shape, tag normalisation, provider wiring."""

import inspect
import threading

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import tagger_provider
from anki_miner.languages.ko import tokenizer as ko_tokenizer
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.token import LanguageToken
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.tagger import LockedTagger


class _FakeKiwiToken:
    def __init__(self, form, tag, lemma="", start=0, end=0):
        self.form = form
        self.tag = tag
        self.lemma = lemma
        self.start = start
        self.end = end


class _FakeKiwi:
    builds: list[int] = []

    def __init__(self):
        _FakeKiwi.builds.append(1)

    def tokenize(self, text, **kwargs):
        assert kwargs["z_coda"] is True
        return [_FakeKiwiToken(text, "NNG", "", 0, len(text))]


def test_base_tag_strips_the_regularity_suffix():
    assert ko_tokenizer.base_tag("VV-I") == "VV"
    assert ko_tokenizer.base_tag("VA-R") == "VA"
    assert ko_tokenizer.base_tag("NNG") == "NNG"


def test_coarse_tag_is_the_two_letter_sejong_class():
    assert ko_tokenizer.coarse_tag("NNG") == "NN"
    assert ko_tokenizer.coarse_tag("NNB") == "NN"
    assert ko_tokenizer.coarse_tag("VV-I") == "VV"
    assert ko_tokenizer.coarse_tag("") == ""


def test_duck_tokens_expose_the_fugashi_attribute_surface():
    text = "학생이 먹었다"
    tokens = ko_tokenizer.to_duck_tokens(
        [
            _FakeKiwiToken("학생", "NNG", "", 0, 2),
            _FakeKiwiToken("먹", "VV-I", "먹다", 4, 5),
        ],
        text,
    )
    noun, verb = tokens
    assert isinstance(noun, LanguageToken)
    assert noun.surface == "학생"
    assert noun.feature.pos1 == "NN"  # coarse class
    assert noun.feature.pos2 == "NNG"  # full base tag, the subtype gate's field
    assert noun.feature.lemma == "학생"  # no kiwi lemma -> the form
    assert noun.feature.kana == ""
    assert verb.feature.pos1 == "VV"  # base tag, not VV-I
    assert verb.feature.pos2 == ""  # full tag equals the coarse class
    assert verb.feature.lemma == "먹다"


def test_z_coda_tokens_never_reach_the_duck_stream():
    text = "먹었어욥"
    tokens = ko_tokenizer.to_duck_tokens(
        [
            _FakeKiwiToken("먹", "VV-I", "먹다", 0, 1),
            _FakeKiwiToken("었", "EP", "", 1, 2),
            _FakeKiwiToken("어요", "EF", "", 2, 4),
            _FakeKiwiToken("ᆸ", "Z_CODA", "", 3, 4),
        ],
        text,
    )
    assert [t.feature.pos1 for t in tokens] == ["VV", "EP", "EF"]


def test_surface_is_the_verbatim_source_slice_so_spans_are_findable():
    text = "걸어 갔다"
    # kiwi restores the irregular stem (걷), which does not occur in the line.
    tokens = ko_tokenizer.to_duck_tokens([_FakeKiwiToken("걷", "VV-I", "걷다", 0, 1)], text)
    assert tokens[0].surface == "걸"
    assert tokens[0].surface in text


def test_build_tagger_wraps_one_kiwi_in_the_shared_parse_lock(monkeypatch):
    _FakeKiwi.builds = []
    monkeypatch.setattr(ko_tokenizer, "_create_kiwi", _FakeKiwi)
    tagger = ko_tokenizer.build_tagger()
    assert isinstance(tagger, LockedTagger)
    results: list[list[LanguageToken]] = []
    threads = [threading.Thread(target=lambda: results.append(tagger("학생"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_FakeKiwi.builds) == 1
    assert all(r[0].surface == "학생" for r in results)


def test_tagger_provider_resolves_ko_through_the_generic_module_branch(monkeypatch):
    """2A.4's importlib branch finds ko/tokenizer.py::build_tagger and CACHES it."""
    _FakeKiwi.builds = []
    monkeypatch.setattr(tagger_provider, "_TAGGERS", {})
    monkeypatch.setattr(ko_tokenizer, "_create_kiwi", _FakeKiwi)
    first = tagger_provider.get_tagger("ko")
    assert isinstance(first, LockedTagger)
    assert tagger_provider.get_tagger("ko") is first
    assert tagger_provider._TAGGERS["ko"] is first
    assert len(_FakeKiwi.builds) == 1


def test_tagger_provider_carries_no_korean_specific_code():
    assert '"ko"' not in inspect.getsource(tagger_provider)


def test_factory_closes_the_bilingual_line_and_ellipsis_guard_seams(monkeypatch):
    """The two seams the zh factory closes, asserted on the kwargs the service receives."""
    from anki_miner.services import subtitle_parser

    seen: dict[str, object] = {}
    monkeypatch.setattr(subtitle_parser, "SubtitleParserService", lambda config, **kwargs: seen.update(kwargs))
    get_profile("ko").create_parser(switch_language(AnkiMinerConfig(), "ko"))
    assert seen["has_target_script"] == get_profile("ko").script.contains_target_script
    assert seen["ellipsis_fragment_guard"] is False


@pytest.fixture(scope="module")
def ko_parser():
    # Module-scoped: tests/conftest.py clears the tagger cache per test, and one kiwi build is enough.
    pytest.importorskip("kiwipiepy")
    return get_profile("ko").create_parser(switch_language(AnkiMinerConfig(), "ko"))


def test_a_bilingual_cue_keeps_its_english_line_out_of_the_sentence(ko_parser, tmp_path):
    cues = ["어디 가?\nWhere are you going?", "배고파.\nI'm hungry."]
    srt = tmp_path / "bilingual.srt"
    srt.write_text(
        "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{cue}\n\n" for i, cue in enumerate(cues, 1)),
        encoding="utf-8",
    )
    assert {w.sentence for w in ko_parser.parse_subtitle_file(srt)} == {"어디 가?", "배고파."}


@pytest.mark.parametrize(
    ("line", "word"),
    [("눈… 눈이 와…", "오다"), ("물… 물 좀 줘…", "주다"), ("돈…돈이 없어…", "돈")],
)
def test_a_stutter_line_keeps_its_one_syllable_word(ko_parser, line, word):
    """와 = 오다 and 줘 = 주다 are one syllable; the Japanese truncation guard read them as severed fragments."""
    words, _index, _counts = ko_parser.parse_text_units([ReadingUnit(text=line, index=0, location_label="t")], False)
    assert word in {w.mined_form for w in words}


@pytest.mark.parametrize("text", ["학생이 밥을 먹었다."])
def test_real_kiwi_tokenizes_into_duck_tokens(text):
    pytest.importorskip("kiwipiepy")
    tokens = ko_tokenizer.build_tagger()(text)
    assert any(t.feature.pos1 == "NN" for t in tokens)
    assert any(t.feature.pos1 == "VV" for t in tokens)
    assert all(t.surface in text for t in tokens if t.surface.strip())
