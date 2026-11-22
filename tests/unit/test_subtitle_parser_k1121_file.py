"""What one subtitle file says beyond labels and honorifics (session K1121, wave 2, lane FILE)."""

from __future__ import annotations

import dataclasses
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

# What the dictionary lists. 嵐牙 and 蒼刃 are no headwords, their pieces are.
_HEADWORDS = (
    "森",
    "嵐",
    "牙",
    "蒼",
    "刃",
    "兄",
    "円",
    "猫",
    "達",
    "年",
    "前",
    "皿",
    "奥",
    "剣",
    "氷",
    "紅",
    "紅牙",
    "来る",
    "待つ",
    "見る",
    "払う",
    "行く",
    "強い",
    "速い",
)
#: Of those, the words the dictionary tags common. 嵐 and 牙 are both common: a name of two common words.
_COMMON = frozenset(_HEADWORDS) - {"蒼"}

# An invented name in plain lines: no label, no honorific, never alone between pauses.
_REPEATED = [
    "嵐牙が森に来た",
    "嵐牙は森で待っている",
    "森の奥に嵐牙がいる",
    "嵐牙の剣は嵐のように速い",
]
_TWICE = _REPEATED[:2]
_PIECES = ("嵐", "牙")
# Proper nouns allowed, as the measuring settings have them.
_NAMES_MINED = dataclasses.replace(AnkiMinerConfig(), excluded_subtypes=("非自立", "接尾", "助動詞", "接頭"))


def _service(config: AnkiMinerConfig | None = None, *, name_piece_rejects: bool = True) -> SubtitleParserService:
    spelled = set(_HEADWORDS)
    return SubtitleParserService(
        config or AnkiMinerConfig(),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
        term_common_lookup=lambda terms: {term: term in _COMMON for term in terms},
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


def _switched_off(service: SubtitleParserService) -> SubtitleParserService:
    service._file_name_list = False
    return service


def _unchanged(tmp_path: Path, lines: list[str]) -> list[tuple[str, list[str]]]:
    """The line cards of ``lines``, after checking that the file pass changes none of them."""
    path = _srt(tmp_path / "unchanged.srt", lines)
    for config in (None, _NAMES_MINED):
        assert _line_cards(_service(config), path) == _line_cards(_switched_off(_service(config)), path)
    return _line_cards(_service(), path)


@pytest.fixture
def repeated(tmp_path: Path) -> Path:
    return _srt(tmp_path / "repeated.srt", _REPEATED)


@pytest.fixture
def twice(tmp_path: Path) -> Path:
    return _srt(tmp_path / "twice.srt", _TWICE)


class TestRepeatedRuns:
    """A run of one-kanji nouns that a file writes three times is a name of that file."""

    def test_the_pieces_of_a_run_written_three_times_are_no_cards(self, repeated: Path) -> None:
        plain = dict(_line_cards(_switched_off(_service()), repeated))
        cards = dict(_line_cards(_service(), repeated))
        assert plain["嵐牙が森に来た"] == ["嵐", "牙", "森", "来る"]
        assert cards["嵐牙が森に来た"] == ["森", "来る"]
        assert cards["森の奥に嵐牙がいる"] == ["森", "奥"]
        # The word 嵐 keeps its card; the piece of the name has none.
        assert plain["嵐牙の剣は嵐のように速い"].count("嵐") == 2
        assert cards["嵐牙の剣は嵐のように速い"] == plain["嵐牙の剣は嵐のように速い"][2:]

    def test_the_whole_run_is_one_proper_noun_token_and_no_card(self, repeated: Path) -> None:
        service = _service(_NAMES_MINED)
        state = next(state for state in service._iter_parsed_lines(repeated) if state[0] == "嵐牙が森に来た")
        name = state[2][0]
        assert (name.surface, name.feature.pos1, name.feature.pos2) == ("嵐牙", "名詞", "固有名詞")
        assert not {"嵐牙", *_PIECES} & set(dict(_line_cards(service, repeated))["嵐牙が森に来た"])

    def test_twice_is_no_evidence(self, tmp_path: Path) -> None:
        assert "嵐" in dict(_unchanged(tmp_path, _TWICE))["嵐牙が森に来た"]

    def test_a_number_and_its_counter_are_no_run(self, tmp_path: Path) -> None:
        cards = _unchanged(tmp_path, ["千円を払った", "千円を見た", "千円が来た", "森に千円がある"])
        assert all("円" in fronts for _text, fronts in cards)

    def test_a_kin_word_after_the_run_keeps_its_card(self, tmp_path: Path) -> None:
        lines = ["嵐牙兄が森に来た", "嵐牙兄は森で待っている", "森の奥に嵐牙兄がいる", "嵐牙兄を見た"]
        assert all("兄" in fronts for _text, fronts in _unchanged(tmp_path, lines))

    def test_a_kin_word_after_a_run_that_also_stands_alone_keeps_its_card(self, tmp_path: Path) -> None:
        cards = dict(_line_cards(_service(), _srt(tmp_path / "kin.srt", [*_REPEATED, "嵐牙兄が森に来た"])))
        assert cards["嵐牙兄が森に来た"] == ["兄", "森", "来る"]

    def test_a_run_the_dictionary_lists_is_a_word(self, tmp_path: Path) -> None:
        _unchanged(tmp_path, ["紅牙が森に来た", "紅牙は森で待っている", "森の奥に紅牙がいる"])

    def test_a_run_of_five_is_no_name(self, tmp_path: Path) -> None:
        _unchanged(tmp_path, ["嵐牙氷森剣が来た", "嵐牙氷森剣は強い", "嵐牙氷森剣を見た"])

    def test_a_counter_after_a_number_starts_no_run(self, tmp_path: Path) -> None:
        cards = _unchanged(tmp_path, ["３年前に森へ来た", "５年前は森で待っていた", "10 年前の剣を見た"])
        assert all("前" in fronts for _text, fronts in cards)

    def test_a_run_closed_by_a_suffix_is_a_name_when_its_noun_is_no_common_word(self, tmp_path: Path) -> None:
        # 蒼|刃: the tagger files 刃 as a suffix, and 蒼 is no common word.
        lines = ["蒼刃が森に来た", "蒼刃は森で待っている", "森の奥に蒼刃がいる"]
        path = _srt(tmp_path / "suffix.srt", lines)
        assert dict(_line_cards(_switched_off(_service()), path))["蒼刃が森に来た"] == ["蒼", "森", "来る"]
        assert dict(_line_cards(_service(), path))["蒼刃が森に来た"] == ["森", "来る"]

    def test_a_common_word_and_its_suffix_keep_the_word(self, tmp_path: Path) -> None:
        cards = _unchanged(tmp_path, ["猫達が森に来た", "猫達は森で待っている", "森の奥に猫達がいる", "猫達を見た"])
        assert all("猫" in fronts for _text, fronts in cards)

    def test_without_a_commonness_probe_a_suffix_closes_no_name(self, tmp_path: Path) -> None:
        path = _srt(tmp_path / "probe.srt", ["蒼刃が森に来た", "蒼刃は森で待っている", "森の奥に蒼刃がいる"])
        service = _service()
        service._term_common_lookup = None
        assert _line_cards(service, path) == _line_cards(_switched_off(_service()), path)


class TestFileState:
    """One parser serves a batch of files: a file's repeated runs never reach another file."""

    def test_runs_do_not_leak_into_the_next_file(self, repeated: Path, twice: Path) -> None:
        alone = _mined(_service(), twice)
        service = _service()
        _mined(service, repeated)
        assert _mined(service, twice) == alone
        assert "嵐" in [front for _sentence, front, _furigana in alone]

    def test_a_file_parsed_after_another_still_merges(self, repeated: Path, twice: Path) -> None:
        alone = _mined(_service(), repeated)
        service = _service()
        _mined(service, twice)
        assert _mined(service, repeated) == alone
        assert "牙" not in [front for _sentence, front, _furigana in alone]

    def test_the_line_cards_are_the_same_in_both_orders(self, repeated: Path, twice: Path) -> None:
        alone = (_line_cards(_service(), repeated), _line_cards(_service(), twice))
        first, second = _service(), _service()
        assert (_line_cards(first, repeated), _line_cards(first, twice)) == alone
        assert (_line_cards(second, twice), _line_cards(second, repeated))[::-1] == alone

    def test_a_second_pass_is_a_cache_hit_and_equal(self, repeated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _service()
        first = _mined(service, repeated)
        monkeypatch.setattr(service, "_build_line_state", lambda *_: pytest.fail("the cached file was parsed again"))
        assert _mined(service, repeated) == first

    def test_the_count_path_and_the_mining_path_agree(self, repeated: Path) -> None:
        service = _service()
        counted = service.count_fronts(repeated)
        assert set(counted) == {front for _sentence, front, _furigana in _mined(service, repeated)}
        assert "牙" not in counted and counted["嵐"] == 1 and counted["森"] == 3

    def test_the_sentence_furigana_of_a_merged_line_is_unchanged(self, repeated: Path) -> None:
        merged = {sentence: furigana for sentence, _front, furigana in _mined(_service(), repeated)}
        plain = {sentence: furigana for sentence, _front, furigana in _mined(_switched_off(_service()), repeated)}
        assert merged == {sentence: plain[sentence] for sentence in merged}
        assert "嵐" in merged["嵐牙が森に来た"]

    def test_switched_off_the_file_parses_line_by_line(self, repeated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        service = _service(name_piece_rejects=False)
        tagger, tokenized = service._tagger(), []
        monkeypatch.setattr(service, "_tagger", lambda: lambda text: tokenized.append(text) or tagger(text))
        next(service._iter_parsed_lines(repeated))
        assert tokenized == ["嵐牙が森に来た"]


# Katakana-written native nouns. The dictionary lists the hiragana and the kanji, never the katakana.
_KANA_HEADWORDS = ("うわさ", "噂", "かすみ", "霞", "ひみつ", "秘密", "田字", "サクラ", "桜", "さくら", "パン", "ゲス")
_KANA_COMMON = frozenset({"噂", "霞", "秘密", "桜", "パン"})
_KANA_VERBS = ("確かめる", "笑う", "待つ", "来る", "守る", "走る", "咲く", "食べる", "行く", "見る", "強い", "買う")


def _kana_service() -> SubtitleParserService:
    spelled = {*_KANA_HEADWORDS, *_KANA_VERBS, "森", "男", "話", "朝", "私"}
    return SubtitleParserService(
        AnkiMinerConfig(),
        term_lookup=lambda terms: spelled.intersection(terms),
        kana_attest_lookup=lambda forms: {form: form in spelled for form in forms},
        term_common_lookup=lambda terms: {term: term in _KANA_COMMON for term in terms},
        name_piece_rejects=True,
    )


def _fronts(service: SubtitleParserService, text: str) -> list[str]:
    state = service._build_line_state(text, 0.0, 1.0)
    words, _index = service._emit_line_words_and_index(state, None, collect_index=False, rejects={})
    return [word.mined_form for word in words]


class TestLineEndTics:
    """A katakana noun that a file puts at the end of its sentences, right after a predicate, is a way of speaking."""

    _TIC = [
        "森へ行くでゲス",
        "朝に食べるでゲス",
        "話を見たでゲス",
        "男は強いゲス",
        "私も行くでゲス",
        "森を見るゲス",
        "朝は来るでゲス",
        "話は強いでゲス",
    ]

    def test_the_tic_is_no_card_where_it_ends_a_line_and_a_card_where_it_is_a_word(self, tmp_path: Path) -> None:
        path = _srt(tmp_path / "tic.srt", [*self._TIC, "ゲスな男を見た", "ゲスが来た"])
        service = _kana_service()
        plain = dict(_line_cards(_switched_off(_kana_service()), path))
        cards = dict(_line_cards(service, path))
        assert all("ゲス" in plain[line] for line in [*self._TIC, "ゲスな男を見た"])
        assert all(cards[line] == [front for front in plain[line] if front != "ゲス"] for line in self._TIC)
        assert cards["ゲスな男を見た"] == plain["ゲスな男を見た"]
        counted = service.count_fronts(path)
        assert counted["ゲス"] == 2 and set(counted) == {front for _sentence, front, _furigana in _mined(service, path)}

    @pytest.mark.parametrize(
        "lines",
        [
            # Seven lines are no habit.
            [*_TIC[:7], "男を見た"],
            # One line said nine times is a song.
            ["森へ行くでゲス"] * 9,
            # As often a word as an ending.
            [*_TIC, "ゲスな男を見た", "ゲスが来た", "ゲスを見た", "ゲスは強い", "森のゲス", "朝のゲス"],
            # A noun that ends its lines after a particle or a noun is a word.
            [
                "森でパン",
                "朝はパン",
                "私のパン",
                "男がパン",
                "話はパン",
                "森のパン",
                "朝もパン",
                "私がパン",
                "男のパン",
            ],
        ],
    )
    def test_a_word_that_ends_some_lines_keeps_its_card(self, lines: list[str], tmp_path: Path) -> None:
        path = _srt(tmp_path / "words.srt", lines)
        assert _line_cards(_kana_service(), path) == _line_cards(_switched_off(_kana_service()), path)

    @pytest.mark.parametrize(("line", "card"), [("そうゲス", False), ("男のゲス", True)])
    def test_a_known_tic_is_no_card_after_an_adverb_and_a_card_after_a_noun(
        self, line: str, card: bool, tmp_path: Path
    ) -> None:
        path = _srt(tmp_path / "known.srt", [*self._TIC, line])
        plain = dict(_line_cards(_switched_off(_kana_service()), path))
        cards = dict(_line_cards(_kana_service(), path))
        assert "ゲス" in plain[line] and ("ゲス" in cards[line]) == card
        assert "ゲス" not in cards["森へ行くでゲス"]

    def test_a_tic_before_a_pause_inside_a_line_is_no_card(self, tmp_path: Path) -> None:
        lines = [*self._TIC, "森へ行くでゲス 朝に食べるでゲス", "話を見たでゲス、男は強いゲス", "男は来るかゲス"]
        cards = _line_cards(_kana_service(), _srt(tmp_path / "inside.srt", lines))
        assert not any("ゲス" in fronts for _text, fronts in cards)

    def test_the_tic_does_not_leak_into_the_next_file(self, tmp_path: Path) -> None:
        tic = _srt(tmp_path / "tic.srt", self._TIC)
        once = _srt(tmp_path / "once.srt", self._TIC[:2])
        alone = (_mined(_kana_service(), tic), _mined(_kana_service(), once))
        service = _kana_service()
        assert (_mined(service, tic), _mined(service, once), _mined(service, tic)) == (*alone, alone[0])
        assert "ゲス" in [front for _sentence, front, _furigana in alone[1]]
