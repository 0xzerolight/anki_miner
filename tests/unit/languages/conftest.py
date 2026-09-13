"""Fixtures for tests that drive the shared parser through the test-only eu stub.

Registers ``tests/unit/languages/eu_stub.py`` for one test (registry entries,
the admitted code, the whitespace tagger) exactly like
``test_eu_boundary_stub.py``'s own ``eu_profile``/``eu_parser`` fixtures, under
different names so that file keeps its own. ``monkeypatch`` undoes all four.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages import registry, tagger_provider
from anki_miner.languages.switching import switch_language
from tests.unit.languages.eu_stub import EU_CODE, WhitespaceTagger, build_profile


@pytest.fixture
def stub_eu_config(monkeypatch: pytest.MonkeyPatch) -> AnkiMinerConfig:
    profile = build_profile()
    monkeypatch.setitem(registry._BUILDERS, EU_CODE, lambda: profile)
    monkeypatch.setitem(registry._CACHE, EU_CODE, profile)
    monkeypatch.setattr("anki_miner.config.config._LANGUAGE_CODES", ("ja", "ko", "zh", EU_CODE))
    monkeypatch.setitem(tagger_provider._TAGGERS, EU_CODE, WhitespaceTagger())
    config = switch_language(AnkiMinerConfig(), EU_CODE)
    assert config.language == EU_CODE
    return config


@pytest.fixture
def make_eu_parser(stub_eu_config: AnkiMinerConfig) -> Callable[..., Any]:
    def _make(**parser_kwargs: Any) -> Any:
        return registry.get_profile(EU_CODE).create_parser(stub_eu_config, **parser_kwargs)

    return _make
