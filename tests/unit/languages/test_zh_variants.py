"""zh normalisation and simplified/traditional script variants."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import anki_miner
from anki_miner.languages.zh import variants


class _FakeConverter:
    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def convert(self, text: str) -> str:
        return self._mapping.get(text, text)


@pytest.fixture(autouse=True)
def _clear_converter_caches():
    def _clear() -> None:
        for name in ("_converter", "_converters"):
            getattr(getattr(variants, name), "cache_clear", lambda: None)()

    _clear()
    yield
    _clear()


def _fake_converters(monkeypatch: pytest.MonkeyPatch, *mappings: dict[str, str]) -> None:
    converters = tuple(_FakeConverter(m) for m in mappings)
    monkeypatch.setattr(variants, "_converters", lambda: converters)


class TestVariantCandidates:
    def test_word_comes_first_and_variants_follow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_converters(monkeypatch, {"汉字": "漢字"}, {"汉字": "汉字"})
        assert variants.variant_candidates("汉字") == ["汉字", "漢字"]

    def test_duplicates_collapse_to_first_occurrence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_converters(monkeypatch, {"中文": "中文"}, {"中文": "中文"})
        assert variants.variant_candidates("中文") == ["中文"]

    def test_no_converter_yields_the_word_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "_converters", tuple)
        assert variants.variant_candidates("汉字") == ["汉字"]

    def test_input_is_nfc_normalised_before_conversion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[str] = []

        class _Recorder:
            def convert(self, text: str) -> str:
                seen.append(text)
                return text

        monkeypatch.setattr(variants, "_converters", lambda: (_Recorder(),))
        variants.variant_candidates("\ufa0c")  # compat ideograph, NFC -> U+5140
        assert seen == ["\u5140"]

    def test_real_opencc_produces_a_traditional_variant(self) -> None:
        pytest.importorskip("opencc")
        assert "漢字" in variants.variant_candidates("汉字")


class TestToTraditional:
    def test_simplified_input_converts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "_converter", lambda name: _FakeConverter({"银行": "銀行"}))
        assert variants.to_traditional("银行") == "銀行"

    def test_missing_opencc_returns_the_normalised_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "_converter", lambda _name: None)
        assert variants.to_traditional("\ufa0c") == "\u5140"

    def test_real_opencc_converts_a_known_pair(self) -> None:
        pytest.importorskip("opencc")
        assert variants.to_traditional("汉字") == "漢字"

    def test_real_opencc_uses_the_taiwan_standard(self) -> None:
        pytest.importorskip("opencc")
        assert variants.to_traditional("这里的面条") == "這裡的麵條"


class TestToSimplified:
    def test_real_opencc_converts_taiwan_spellings(self) -> None:
        pytest.importorskip("opencc")
        assert variants.to_simplified("頭髮") == "头发"
        assert variants.to_simplified("看著") == "看着"

    def test_simplified_input_is_kept(self) -> None:
        pytest.importorskip("opencc")
        assert variants.to_simplified("银行") == "银行"

    def test_missing_opencc_returns_the_normalised_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(variants, "_converter", lambda _name: None)
        assert variants.to_simplified("兀") == "兀"


#: (traditional, simplified) spellings of one word; each pair must share a key.
_SAME_WORD = [
    ("頭髮", "头发"),
    ("銀行", "银行"),
    ("台灣", "台湾"),
    ("看著", "看着"),
    ("記著", "记着"),
    ("濕", "湿"),
    ("麵條", "面条"),
    ("裏面", "里面"),
    ("書", "书"),
]

#: Distinct traditional words whose simplified spelling is shared; never merged.
_DISTINCT_WORDS = [("麵", "面"), ("乾", "幹"), ("隻", "只"), ("髮", "發"), ("鐘", "鍾"), ("週", "周"), ("係", "系")]


class TestScriptKey:
    @pytest.fixture(autouse=True)
    def _opencc(self) -> None:
        pytest.importorskip("opencc")

    @pytest.mark.parametrize(("traditional", "simplified"), _SAME_WORD)
    def test_both_scripts_of_one_word_share_a_key(self, traditional: str, simplified: str) -> None:
        assert variants.script_key(traditional) == variants.script_key(simplified)

    @pytest.mark.parametrize(("first", "second"), _DISTINCT_WORDS)
    def test_distinct_traditional_words_keep_distinct_keys(self, first: str, second: str) -> None:
        assert variants.script_key(first) != variants.script_key(second)

    def test_simplified_text_is_its_own_key(self) -> None:
        for word in ("头发", "银行", "面", "关系"):
            assert variants.script_key(word) == word

    def test_the_key_is_idempotent(self) -> None:
        for word in [w for pair in _SAME_WORD + _DISTINCT_WORDS for w in pair] + ["麼", "什麼"]:
            key = variants.script_key(word)
            assert variants.script_key(key) == key, word


class TestToScript:
    @pytest.fixture(autouse=True)
    def _opencc(self) -> None:
        pytest.importorskip("opencc")

    def test_simplified_converts_a_traditional_word(self) -> None:
        assert variants.to_script("頭髮", "simplified") == "头发"

    def test_simplified_keeps_an_ambiguous_traditional_word(self) -> None:
        # 麵 (noodles) -> 面 would merge it with 面 (face); the front stays its own key.
        assert variants.to_script("麵", "simplified") == "麵"

    def test_traditional_converts_a_simplified_word_to_taiwan_spelling(self) -> None:
        assert variants.to_script("这里", "traditional") == "這裡"

    def test_traditional_keeps_a_traditional_word_as_written(self) -> None:
        assert variants.to_script("頭髮", "traditional") == "頭髮"

    def test_traditional_front_shares_the_source_key(self) -> None:
        for word in ("头发", "银行", "面条", "干部"):
            assert variants.script_key(variants.to_script(word, "traditional")) == variants.script_key(word)

    def test_no_variant_keeps_the_text(self) -> None:
        assert variants.to_script("頭髮", "") == "頭髮"


class TestOpenCCAbsent:
    """The graceful-degradation contract, exercised through the real import.

    ``sys.modules["opencc"] = None`` makes ``import opencc`` raise ImportError,
    which is what an uninstalled extra looks like from inside ``_converter``.
    """

    @pytest.fixture(autouse=True)
    def _block_opencc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "opencc", None)

    def test_converter_is_none(self) -> None:
        assert variants._converter("s2t") is None

    def test_variant_candidates_yield_the_word_alone(self) -> None:
        assert variants.variant_candidates("汉字") == ["汉字"]

    def test_to_traditional_returns_the_normalised_input(self) -> None:
        assert variants.to_traditional("\ufa0c") == "\u5140"

    def test_to_simplified_returns_the_normalised_input(self) -> None:
        assert variants.to_simplified("\ufa0c") == "\u5140"

    def test_script_key_is_the_normalised_word(self) -> None:
        assert variants.script_key("\u982d\u9aee") == "\u982d\u9aee"
        assert variants.script_key("\ufa0c") == "\u5140"

    def test_to_script_returns_the_normalised_input(self) -> None:
        assert variants.to_script("\u5934\u53d1", "traditional") == "\u5934\u53d1"


def test_the_zh_package_imports_without_opencc() -> None:
    """No module-level ``import opencc`` anywhere the zh package reaches."""
    root = str(Path(anki_miner.__file__).resolve().parents[1])
    src = (
        "import sys; sys.modules['opencc'] = None;"
        "import anki_miner.languages.zh, anki_miner.languages.zh.variants as v;"
        "print(v.variant_candidates('\\u6c49\\u5b57'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": root},
    )
    assert out.stdout.strip() == "['汉字']", out.stdout
