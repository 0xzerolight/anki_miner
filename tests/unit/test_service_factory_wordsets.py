import dataclasses

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils import service_factory


class _Fake:
    def __init__(self, enabled_ids, resource_dir=None):
        self.enabled_ids = enabled_ids
        self.loaded = False

    def load(self):
        self.loaded = True

    def is_available(self):
        return True


def test_factory_builds_wordset_service_when_enabled(monkeypatch):
    monkeypatch.setattr(service_factory, "WordsetService", _Fake, raising=True)
    cfg = dataclasses.replace(AnkiMinerConfig(), excluded_wordsets=("surnames",))
    services = service_factory.create_services(cfg)
    assert services.wordset_service is not None
    assert services.wordset_service.loaded


def test_factory_skips_wordset_service_when_empty():
    services = service_factory.create_services(AnkiMinerConfig())
    assert services.wordset_service is None
