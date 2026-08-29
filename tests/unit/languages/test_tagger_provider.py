"""Tests for the per-language tagger provider (task 1A.6).

The provider wraps ``services.tagger.get_shared_tagger`` in place: the ja
branch keeps calling the *module-level* name in ``subtitle_parser`` /
``card_backfiller``, so every pre-existing monkeypatch site still bites.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.languages import tagger_provider
from anki_miner.languages.tagger_provider import get_tagger


@pytest.fixture(autouse=True)
def _clear_tagger_cache():
    """Evict the process-wide ``_TAGGERS`` cache around every test.

    Controller ruling R7: the cache is process-wide and never evicted in
    production, while ~99 pre-existing tests patch tagger construction. A cache
    entry leaking into (or out of) one of these tests is an order-dependent
    flake under ``--dist loadfile``.
    """
    tagger_provider._TAGGERS.clear()
    yield
    tagger_provider._TAGGERS.clear()


class _FakeAnki:
    """Minimum surface ``_scan_backfill_impl`` touches before the note loop."""

    def __init__(self, note_type: str, fields: list[str]) -> None:
        self._note_type = note_type
        self._fields = fields

    def note_type_names(self) -> list[str]:
        return [self._note_type]

    def ordered_note_type_field_names(self, _note_type: str) -> list[str]:
        return list(self._fields)

    def find_notes(self, _query: str) -> list[int]:
        return []


def _empty_services() -> SimpleNamespace:
    return SimpleNamespace(pitch_accent_service=None, frequency_service=None, definition_service=None)


# ---------------------------------------------------------------------------
# Provider core
# ---------------------------------------------------------------------------


def test_ja_returns_the_shared_locked_tagger():
    from anki_miner.services.tagger import get_shared_tagger

    assert get_tagger("ja") is get_shared_tagger()


def test_ja_is_the_default_language():
    from anki_miner.services.tagger import get_shared_tagger

    assert get_tagger() is get_shared_tagger()


def test_cache_is_per_language_and_never_evicted(monkeypatch):
    """One ``_build`` per language, and each language gets its own slot."""
    built: list[str] = []

    def fake_build(language: str) -> object:
        built.append(language)
        return SimpleNamespace(code=language)

    monkeypatch.setattr(tagger_provider, "_build", fake_build)

    first = get_tagger("zz")
    assert get_tagger("zz") is first
    assert built == ["zz"]

    other = get_tagger("yy")
    assert other is not first
    assert built == ["zz", "yy"]
    assert set(tagger_provider._TAGGERS) == {"zz", "yy"}


def test_unknown_language_raises_valueerror():
    with pytest.raises(ValueError):
        get_tagger("xx")


def test_unknown_language_is_not_cached():
    """A failed build must not poison the cache with ``None``."""
    with pytest.raises(ValueError):
        get_tagger("xx")
    assert "xx" not in tagger_provider._TAGGERS


def test_missing_tokenizer_dependency_is_reported_as_unregistered(monkeypatch):
    """A missing third-party engine reaches callers as the documented ValueError.

    ``languages/zh/tokenizer.py`` imports jieba at module level, so an install
    without the ``zh`` extra raised a bare ``ModuleNotFoundError`` out of every
    get_tagger("zh") — past every ``except ValueError`` the provider's contract
    tells callers to write.
    """
    monkeypatch.setitem(sys.modules, "jieba", None)
    monkeypatch.setitem(sys.modules, "jieba.posseg", None)

    with pytest.raises(ValueError) as excinfo:
        get_tagger("zh")

    assert "zh" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ImportError)
    assert "zh" not in tagger_provider._TAGGERS


# ---------------------------------------------------------------------------
# Composition sites: the ja branch keeps the pre-existing patch targets
# ---------------------------------------------------------------------------


def test_existing_patch_targets_still_resolve(monkeypatch, test_config):
    """The pre-existing monkeypatch sites must keep working."""
    from anki_miner.services import card_backfiller, subtitle_parser

    sentinel = object()
    monkeypatch.setattr(subtitle_parser, "get_shared_tagger", lambda: sentinel)
    monkeypatch.setattr(card_backfiller, "get_shared_tagger", lambda: sentinel)
    assert subtitle_parser.SubtitleParserService(test_config).tagger is sentinel


def test_ja_backfill_scan_uses_the_module_level_shared_tagger(monkeypatch, test_config):
    from anki_miner.services import card_backfiller

    sentinel = object()
    provider_calls: list[str] = []
    monkeypatch.setattr(card_backfiller, "get_shared_tagger", lambda: sentinel)
    monkeypatch.setattr(card_backfiller, "get_tagger", lambda language: provider_calls.append(language))

    anki = _FakeAnki(test_config.anki_note_type, ["word", "expression_furigana"])
    card_backfiller.scan_backfill(
        anki,
        test_config,
        _empty_services(),
        card_backfiller.BackfillOptions(field_keys=frozenset({"expression_furigana"})),
    )

    assert provider_calls == []


# ---------------------------------------------------------------------------
# Composition sites: the non-ja branch actually reaches the provider
# ---------------------------------------------------------------------------


def test_non_ja_parser_takes_its_tagger_from_the_provider(monkeypatch, test_config):
    from anki_miner.services import subtitle_parser

    sentinel = object()
    tagger_provider._TAGGERS["zh"] = sentinel

    def boom():  # pragma: no cover - fails the test if reached
        raise AssertionError("non-ja must not touch the ja shared tagger")

    monkeypatch.setattr(subtitle_parser, "get_shared_tagger", boom)

    parser = subtitle_parser.SubtitleParserService(replace(test_config, language="zh"))
    assert parser.tagger is sentinel


def test_non_ja_backfill_scan_takes_its_tagger_from_the_provider(monkeypatch, test_config):
    from anki_miner.services import card_backfiller

    sentinel = object()
    tagger_provider._TAGGERS["zh"] = sentinel
    provider_calls: list[str] = []

    def boom():  # pragma: no cover - fails the test if reached
        raise AssertionError("non-ja must not touch the ja shared tagger")

    def spy(language: str) -> object:
        provider_calls.append(language)
        return tagger_provider.get_tagger(language)

    monkeypatch.setattr(card_backfiller, "get_shared_tagger", boom)
    monkeypatch.setattr(card_backfiller, "get_tagger", spy)

    config = replace(test_config, language="zh")
    anki = _FakeAnki(config.anki_note_type, ["word", "expression_furigana"])
    card_backfiller.scan_backfill(
        anki,
        config,
        _empty_services(),
        card_backfiller.BackfillOptions(field_keys=frozenset({"expression_furigana"})),
    )

    assert provider_calls == ["zh"]


def test_prewarm_builds_the_tokenizer_for_the_configured_language(monkeypatch, test_config):
    """The prewarm warms the CONFIGURED language, not an unconditional ja."""
    pytest.importorskip("PyQt6.QtCore")
    from unittest.mock import MagicMock

    from anki_miner.gui.workers import prewarm_worker as prewarm_worker_module

    calls: list[str] = []
    monkeypatch.setattr(tagger_provider, "get_tagger", lambda language: calls.append(language))
    monkeypatch.setattr(prewarm_worker_module, "build_definition_service", MagicMock())

    prewarm_worker_module.PrewarmWorker(replace(test_config, language="zh")).run()

    assert calls == ["zh"]
