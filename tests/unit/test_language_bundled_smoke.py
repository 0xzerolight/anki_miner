"""ANKI_MINER_SMOKE=<code>: the frozen-bundle language leg, run in-process."""

from __future__ import annotations

import pytest

from anki_miner.gui import app as app_module


def test_ja_leg_passes_and_prints_the_marker(capsys):
    assert app_module._run_language_bundled_smoke("ja") == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out


def test_zh_leg_passes_and_prints_the_marker(capsys):
    pytest.importorskip("jieba")
    pytest.importorskip("pypinyin")
    pytest.importorskip("opencc")
    assert app_module._run_language_bundled_smoke("zh") == 0
    assert "BUNDLED_SMOKE_PASS" in capsys.readouterr().out


def test_an_unbuilt_language_fails_closed(capsys):
    assert app_module._run_language_bundled_smoke("xx") == 1
    assert "BUNDLED_SMOKE_FAIL" in capsys.readouterr().err
