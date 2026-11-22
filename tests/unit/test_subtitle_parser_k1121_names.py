"""Lane NAMES (K1121): the per-file name pass. A name the file itself vouches for is one token, not its pieces."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.services.subtitle_parser import SubtitleParserService


def _fugashi_available() -> bool:
    try:
        import fugashi  # noqa: F401
        import unidic_lite  # noqa: F401

        return True
    except ImportError:
        return False


pytestmark = pytest.mark.skipif(not _fugashi_available(), reason="fugashi/unidic-lite not installed")

# What the dictionary lists. 凛堂 is no headword, its pieces are.
_HEADWORDS = (
    "森",
    "瞳",
    "凛",
    "堂",
    "奥",
    "姿",
    "店員",
    "客",
    "黒騎士",
    "馬",
    "行く",
    "閉じる",
    "待つ",
    "見る",
    "会う",
    "帰る",
    "話す",
    "来る",
    "急ぐ",
    "乗る",
    "黒い",
    "王",
)

_LABELLED = [
    "（凛堂）森へ行こう",
    "（凛堂）瞳を閉じてくれ",
    "（森）凛堂、待ってくれ",
    "（瞳）凛堂さんは森の奥にいる",
    "森で凛とした堂々たる姿を見た",
    "凛堂は森で瞳に会った",
]
_UNLABELLED = [re.sub(r"^（[^）]*）", "", line) for line in _LABELLED]
_PIECES = ("凛", "堂")
# Proper nouns allowed, as the measuring settings have them.
_NAMES_MINED = dataclasses.replace(AnkiMinerConfig(), excluded_subtypes=("非自立", "接尾", "助動詞", "接頭"))


def _service(config: AnkiMinerConfig | None = None, *, name_piece_rejects: bool = True) -> SubtitleParserService:
    spelled = set(_HEADWORDS)
    return SubtitleParserService(
        config or AnkiMinerConfig(),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
        name_piece_rejects=name_piece_rejects,
    )


def _srt(path: Path, lines: list[str]) -> Path:
    path.write_text(
        "".join(
            f"{index + 1}\n00:00:{2 * index:02d},000 --> 00:00:{2 * index + 1:02d},000\n{line}\n\n"
            for index, line in enumerate(lines)
        ),
        encoding="utf-8",
    )
    return path


def _line_cards(service: SubtitleParserService, path: Path) -> list[tuple[str, list[str]]]:
    """Each cue's text and the card fronts mined in it, with nothing dropped as a repeat."""
    cards = []
    for state in service._iter_parsed_lines(path):
        words, _index = service._emit_line_words_and_index(state, None, collect_index=False, rejects={})
        cards.append((state[0], [word.mined_form for word in words]))
    return cards


def _mined(service: SubtitleParserService, path: Path) -> list[tuple[str, str, str]]:
    return [(word.sentence, word.mined_form, word.sentence_furigana) for word in service.parse_subtitle_file(path)]


def _without_pieces(cards: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
    return [(text, [front for front in fronts if front not in _PIECES]) for text, fronts in cards]


def _switched_off(service: SubtitleParserService) -> SubtitleParserService:
    service._file_name_list = False
    return service


@pytest.fixture
def labelled(tmp_path: Path) -> Path:
    return _srt(tmp_path / "labelled.srt", _LABELLED)


@pytest.fixture
def unlabelled(tmp_path: Path) -> Path:
    return _srt(tmp_path / "unlabelled.srt", _UNLABELLED)


class TestSpeakerLabelNames:
    def test_the_pieces_of_a_labelled_name_are_no_cards(self, labelled: Path, unlabelled: Path) -> None:
        plain = _line_cards(_service(), unlabelled)
        assert "凛" in [front for _text, fronts in plain for front in fronts]
        assert _line_cards(_service(), labelled) == _without_pieces(plain)

    def test_a_word_that_is_also_a_name_keeps_its_card(self, labelled: Path) -> None:
        cards = dict(_line_cards(_service(), labelled))
        assert "森" in cards["森へ行こう"]
        assert "瞳" in cards["瞳を閉じてくれ"]
        assert {"森", "奥"} <= set(cards["凛堂さんは森の奥にいる"])
        assert {"森", "瞳"} <= set(cards["凛堂は森で瞳に会った"])

    def test_a_line_where_the_name_does_not_stand_is_unchanged(self, labelled: Path, unlabelled: Path) -> None:
        line = "森で凛とした堂々たる姿を見た"
        assert dict(_line_cards(_service(), labelled))[line] == dict(_line_cards(_service(), unlabelled))[line]

    def test_the_whole_name_is_one_proper_noun(self, labelled: Path) -> None:
        cards = dict(_line_cards(_service(_NAMES_MINED), labelled))
        assert cards["凛堂、待ってくれ"] == ["凛堂", "待つ"]
        assert "凛堂" in cards["凛堂は森で瞳に会った"]
        assert not set(_PIECES) & {front for fronts in cards.values() for front in fronts}

    def test_labels_before_a_colon_and_shared_labels_count(self, tmp_path: Path) -> None:
        path = _srt(tmp_path / "colon.srt", ["凛堂：森へ行こう", "凛堂・瞳：瞳を閉じてくれ", "凛堂が森に来た"])
        assert dict(_line_cards(_service(), path))["凛堂が森に来た"] == ["森", "来る"]

    def test_one_label_is_no_evidence(self, tmp_path: Path) -> None:
        path = _srt(tmp_path / "once.srt", ["（凛堂）森へ行こう", "凛堂が森に来た"])
        assert "凛" in dict(_line_cards(_service(), path))["凛堂が森に来た"]

    def test_a_role_label_of_two_words_keeps_both_words(self, tmp_path: Path) -> None:
        lines = ["（店員客）森へ行こう", "（店員客）店員と客が話している", "店員客は帰った"]
        cards = dict(_line_cards(_service(), _srt(tmp_path / "roles.srt", lines)))
        assert cards["店員客は帰った"] == ["店員", "客", "帰る"]

    def test_a_label_the_dictionary_lists_changes_nothing(self, tmp_path: Path) -> None:
        lines = ["（黒騎士）森へ行こう", "（黒騎士）瞳を閉じてくれ", "黒騎士が黒い馬に乗った"]
        body = [re.sub(r"^（[^）]*）", "", line) for line in lines]
        with_labels = _line_cards(_service(_NAMES_MINED), _srt(tmp_path / "knight.srt", lines))
        assert with_labels == _line_cards(_service(_NAMES_MINED), _srt(tmp_path / "knight-body.srt", body))

    def test_a_label_for_several_people_is_no_evidence(self, tmp_path: Path) -> None:
        lines = ["（凛堂たち）森へ行こう", "（凛堂たち）瞳を閉じてくれ", "凛堂が森に来た"]
        assert "凛" in dict(_line_cards(_service(), _srt(tmp_path / "group.srt", lines)))["凛堂が森に来た"]

    def test_a_title_after_a_known_name_stays_a_word(self, tmp_path: Path) -> None:
        lines = ["（葛城王）森へ行こう", "（葛城王）瞳を閉じてくれ", "葛城王は森へ行く"]
        path = _srt(tmp_path / "title.srt", lines)
        assert _line_cards(_service(_NAMES_MINED), path) == _line_cards(_switched_off(_service(_NAMES_MINED)), path)
        assert "王" in dict(_line_cards(_service(), path))["葛城王は森へ行く"]


class TestFileState:
    """One parser serves a batch of files: a file's names never reach another file."""

    def test_names_do_not_leak_into_the_next_file(self, labelled: Path, unlabelled: Path) -> None:
        alone = _mined(_service(), unlabelled)
        service = _service()
        _mined(service, labelled)
        assert _mined(service, unlabelled) == alone
        assert "凛" in [front for _sentence, front, _furigana in alone]

    def test_a_file_parsed_after_another_still_merges(self, labelled: Path, unlabelled: Path) -> None:
        alone = _mined(_service(), labelled)
        service = _service()
        _mined(service, unlabelled)
        assert _mined(service, labelled) == alone
        assert "凛" not in [front for _sentence, front, _furigana in alone]

    def test_the_line_cards_are_the_same_in_both_orders(self, labelled: Path, unlabelled: Path) -> None:
        alone = (_line_cards(_service(), labelled), _line_cards(_service(), unlabelled))
        first, second = _service(), _service()
        assert (_line_cards(first, labelled), _line_cards(first, unlabelled)) == alone
        assert (_line_cards(second, unlabelled), _line_cards(second, labelled))[::-1] == alone

    def test_a_second_pass_is_a_cache_hit_and_equal(self, labelled: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _service()
        first = _mined(service, labelled)
        monkeypatch.setattr(service, "_build_line_state", lambda *_: pytest.fail("the cached file was parsed again"))
        assert _mined(service, labelled) == first

    def test_the_count_path_and_the_mining_path_agree(self, labelled: Path) -> None:
        service = _service()
        counted = service.count_fronts(labelled)
        assert set(counted) == {front for _sentence, front, _furigana in _mined(service, labelled)}
        assert "凛" not in counted and counted["森"] == 4

    def test_the_sentence_furigana_of_a_merged_line_is_unchanged(self, labelled: Path, unlabelled: Path) -> None:
        merged = {sentence: furigana for sentence, _front, furigana in _mined(_service(), labelled)}
        plain = {sentence: furigana for sentence, _front, furigana in _mined(_service(), unlabelled)}
        assert merged["凛堂、待ってくれ"] == plain["凛堂、待ってくれ"]
        assert "凛" in merged["凛堂、待ってくれ"]
        assert all(plain[sentence] == furigana for sentence, furigana in merged.items())

    @pytest.mark.parametrize("switch", ["factory", "attribute"])
    def test_switched_off_the_file_parses_line_by_line_as_before(
        self, switch: str, labelled: Path, unlabelled: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _service(name_piece_rejects=switch != "factory")
        if switch == "attribute":
            service._file_name_list = False
        assert _line_cards(service, labelled) == _line_cards(_service(name_piece_rejects=False), unlabelled)
        tagger, tokenized = service._tagger(), []
        monkeypatch.setattr(service, "_tagger", lambda: lambda text: tokenized.append(text) or tagger(text))
        service._line_cache.clear()
        next(service._iter_parsed_lines(labelled))
        assert tokenized == ["森へ行こう"]
