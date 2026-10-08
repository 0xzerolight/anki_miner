"""No-definition-source warning matrix for build_definition_service (Issue #100).

The reporter's clean install (jmdict-english enabled but missing on disk) mined
definition-less cards with no warning naming the cause. Every row with no
available provider warns.
"""

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils import service_factory
from anki_miner.gui.utils.service_factory import ServiceLoadResult, build_definition_service

NO_DICT_SNIPPET = "Cards will have no definitions"


class _FakeProvider:
    """Minimal DictionaryProvider stand-in."""

    def __init__(self, name: str, *, available: bool) -> None:
        self.name = name
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def load(self) -> None:
        pass


class _FakeRegistry:
    def __init__(self, providers) -> None:
        self._providers = providers

    def build_provider_chain(self, config):
        return list(self._providers)


def _chain_config(test_config: AnkiMinerConfig, *entries: ChainEntry) -> AnkiMinerConfig:
    return replace(test_config, dictionary_chain=tuple(entries))


def _build(config: AnkiMinerConfig, providers) -> ServiceLoadResult:
    load_result = ServiceLoadResult()
    service = build_definition_service(config, load_result, registry=_FakeRegistry(providers))
    service.close()
    return load_result


def test_enabled_but_missing_dict_warns_no_dictionary(test_config):
    # Missing slot is dropped by build_provider_chain -> empty providers list.
    config = _chain_config(test_config, ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True))
    load_result = _build(config, [])
    assert any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_built_but_unavailable_provider_warns_no_dictionary(test_config):
    config = _chain_config(test_config, ChainEntry(kind="indexed", dict_id="broken", enabled=True))
    broken = _FakeProvider("broken", available=False)
    load_result = _build(config, [broken])
    # PRESENCE assertion: "Skipping unavailable provider(s)" co-emits here.
    assert any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_available_offline_dict_no_warning(test_config):
    config = _chain_config(test_config, ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True))
    dict_provider = _FakeProvider("JMdict", available=True)
    load_result = _build(config, [dict_provider])
    assert not any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_zero_enabled_entries_warns_no_dictionary(test_config):
    config = _chain_config(
        test_config,
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
    )
    load_result = _build(config, [])
    assert any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_no_dictionary_warning_helper_is_actionable():
    text = service_factory._no_dictionary_warning()
    assert "Settings" in text and "Dictionaries" in text
