"""Tests for the dev/tooling text-scale override at app startup.

``_dev_text_scale`` reads ``ANKI_MINER_TEXT_SCALE`` — the only thing left that
can stress text at a scale independent of Zoom (the UI atlas hostile cell,
``scripts/ui_atlas/isolation.py``) now that the removed ``ui_font_scale``
config field has no user-facing setting driving it.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.app import _dev_text_scale


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ANKI_MINER_TEXT_SCALE", raising=False)


def test_absent_defaults_to_one(monkeypatch):
    monkeypatch.delenv("ANKI_MINER_TEXT_SCALE", raising=False)

    assert _dev_text_scale() == 1.0


def test_malformed_falls_back_to_one(monkeypatch):
    monkeypatch.setenv("ANKI_MINER_TEXT_SCALE", "not-a-number")

    assert _dev_text_scale() == 1.0


def test_a_valid_value_is_used(monkeypatch):
    monkeypatch.setenv("ANKI_MINER_TEXT_SCALE", "1.5")

    assert _dev_text_scale() == 1.5


@pytest.mark.parametrize(
    "raw, expected",
    [("0.1", 0.5), ("3.0", 2.0)],
)
def test_out_of_range_values_are_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv("ANKI_MINER_TEXT_SCALE", raw)

    assert _dev_text_scale() == expected
