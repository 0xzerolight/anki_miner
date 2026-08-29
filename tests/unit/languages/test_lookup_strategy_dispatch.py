"""LookupStrategy dispatch — the lookup-miss fan-out becomes injectable.

Task 1A.4. ``DefinitionService._fallback_candidates`` stays a ``@staticmethod``
with its name and body untouched (``tests/unit/test_definition_service.py`` and
``scripts/engine_golden_contract_v2.py`` call it statically). What moves is the
two *in-service* call sites: they now go through the instance method
``fallback_candidates``, which delegates to the injected strategy and falls back
to the JA static when nothing was injected.

The ja-preserving default is what keeps an unconfigured service — every
pre-existing test, the golden contract script — byte-identical, and it is why
the composition root omits the keyword entirely for Japanese: for ja the two
calls mean the same thing, and only the shorter one matches the pre-transition
construction shape pre-existing tests pin.

``EpisodeProcessor``'s phase-2 probe was NOT part of this task: task 1A.9 gave
the processor a profile of its own and routed the probe through
``self.profile.lookup``. Its coverage lives in
``test_phase2_probe_dispatch.py``.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import service_factory
from anki_miner.languages import tagger_provider
from anki_miner.languages.registry import get_profile
from anki_miner.services.definition_service import DefinitionService


@pytest.fixture
def config(tmp_path):
    """Config whose on-disk paths live under tmp_path, not ~/.anki_miner."""
    return dataclasses.replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        stats_db_path=tmp_path / "stats.db",
    )


class _StubStrategy:
    """A LookupStrategy answering one fixed pair, recording every call."""

    def __init__(self, answer: list[tuple[str, int]] | None = None) -> None:
        self.answer = [("STUB", 0)] if answer is None else answer
        self.calls: list[tuple[str, str, str | None]] = []

    def candidates(self, word: str, orth_base: str, ctype: str | None) -> list[tuple[str, int]]:
        self.calls.append((word, orth_base, ctype))
        return list(self.answer)


class _StubProvider:
    """Offline provider answering only the strategy's candidate text."""

    is_online = False

    def __init__(self, hits: dict[str, str]) -> None:
        self.name = "stub"
        self._hits = hits
        self.fallback_calls: list[tuple[str, int]] = []

    def is_available(self) -> bool:
        return True

    def load(self) -> bool:
        return True

    def lookup(self, word: str) -> str | None:
        return self._hits.get(word)

    def lookup_fallback(self, word: str, conditions: int) -> str | None:
        self.fallback_calls.append((word, conditions))
        return self._hits.get(word)


def _use_stub_profile(monkeypatch, code: str, lookup: _StubStrategy):
    """Register a non-ja profile carrying *lookup*, and return the ja config swap.

    Built with ``dataclasses.replace`` off the real ja profile so every other
    field stays a working one — ``create_services`` reads ``mined_form`` off the
    same profile and must not trip over a half-built stub.

    Task 1A.6 added a second non-ja dependency the profile cannot carry:
    ``SubtitleParserService`` takes a non-ja tagger from
    ``languages.tagger_provider``, which raises for a code no tokenizer is
    registered for. Seed the provider cache with the ja tagger for the same
    reason the profile is a real one — ``monkeypatch.setitem`` drops the entry
    again at teardown, so the process-wide cache is left as it was found.

    The registry gets the same profile registered under *code*, because
    ``config_language`` degrades a code with no registered profile to ja before
    ``get_profile`` is ever reached.
    """
    from anki_miner.languages import registry

    stub_profile = dataclasses.replace(get_profile("ja"), code=code, lookup=lookup)
    real_get_profile = service_factory.get_profile

    def fake_get_profile(requested: str):
        return stub_profile if requested == code else real_get_profile(requested)

    monkeypatch.setattr(service_factory, "get_profile", fake_get_profile)
    monkeypatch.setitem(registry._BUILDERS, code, lambda: stub_profile)
    monkeypatch.setitem(registry._CACHE, code, stub_profile)
    monkeypatch.setitem(tagger_provider._TAGGERS, code, tagger_provider.get_tagger("ja"))
    return stub_profile


# --------------------------------------------------------------------------
# The instance method itself
# --------------------------------------------------------------------------


def test_instance_method_matches_static_for_ja(config):
    """No strategy injected ⇒ the JA static runs verbatim.

    This is the byte-stability guarantee for every unconfigured service: the
    pre-existing tests, and the frozen ``engine_golden_contract_v2.py``.
    """
    svc = DefinitionService(config, providers=[])

    assert svc.fallback_candidates("食べた", "", None) == DefinitionService._fallback_candidates("食べた", "", None)
    assert svc.fallback_candidates("帰れる", "返る", None) == DefinitionService._fallback_candidates(
        "帰れる", "返る", None
    )


def test_injected_strategy_is_consulted(config):
    stub = _StubStrategy()
    svc = DefinitionService(config, providers=[], lookup=stub)

    assert svc.fallback_candidates("x", "", None) == [("STUB", 0)]
    assert stub.calls == [("x", "", None)]


def test_injected_strategy_receives_all_three_arguments_positionally(config):
    """The ONE signature: candidates(word, orth_base, ctype), three args always."""
    stub = _StubStrategy()
    svc = DefinitionService(config, providers=[], lookup=stub)

    svc.fallback_candidates("殺る", "遣る", "五段-ラ行")

    assert stub.calls == [("殺る", "遣る", "五段-ラ行")]


def test_the_static_keeps_its_name_and_body(config):
    """``_fallback_candidates`` stays an unbound @staticmethod.

    ``scripts/engine_golden_contract_v2.py:389`` calls it off an *instance*
    (``service._fallback_candidates(...)``) and the pre-existing definition
    service tests call it off the *class*. Both must keep working.
    """
    svc = DefinitionService(config, providers=[], lookup=_StubStrategy())

    assert isinstance(DefinitionService.__dict__["_fallback_candidates"], staticmethod)
    # Bound-off-an-instance call still reaches the JA static, NOT the strategy.
    assert svc._fallback_candidates("食べた", "", None) == DefinitionService._fallback_candidates("食べた", "", None)


# --------------------------------------------------------------------------
# The two in-service call sites
# --------------------------------------------------------------------------


def test_fallback_lookup_offline_routes_through_the_strategy(config):
    """``_fallback_lookup_offline`` probes the strategy's candidates, not the static's."""
    provider = _StubProvider({"STUB": "<div>stub entry</div>"})
    svc = DefinitionService(config, providers=[provider], lookup=_StubStrategy())

    assert svc._fallback_lookup_offline("食べた", "", None) == "<div>stub entry</div>"
    assert provider.fallback_calls == [("STUB", 0)]


def test_lookup_all_offline_routes_through_the_strategy(config):
    """``lookup_all_offline`` probes the strategy's candidates, not the static's."""
    provider = _StubProvider({"STUB": "<div>stub entry</div>"})
    svc = DefinitionService(config, providers=[provider], lookup=_StubStrategy())

    assert svc.lookup_all_offline("食べた") == [("stub", "<div>stub entry</div>")]
    assert provider.fallback_calls == [("STUB", 0)]


def test_unconfigured_service_call_sites_keep_the_ja_ladder(config):
    """Same two sites with NO strategy: the JA deinflector ladder still fires."""
    provider = _StubProvider({"食べる": "<div>to eat</div>"})
    svc = DefinitionService(config, providers=[provider])

    assert svc._fallback_lookup_offline("食べさせられた", "", None) == "<div>to eat</div>"
    assert any(term == "食べる" for term, _conditions in provider.fallback_calls)


# --------------------------------------------------------------------------
# Composition root — the JA path omits the keyword, a non-JA path passes it
# --------------------------------------------------------------------------


def _patched_construction(config):
    """Run ``build_definition_service`` with the class patched; return the mock."""
    registry = MagicMock(name="registry")
    providers = [MagicMock(name="provider")]
    registry.build_provider_chain.return_value = providers

    with (
        patch.object(service_factory, "_load_dict_registry", return_value=registry),
        patch.object(service_factory, "DefinitionService") as service_cls,
    ):
        service_factory.build_definition_service(config)
    return service_cls, providers, registry


def test_ja_construction_omits_the_lookup_kwarg(config):
    """Japanese constructs with the exact pre-transition call shape.

    ``lookup=None`` already IS the JA ladder and the JA strategy is a pure
    delegate to it, so passing the keyword would be a no-op that nonetheless
    changes the call signature pre-existing tests pin
    (``test_service_factory.py::test_build_definition_service_reuses_loaded_registry``).
    """
    service_cls, providers, registry = _patched_construction(config)

    service_cls.assert_called_once_with(config, providers=providers, registry=registry)
    assert "lookup" not in service_cls.call_args.kwargs


def test_non_ja_construction_passes_the_lookup_kwarg(config, monkeypatch):
    """A non-JA profile's strategy reaches the constructor.

    The point of the omit-for-JA shortcut is that it is a shortcut, not a
    deletion: an unpassed keyword is a silently dead field, which is the exact
    failure this whole seam exists to prevent.
    """
    stub = _StubStrategy()
    _use_stub_profile(monkeypatch, "zh", stub)
    zh_config = dataclasses.replace(config, language="zh")

    service_cls, providers, registry = _patched_construction(zh_config)

    service_cls.assert_called_once_with(zh_config, providers=providers, registry=registry, lookup=stub)


def test_non_ja_service_dispatches_through_the_injected_strategy(config, monkeypatch):
    """End to end: the real service the factory returns answers from the profile."""
    stub = _StubStrategy()
    _use_stub_profile(monkeypatch, "zh", stub)
    zh_config = dataclasses.replace(config, language="zh")

    svc = service_factory.build_definition_service(zh_config)

    assert svc.fallback_candidates("猫", "", None) == [("STUB", 0)]
    assert stub.calls == [("猫", "", None)]


def test_both_factory_paths_carry_the_profile_strategy(config, monkeypatch):
    """``create_shared_lookup_services`` and ``create_services`` both carry it.

    Both route through ``build_definition_service``, which is also the
    ``PrewarmWorker`` path — one injection site covers all three.
    """
    stub = _StubStrategy()
    _use_stub_profile(monkeypatch, "zh", stub)
    zh_config = dataclasses.replace(config, language="zh")

    shared = service_factory.create_shared_lookup_services(zh_config)
    assert shared.definition_service._lookup is stub

    services = service_factory.create_services(zh_config)
    assert services.definition_service._lookup is stub


def test_ja_factory_paths_leave_the_strategy_unset(config):
    """The JA services carry no strategy at all — the default is the JA ladder."""
    shared = service_factory.create_shared_lookup_services(config)
    assert shared.definition_service._lookup is None

    services = service_factory.create_services(config)
    assert services.definition_service._lookup is None


def test_ja_profile_strategy_is_a_pure_delegate(config):
    """The JA profile's strategy answers exactly what the static answers.

    This is what licenses omitting the keyword for Japanese. If it drifts, the
    omission stops being behaviour-preserving — and the drift canary's
    definitions drift with it.
    """
    ja_lookup = get_profile(config.language).lookup

    for word, orth_base, ctype in (
        ("食べた", "", None),
        ("帰れる", "返る", None),
        ("ネコ", "", None),
        ("殺る", "遣る", "五段-ラ行"),
    ):
        assert ja_lookup.candidates(word, orth_base, ctype) == DefinitionService._fallback_candidates(
            word, orth_base, ctype
        )
