"""S5: the normalisation seam replaces the two JA steps and nothing else."""

from __future__ import annotations

import dataclasses
import html
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from anki_miner.gui.utils.service_factory import create_profile_parser
from anki_miner.gui.workers import reading_queue_worker
from anki_miner.languages.ja.text import ja_normalize
from anki_miner.languages.registry import get_profile
from anki_miner.languages.token import LanguageToken
from anki_miner.models.reading import ReadingSourceRef, ReadingUnit
from anki_miner.services.reading import detector, subtitle_source
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.utils.ja_normalize import normalize_for_tokenization, standardize_kanji_variants
from anki_miner.utils.text_utils import clean_subtitle_text, strip_inline_annotations, strip_subtitle_markup
from tests.e2e.fixtures_subtitle import SUBTITLE_LINES
from tests.unit.languages.eu_stub import eu_normalize
from tests.unit.languages.stub_registry import register_stub_profile

_ROOT = Path(__file__).resolve().parents[3]

TRICKY = [
    "ﾊﾟｿｺﾝを𠮟る",
    "⼝を開けて ➡ 次へ",
    "{\\i1}新しい{\\i0}本\\N（しんぶん）新聞",
    "&amp;食べる&lt;b&gt;",
    "田中：\u3000はい",
    "か\u3099っこう",
]


def _pre_seam(text: str) -> str:
    """clean_subtitle_text as it was at 0f2303b9, spelled out."""
    text = re.sub(r"\\[nN]|\r\n?", "\n", text)
    text = strip_subtitle_markup(text)
    text = html.unescape(text)
    text = normalize_for_tokenization(text)
    text = standardize_kanji_variants(text)
    text = strip_inline_annotations(text)
    return " ".join(text.split())


def _corpus() -> list[str]:
    lines = [line for _start, _end, line in SUBTITLE_LINES] + TRICKY
    for path in sorted((_ROOT / "tests" / "fixtures" / "parse_corpus").glob("*.jsonl")):
        for row in path.read_text(encoding="utf-8").splitlines():
            if row.strip():
                sentence = json.loads(row).get("sentence")
                if isinstance(sentence, str):
                    lines.append(sentence)
    return lines


def test_default_is_byte_identical_over_the_corpus():
    corpus = _corpus()
    assert len(corpus) > 20
    assert [clean_subtitle_text(line) for line in corpus] == [_pre_seam(line) for line in corpus]


def test_ja_normalize_is_the_exact_composition():
    for line in _corpus():
        assert ja_normalize(line) == standardize_kanji_variants(normalize_for_tokenization(line))
    assert get_profile("ja").normalize is ja_normalize


def test_an_injected_normalize_replaces_only_the_japanese_pair():
    seen: list[str] = []

    def upper(text: str) -> str:
        seen.append(text)
        return text.upper()

    assert clean_subtitle_text("{\\b1}ﾊﾟ &amp; b\\Nc\\N(note)", normalize=upper) == "ﾊﾟ & B C"
    assert seen == ["ﾊﾟ & b\nc\n(note)"]


def test_the_parser_applies_its_normalize_on_both_paths(test_config):
    parser = SubtitleParserService(test_config, normalize=str.upper)
    assert parser.normalize is str.upper
    assert parser._clean_line_text("abc") == "ABC"
    assert SubtitleParserService(test_config).normalize is None


def test_text_units_use_the_injected_normalize(make_eu_parser):
    parser = make_eu_parser(normalize=lambda text: text.replace("’", "'"))
    words, _index, _counts = parser.parse_text_units(
        [ReadingUnit(text="don’t stop", index=0, location_label="p.1")], want_line_index=False
    )
    assert words[0].sentence == "don't stop"


class _SpaceTokenTagger:
    """jieba's shape: every whitespace character comes back as a token of its own."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, text: str) -> list[LanguageToken]:
        self.seen.append(text)
        pieces = [piece for piece in re.split(r"(\s)", text) if piece]
        return [LanguageToken(piece, "WORD" if piece.strip() else "x", lemma=piece) for piece in pieces]


def test_no_break_spaces_fold_for_the_tagger_and_its_offsets_only(make_eu_parser):
    """Whitespace tokens are located in the line the tagger read; the stored line keeps its no-break space."""
    parser = make_eu_parser(normalize=eu_normalize)
    parser.tagger = tagger = _SpaceTokenTagger()
    line = "alpha\u00a0beta gamma"
    words, index, counts = parser.parse_text_units(
        [ReadingUnit(text=line, index=0, location_label="p.1")], want_line_index=True
    )
    assert tagger.seen == ["alpha beta gamma"]
    assert [word.surface for word in words] == ["alpha", "beta", "gamma"]
    assert all(word.sentence == line for word in words)
    assert index is not None and index[0].line_text == line
    assert sum(counts.values()) == 3


def _filtering_parser(test_config, pattern: str, replacement: str = "", **kwargs) -> SubtitleParserService:
    config = dataclasses.replace(
        test_config,
        use_subtitle_regex_filter=True,
        subtitle_regex_filter=pattern,
        subtitle_regex_replacement=replacement,
    )
    return SubtitleParserService(config, **kwargs)


@pytest.mark.parametrize(
    ("pattern", "replacement"),
    [
        (r"\[[^\]]*\]", ""),
        (r"(\w+):", r"\1 -"),  # a backreference
        (r"(?P<w>a)", r"\g<w>\g<w>"),  # a named one
        (r"(a)|(b)", r"[\2]"),  # an unmatched group expands to ""
        (r"x*", "-"),  # empty matches, placed exactly where re.sub places them
    ],
)
def test_the_non_japanese_filter_splice_is_re_sub_on_a_plain_line(test_config, pattern, replacement):
    """finditer + Match.expand over the folded line is re.sub: no no-break space, same output as before."""
    line = "JEAN: a [b] xax  b."
    expected = " ".join(re.sub(pattern, replacement, line).split())
    assert (
        _filtering_parser(test_config, pattern, replacement, normalize=eu_normalize)._apply_text_filter(line)
        == expected
    )


def test_the_japanese_filter_is_unchanged(test_config):
    """normalize=None keeps re.sub + the full whitespace flatten, no-break spaces included."""
    line = "猫\u00a0[笑]\u202f犬 \u00a0です"
    assert _filtering_parser(test_config, r"\[[^\]]*\]")._apply_text_filter(line) == "猫 犬 です"


def test_the_subtitle_loader_takes_the_normalizer(tmp_path: Path):
    path = tmp_path / "x.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\nabc\n\n", encoding="utf-8")
    ref = ReadingSourceRef(kind="subtitle", path=path, image_root=None, title="x", volume=None)

    document = subtitle_source.load(ref, normalize=str.upper)

    assert [unit.text for unit in document.units] == ["ABC"]


def test_the_detector_forwards_normalize_to_the_subtitle_loader_only(monkeypatch):
    fake = MagicMock(return_value=object())
    monkeypatch.setattr(subtitle_source, "load", fake)
    ref = ReadingSourceRef(kind="subtitle", path=Path("x.srt"), image_root=None, title="x", volume=None)

    detector.load(ref)
    detector.load(ref, normalize=str.upper)

    assert fake.call_args_list[0].kwargs == {}
    assert fake.call_args_list[1].kwargs == {"normalize": str.upper}


def test_the_reading_worker_hands_the_loader_its_parser_normalize(test_config, monkeypatch):
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

    processor.subtitle_parser = SimpleNamespace(normalize=str.upper)
    reading_queue_worker.ReadingQueueWorker._mine_one(worker, 0, item)
    assert load.call_args.kwargs["normalize"] is str.upper

    processor.subtitle_parser = SimpleNamespace(normalize=None)
    reading_queue_worker.ReadingQueueWorker._mine_one(worker, 0, item)
    assert "normalize" not in load.call_args.kwargs


def test_display_parsers_follow_the_profile_factory(test_config, monkeypatch):
    calls: list[tuple] = []

    class _Recorder:
        def __init__(self, config, **kwargs):
            calls.append((config, kwargs))

    create_profile_parser(test_config, _Recorder)
    assert calls == [(test_config, {})]  # ja: the caller's own class, same call shape

    zh_parser = object()
    register_stub_profile(monkeypatch, "zh", create_parser=lambda config, **kwargs: zh_parser)

    assert create_profile_parser(dataclasses.replace(test_config, language="zh"), _Recorder) is zh_parser
    assert len(calls) == 1
