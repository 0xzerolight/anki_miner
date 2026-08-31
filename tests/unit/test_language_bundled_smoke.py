"""ANKI_MINER_SMOKE=<code>: the frozen-bundle language leg, run in-process."""

from __future__ import annotations

import pytest

from anki_miner.gui import app as app_module
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile


@pytest.mark.parametrize("code", AVAILABLE_LANGUAGES)
def test_every_available_language_resolves_a_smoke_line(code):
    line = app_module._LANGUAGE_SMOKE_LINES.get(code) or get_profile(code).smoke_sentence
    assert line


def test_ja_leg_passes_and_prints_the_marker(capsys):
    assert app_module._run_language_bundled_smoke("ja") == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out


def test_zh_leg_passes_and_prints_the_marker(capsys):
    pytest.importorskip("jieba")
    pytest.importorskip("pypinyin")
    pytest.importorskip("opencc")
    assert app_module._run_language_bundled_smoke("zh") == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out


def test_a_chained_cause_travels_on_the_marker_line(capsys, monkeypatch):
    """CI sees only this line, and the flat message names no missing module."""
    from anki_miner.languages import registry

    def _boom(code: str):
        try:
            raise ModuleNotFoundError("No module named 'anki_miner.languages.zh.pack'")
        except ModuleNotFoundError as exc:
            raise ValueError(f"No tokenizer registered for language: {code!r}") from exc

    monkeypatch.setattr(registry, "get_profile", _boom)

    assert app_module._run_language_bundled_smoke("zh") == 1
    err = capsys.readouterr().err
    assert "BUNDLED_SMOKE_FAIL: ValueError: No tokenizer registered" in err
    assert "(cause: ModuleNotFoundError: No module named 'anki_miner.languages.zh.pack')" in err


def test_an_unbuilt_language_fails_closed(capsys):
    assert app_module._run_language_bundled_smoke("xx") == 1
    assert "BUNDLED_SMOKE_FAIL" in capsys.readouterr().err
