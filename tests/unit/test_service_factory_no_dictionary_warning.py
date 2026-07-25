"""No-definition-source warning matrix for build_definition_service (Issue #100).

The reporter's clean install (jmdict-english enabled but missing on disk,
Jisho disabled) mined definition-less cards while the only warning claimed
"using Jisho only". The decision is keyed on the absence of an available
OFFLINE provider — `JishoProvider.is_available()` is hard-True and Jisho sits
in the same providers list, so an `available`-empty predicate can never fire
with Jisho enabled.
"""

from dataclasses import replace

from anki_miner.config import AnkiMinerConfig, ChainEntry
from anki_miner.gui.utils import service_factory
from anki_miner.gui.utils.service_factory import ServiceLoadResult, build_definition_service

NO_DICT_SNIPPET = "cards will have empty definitions"
JISHO_ONLY_SNIPPET = "using Jisho only"


class _FakeProvider:
    """Minimal DictionaryProvider stand-in."""

    def __init__(self, name: str, *, available: bool, online: bool) -> None:
        self.name = name
        self._available = available
        self._online = online

    @property
    def is_online(self) -> bool:
        return self._online

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


def test_enabled_but_missing_dict_jisho_off_warns_no_dictionary(test_config):
    # Missing slot is dropped by build_provider_chain -> empty providers list.
    config = _chain_config(test_config, ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True))
    load_result = _build(config, [])
    assert any(NO_DICT_SNIPPET in w for w in load_result.warnings)
    assert not any(JISHO_ONLY_SNIPPET in w for w in load_result.warnings)


def test_enabled_but_missing_dict_jisho_on_warns_jisho_only(test_config):
    config = _chain_config(
        test_config,
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True),
        ChainEntry(kind="jisho", dict_id=None, enabled=True),
    )
    # Jisho itself IS available in the providers list — the predicate must
    # ignore online providers, or this row can never warn at all.
    jisho = _FakeProvider("Jisho API", available=True, online=True)
    load_result = _build(config, [jisho])
    assert any(JISHO_ONLY_SNIPPET in w for w in load_result.warnings)
    assert not any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_built_but_unavailable_provider_jisho_off_warns_no_dictionary(test_config):
    config = _chain_config(test_config, ChainEntry(kind="indexed", dict_id="broken", enabled=True))
    broken = _FakeProvider("broken", available=False, online=False)
    load_result = _build(config, [broken])
    # PRESENCE assertion: "Skipping unavailable provider(s)" co-emits here.
    assert any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_available_offline_dict_no_warning(test_config):
    config = _chain_config(test_config, ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=True))
    dict_provider = _FakeProvider("JMdict", available=True, online=False)
    load_result = _build(config, [dict_provider])
    assert not any(NO_DICT_SNIPPET in w for w in load_result.warnings)
    assert not any(JISHO_ONLY_SNIPPET in w for w in load_result.warnings)


def test_zero_enabled_entries_warns_no_dictionary(test_config):
    config = _chain_config(
        test_config,
        ChainEntry(kind="indexed", dict_id="jmdict-english", enabled=False),
        ChainEntry(kind="jisho", dict_id=None, enabled=False),
    )
    load_result = _build(config, [])
    assert any(NO_DICT_SNIPPET in w for w in load_result.warnings)


def test_no_dictionary_warning_helper_is_actionable():
    text = service_factory._no_dictionary_warning()
    assert "Settings" in text and "Dictionaries" in text
