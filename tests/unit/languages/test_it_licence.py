"""The it_core_news_sm licence notice ships with the app (spec §8; the kiwipiepy notice pattern)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
NOTICE = ROOT / "licenses" / "it_core_news_sm"


def test_the_notice_carries_the_model_licence_and_its_sources():
    assert "Attribution-NonCommercial-ShareAlike 3.0 Unported" in (NOTICE / "LICENSE").read_text(encoding="utf-8")
    sources = (NOTICE / "LICENSES_SOURCES").read_text(encoding="utf-8")
    assert "UD Italian ISDT v2.8" in sources and "WikiNER" in sources
    readme = (NOTICE / "README.md").read_text(encoding="utf-8")
    assert "CC BY-NC-SA 3.0" in readme and "anki_miner/languages/it/pack.py" in readme


def test_the_spec_ships_the_notice():
    spec = (ROOT / "anki_miner.spec").read_text(encoding="utf-8")
    assert '"licenses", "it_core_news_sm"' in spec
    assert "+ it_core_news_sm_license_datas" in spec
