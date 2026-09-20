"""ZH-046: a bilingual cue's other-language line reaches no Chinese card.

The seam is the same shape as the normalisation one (``test_normalize_seam``):
``clean_subtitle_text`` takes the predicate, the parser stores it, the zh
factory opts in, and the reading-tab loader gets the value the parser holds.
Every other language, ja included, passes nothing and stays byte-identical.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pysubs2
import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import _create_subtitle_parser
from anki_miner.gui.workers import reading_queue_worker
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.zh.parser import create_parser
from anki_miner.models.reading import ReadingSourceRef
from anki_miner.services.reading import detector, subtitle_source
from anki_miner.utils.text_utils import clean_subtitle_text

_ZH = "我今天早上吃了三个苹果。"
_EN = "I ate three apples this morning."

#: The zh profile's own gate, which is what the factory injects.
HAN = get_profile("zh").script.contains_target_script


def _zh_config() -> AnkiMinerConfig:
    return switch_language(AnkiMinerConfig(), "zh")


def _srt(tmp_path: Path, name: str, *cues: str) -> Path:
    path = tmp_path / name
    path.write_text(
        "".join(f"{i}\n00:00:0{i},000 --> 00:00:0{i},900\n{cue}\n\n" for i, cue in enumerate(cues, 1)),
        encoding="utf-8",
    )
    return path


def _ass(tmp_path: Path, name: str, *cues: str) -> Path:
    """A real ASS file, so the ``\\N`` break is the format's own, not a literal."""
    subs = pysubs2.SSAFile()
    for i, cue in enumerate(cues, 1):
        subs.append(pysubs2.SSAEvent(start=i * 1000, end=i * 1000 + 900, text=cue))
    path = tmp_path / name
    subs.save(str(path))
    return path


def _texts(parser, path: Path) -> list[str]:
    return [text for _start, _end, text in parser.parse_raw_entries(path)]


@pytest.fixture
def zh_parser(test_config):
    return _create_subtitle_parser(switch_language(test_config, "zh"))


class TestCleanSubtitleText:
    def test_the_default_leaves_a_bilingual_cue_whole(self) -> None:
        assert clean_subtitle_text(f"{_ZH}\\N{_EN}") == f"{_ZH} {_EN}"

    def test_the_predicate_drops_the_other_language_line(self) -> None:
        assert clean_subtitle_text(f"{_ZH}\\N{_EN}", has_target_script=HAN) == _ZH
        assert clean_subtitle_text(f"{_EN}\n{_ZH}", has_target_script=HAN) == _ZH

    def test_every_matching_line_of_a_three_line_cue_survives(self) -> None:
        assert clean_subtitle_text(f"{_ZH}\n{_EN}\n你好", has_target_script=HAN) == f"{_ZH} 你好"

    def test_a_cue_with_no_matching_line_is_left_as_written(self) -> None:
        assert clean_subtitle_text(f"{_EN}\\NGood morning.", has_target_script=HAN) == f"{_EN} Good morning."

    def test_a_single_line_cue_is_never_touched(self) -> None:
        assert clean_subtitle_text("我喜欢Netflix。", has_target_script=HAN) == "我喜欢Netflix。"
        assert clean_subtitle_text(_EN, has_target_script=HAN) == _EN


class TestTheZhParser:
    def test_a_dual_line_srt_cue_mines_the_chinese_line_alone(self, zh_parser, tmp_path: Path) -> None:
        dual = zh_parser.parse_subtitle_file(_srt(tmp_path, "dual.srt", f"{_ZH}\n{_EN}"))
        single = zh_parser.parse_subtitle_file(_srt(tmp_path, "single.srt", _ZH))

        assert {word.sentence for word in dual} == {_ZH}
        assert [word.mined_form for word in dual] == [word.mined_form for word in single]
        assert "苹果" in {word.mined_form for word in dual}

    def test_an_ass_cue_splits_on_the_line_break_marker(self, zh_parser, tmp_path: Path) -> None:
        words = zh_parser.parse_subtitle_file(_ass(tmp_path, "dual.ass", f"{_ZH}\\N{_EN}"))
        assert {word.sentence for word in words} == {_ZH}

    def test_a_cue_whose_lines_are_all_english_keeps_both(self, zh_parser, tmp_path: Path) -> None:
        path = _srt(tmp_path, "en.srt", "Good morning.\nHow are you?")
        assert _texts(zh_parser, path) == ["Good morning. How are you?"]

    def test_a_single_line_mixed_cue_is_unchanged(self, zh_parser, tmp_path: Path) -> None:
        path = _srt(tmp_path, "mixed.srt", "我喜欢Netflix。")
        assert _texts(zh_parser, path) == ["我喜欢Netflix。"]
        assert "喜欢" in {word.mined_form for word in zh_parser.parse_subtitle_file(path)}


class TestEveryOtherLanguage:
    def test_the_ja_parser_keeps_both_lines_of_a_bilingual_cue(self, test_config, tmp_path: Path) -> None:
        parser = _create_subtitle_parser(test_config)
        path = _srt(tmp_path, "ja.srt", "りんごを食べた\nI ate an apple")

        assert parser.has_target_script is None
        assert _texts(parser, path) == ["りんごを食べた I ate an apple"]


class TestTheFactorySeam:
    def test_the_zh_factory_opts_in_with_the_profile_gate(self) -> None:
        assert create_parser(_zh_config()).has_target_script == get_profile("zh").script.contains_target_script

    def test_an_explicit_argument_still_wins(self) -> None:
        assert create_parser(_zh_config(), has_target_script=str.isascii).has_target_script is str.isascii

    def test_the_zh_factory_forwards_nothing_else(self, test_config) -> None:
        # The bilingual gate is zh's only new injection: normalize in particular
        # stays the Japanese pair, as it was before this seam existed.
        assert create_parser(switch_language(test_config, "zh")).normalize is None


class TestTheReadingPath:
    def test_the_subtitle_loader_takes_the_predicate(self, tmp_path: Path) -> None:
        path = tmp_path / "x.srt"
        path.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{_ZH}\n{_EN}\n\n", encoding="utf-8")
        ref = ReadingSourceRef(kind="subtitle", path=path, image_root=None, title="x", volume=None)

        document = subtitle_source.load(ref, has_target_script=HAN)

        assert [unit.text for unit in document.units] == [_ZH]

    def test_the_detector_forwards_it_to_the_subtitle_loader_only(self, monkeypatch) -> None:
        fake = MagicMock(return_value=object())
        monkeypatch.setattr(subtitle_source, "load", fake)
        ref = ReadingSourceRef(kind="subtitle", path=Path("x.srt"), image_root=None, title="x", volume=None)

        detector.load(ref)
        detector.load(ref, has_target_script=HAN)

        assert fake.call_args_list[0].kwargs == {}
        assert fake.call_args_list[1].kwargs == {"has_target_script": HAN}

    def test_the_reading_worker_hands_the_loader_its_parser_predicate(self, test_config, monkeypatch) -> None:
        load = MagicMock(return_value=object())
        monkeypatch.setattr(reading_queue_worker.detector, "load", load)
        processor = MagicMock()
        worker = SimpleNamespace(
            _config=test_config,
            check_cancelled=lambda: False,
            _processor=processor,
            item_progress=MagicMock(),
            _active_curation_callback=None,
            _cancel_event=None,
            curation_document=None,
        )
        item = SimpleNamespace(source=ReadingSourceRef(kind="subtitle", path=Path("x.srt"), title="x"))

        processor.subtitle_parser = SimpleNamespace(normalize=None, has_target_script=HAN)
        reading_queue_worker.ReadingQueueWorker._mine_one(worker, 0, item)
        assert load.call_args.kwargs["has_target_script"] is HAN

        processor.subtitle_parser = SimpleNamespace(normalize=None, has_target_script=None)
        reading_queue_worker.ReadingQueueWorker._mine_one(worker, 0, item)
        assert "has_target_script" not in load.call_args.kwargs
