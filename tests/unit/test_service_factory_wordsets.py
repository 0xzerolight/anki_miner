import dataclasses

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import service_factory
from anki_miner.services.subtitle_parser import PARSE_RELEVANT_CONFIG_FIELDS


class _Fake:
    def __init__(self, enabled_ids, resource_dir=None):
        self.enabled_ids = enabled_ids
        self.loaded = False

    def load(self):
        self.loaded = True

    def is_available(self):
        return True

    def excluded_terms(self, terms):
        return set(terms)


@pytest.fixture
def base_config(tmp_path):
    """A config whose on-disk paths live under tmp_path, not ~/.anki_miner.

    create_services() scans ``dicts_root`` and constructs ``KnownWordDB`` at
    ``known_words_db_path``; a bare ``AnkiMinerConfig()`` would point both at
    the developer's real home directory.
    """
    return dataclasses.replace(
        AnkiMinerConfig(),
        dicts_root=tmp_path / "dicts",
        known_words_db_path=tmp_path / "known_words.db",
        stats_db_path=tmp_path / "stats.db",
    )


def test_factory_builds_wordset_service_when_enabled(monkeypatch, base_config):
    monkeypatch.setattr(service_factory, "WordsetService", _Fake, raising=True)
    cfg = dataclasses.replace(base_config, excluded_wordsets=("surnames",))
    services = service_factory.create_services(cfg)
    assert services.wordset_service is not None
    assert services.wordset_service.loaded


def test_factory_injects_loaded_wordset_lookup_into_fresh_parser(monkeypatch, base_config):
    monkeypatch.setattr(service_factory, "WordsetService", _Fake, raising=True)
    cfg = dataclasses.replace(base_config, excluded_wordsets=("surnames",))

    services = service_factory.create_services(cfg)

    assert services.wordset_service is not None
    matcher = services.subtitle_parser._name_matcher
    assert matcher is not None
    assert matcher._lookup == services.wordset_service.excluded_terms


def test_enabled_wordsets_are_parse_relevant():
    assert "excluded_wordsets" in PARSE_RELEVANT_CONFIG_FIELDS


def test_factory_builds_wordset_service_by_default(monkeypatch, base_config):
    """Default-ON (junk-reduction r3): the default config wires the service."""
    monkeypatch.setattr(service_factory, "WordsetService", _Fake, raising=True)
    # base_config keeps the dataclass default (all four sets enabled).
    services = service_factory.create_services(base_config)
    assert services.wordset_service is not None
    assert services.wordset_service.loaded


def test_factory_skips_wordset_service_when_empty(base_config):
    cfg = dataclasses.replace(base_config, excluded_wordsets=())
    services = service_factory.create_services(cfg)
    assert services.wordset_service is None


class _OutOfMemory:
    """A wordset service whose union allocation fails."""

    def __init__(self, enabled_ids, resource_dir=None):
        self.enabled_ids = enabled_ids

    def load(self):
        raise MemoryError("wordset union allocation failed")

    def is_available(self):
        return True


class _Unreadable:
    """A wordset service whose data is missing or malformed."""

    def __init__(self, enabled_ids, resource_dir=None):
        self.enabled_ids = enabled_ids

    def load(self):
        raise OSError("wordset file is unreadable")

    def is_available(self):
        return True


def test_memory_error_fails_the_run(monkeypatch, base_config):
    """Exhaustion must not disable the filter and let mining continue."""
    monkeypatch.setattr(service_factory, "WordsetService", _OutOfMemory, raising=True)
    cfg = dataclasses.replace(base_config, excluded_wordsets=("surnames",))
    with pytest.raises(MemoryError):
        service_factory.create_services(cfg)


def test_unreadable_data_still_warns_and_disables(monkeypatch, base_config):
    """The recoverable path is unchanged: warn, disable, keep mining."""
    monkeypatch.setattr(service_factory, "WordsetService", _Unreadable, raising=True)
    cfg = dataclasses.replace(base_config, excluded_wordsets=("surnames",))
    services = service_factory.create_services(cfg)
    assert services.wordset_service is None
