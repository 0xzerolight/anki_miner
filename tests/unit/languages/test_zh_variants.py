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
