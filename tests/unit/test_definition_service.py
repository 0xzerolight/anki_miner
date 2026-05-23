"""Tests for DefinitionService — chain walking over injected providers."""

from unittest.mock import MagicMock

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.definition_service import DefinitionService


def make_provider(name="Test", available=True, return_value=None, load_raises=None):
    """Create a mock DictionaryProvider with configurable behavior."""
    p = MagicMock()
    p.name = name
    p.is_online = False  # default; tests override as needed
    p.is_available.return_value = available
    p.lookup.return_value = return_value
    if load_raises is not None:
        p.load.side_effect = load_raises
    else:
        p.load.return_value = True
    return p


class TestGetDefinition:
    """Tests for DefinitionService.get_definition chain walking."""

    def test_first_hit_wins(self, test_config):
        """When the first provider returns a definition, later providers are not called."""
        p1 = make_provider("A", return_value="from A")
        p2 = make_provider("B", return_value="from B")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definition("x") == "from A"
        p1.lookup.assert_called_once_with("x")
        p2.lookup.assert_not_called()

    def test_falls_through_when_first_misses(self, test_config):
        """When the first provider returns None, fall through to the next provider."""
        p1 = make_provider("A", return_value=None)
        p2 = make_provider("B", return_value="from B")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definition("x") == "from B"
        p1.lookup.assert_called_once_with("x")
        p2.lookup.assert_called_once_with("x")

    def test_skips_unavailable_provider(self, test_config):
        """Providers where is_available() returns False are skipped without calling lookup()."""
        p1 = make_provider("offline", available=False, return_value="should not be returned")
        p2 = make_provider("online", available=True, return_value="online result")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definition("x") == "online result"
        p1.lookup.assert_not_called()
        p2.lookup.assert_called_once()

    def test_returns_none_when_all_miss(self, test_config):
        """When every provider returns None, get_definition returns None."""
        p1 = make_provider("A", return_value=None)
        p2 = make_provider("B", return_value=None)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definition("unknown") is None

    def test_returns_none_when_no_providers(self, test_config):
        """Empty provider list yields None for every lookup."""
        service = DefinitionService(test_config, providers=[])
        assert service.get_definition("x") is None

    def test_returns_none_when_all_unavailable(self, test_config):
        """When every provider is unavailable, get_definition returns None."""
        p1 = make_provider("A", available=False)
        p2 = make_provider("B", available=False)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_definition("x") is None
        p1.lookup.assert_not_called()
        p2.lookup.assert_not_called()


class TestEnsureLoaded:
    """Tests for DefinitionService.ensure_loaded idempotence and load() dispatch."""

    def test_calls_load_on_every_provider(self, test_config):
        """ensure_loaded must invoke load() on each provider exactly once."""
        p1 = make_provider("A")
        p2 = make_provider("B")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.ensure_loaded()

        p1.load.assert_called_once()
        p2.load.assert_called_once()

    def test_returns_true_when_at_least_one_available(self, test_config):
        """Returns True when any provider is_available() after load."""
        p1 = make_provider("A", available=False)
        p2 = make_provider("B", available=True)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.ensure_loaded() is True

    def test_returns_false_when_no_provider_available(self, test_config):
        """Returns False when every provider is_available() is False."""
        p1 = make_provider("A", available=False)
        p2 = make_provider("B", available=False)
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.ensure_loaded() is False

    def test_returns_false_when_no_providers_configured(self, test_config):
        """Returns False when the provider list is empty."""
        service = DefinitionService(test_config, providers=[])
        assert service.ensure_loaded() is False

    def test_idempotent_load(self, test_config):
        """Multiple calls to ensure_loaded() only invoke provider.load() once."""
        p1 = make_provider("A")
        service = DefinitionService(test_config, providers=[p1])

        service.ensure_loaded()
        service.ensure_loaded()
        service.ensure_loaded()

        p1.load.assert_called_once()

    def test_swallows_provider_load_exception(self, test_config):
        """A provider raising during load() must not abort the chain."""
        p1 = make_provider("Broken", available=False, load_raises=Exception("boom"))
        p2 = make_provider("Working", available=True, return_value="ok")
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.ensure_loaded() is True
        assert service.get_definition("x") == "ok"

    def test_get_definition_triggers_ensure_loaded(self, test_config):
        """Calling get_definition() lazily loads providers."""
        p1 = make_provider("A", return_value="hit")
        service = DefinitionService(test_config, providers=[p1])

        # Did not call ensure_loaded explicitly
        result = service.get_definition("x")

        p1.load.assert_called_once()
        assert result == "hit"


class TestGetDefinitionsBatch:
    """Tests for DefinitionService.get_definitions_batch."""

    def test_returns_definitions_in_order(self, test_config):
        """Returned list mirrors the input word order."""
        responses = {"a": "def-a", "b": None, "c": "def-c"}
        p = make_provider("M", available=True)
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        results = service.get_definitions_batch(["a", "b", "c"])

        assert results == ["def-a", None, "def-c"]

    def test_empty_list_returns_empty_list(self, test_config):
        """Empty input yields an empty result list."""
        service = DefinitionService(test_config, providers=[])
        assert service.get_definitions_batch([]) == []

    def test_progress_callback_called_correctly(self, test_config, recording_progress):
        """Progress callbacks fire with the expected counts and statuses."""
        responses = {"a": "def-a", "b": None}
        p = make_provider("M", available=True)
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        service.get_definitions_batch(["a", "b"], progress_callback=recording_progress)

        # on_start called once with total count and description
        assert len(recording_progress.starts) == 1
        assert recording_progress.starts[0] == (2, "Fetching definitions")

        # on_progress called for each word (1-indexed)
        assert len(recording_progress.progresses) == 2
        assert recording_progress.progresses[0] == (1, "Definition found: a")
        assert recording_progress.progresses[1] == (2, "No definition: b")

        # on_complete called once
        assert recording_progress.completes == 1

    def test_batch_walks_chain_per_word(self, test_config):
        """Each word triggers the full chain walk independently."""
        p1 = make_provider("first", available=True)
        p1.lookup.side_effect = lambda w: "first-only" if w == "a" else None
        p2 = make_provider("second", available=True, return_value="second-result")
        service = DefinitionService(test_config, providers=[p1, p2])

        results = service.get_definitions_batch(["a", "b"])

        assert results == ["first-only", "second-result"]


class TestConfigStored:
    """The config object is stored verbatim (no mutation)."""

    def test_config_is_stored(self):
        """The passed config is accessible on the service."""
        config = AnkiMinerConfig()
        service = DefinitionService(config, providers=[])
        assert service.config is config


class TestGetGlossary:
    """Tests for DefinitionService.get_glossary — collect-all with online fallback."""

    def test_concatenates_all_offline_hits(self, test_config):
        p1 = make_provider("A", return_value="<div>A</div>")
        p1.is_online = False
        p2 = make_provider("B", return_value="<div>B</div>")
        p2.is_online = False
        service = DefinitionService(test_config, providers=[p1, p2])

        assert service.get_glossary("x") == "<div>A</div><div>B</div>"
        p1.lookup.assert_called_once_with("x")
        p2.lookup.assert_called_once_with("x")

    def test_skips_online_when_offline_hit_exists(self, test_config):
        offline = make_provider("Off", return_value="<div>off</div>")
        offline.is_online = False
        online = make_provider("Jisho", return_value="<div>online</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        assert service.get_glossary("x") == "<div>off</div>"
        offline.lookup.assert_called_once_with("x")
        online.lookup.assert_not_called()

    def test_uses_online_when_no_offline_hits(self, test_config):
        offline = make_provider("Off", return_value=None)
        offline.is_online = False
        online = make_provider("Jisho", return_value="<div>online</div>")
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        assert service.get_glossary("x") == "<div>online</div>"
        offline.lookup.assert_called_once_with("x")
        online.lookup.assert_called_once_with("x")

    def test_returns_none_when_all_miss(self, test_config):
        offline = make_provider("Off", return_value=None)
        offline.is_online = False
        online = make_provider("Jisho", return_value=None)
        online.is_online = True
        service = DefinitionService(test_config, providers=[offline, online])

        assert service.get_glossary("x") is None

    def test_skips_unavailable_providers(self, test_config):
        unavail = make_provider("X", available=False, return_value="<div>X</div>")
        unavail.is_online = False
        ok = make_provider("Y", available=True, return_value="<div>Y</div>")
        ok.is_online = False
        service = DefinitionService(test_config, providers=[unavail, ok])

        assert service.get_glossary("x") == "<div>Y</div>"
        unavail.lookup.assert_not_called()


class TestGetGlossariesBatch:
    """Tests for DefinitionService.get_glossaries_batch."""

    def test_returns_glossaries_in_order(self, test_config):
        responses = {"a": "<div>a</div>", "b": None, "c": "<div>c</div>"}
        p = make_provider("M", available=True)
        p.is_online = False
        p.lookup.side_effect = lambda w: responses.get(w)
        service = DefinitionService(test_config, providers=[p])

        results = service.get_glossaries_batch(["a", "b", "c"])

        assert results == ["<div>a</div>", None, "<div>c</div>"]

    def test_empty_list_returns_empty_list(self, test_config):
        service = DefinitionService(test_config, providers=[])
        assert service.get_glossaries_batch([]) == []


class TestClose:
    """Tests for DefinitionService.close (Issue #30 — Win11 sqlite handle release)."""

    def test_calls_close_on_each_provider_that_has_it(self, test_config):
        """Every provider exposing a ``close`` method must have it invoked."""
        p1 = make_provider("A")
        p2 = make_provider("B")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.close()

        p1.close.assert_called_once()
        p2.close.assert_called_once()

    def test_skips_providers_without_close(self, test_config):
        """Providers without a ``close`` attribute must not raise (Jisho case)."""
        p1 = MagicMock(spec=["name", "is_online", "is_available", "lookup", "load"])
        p1.name = "Jisho"
        p2 = make_provider("Indexed")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.close()  # must not raise even though p1 has no close()
        p2.close.assert_called_once()

    def test_swallows_provider_close_exception(self, test_config):
        """A provider raising during close() must not abort the rest of the chain."""
        p1 = make_provider("Broken")
        p1.close.side_effect = Exception("boom")
        p2 = make_provider("Working")
        service = DefinitionService(test_config, providers=[p1, p2])

        service.close()  # must not raise

        p1.close.assert_called_once()
        p2.close.assert_called_once()

    def test_resets_loaded_so_next_lookup_reopens(self, test_config):
        """After close(), the next get_definition() must re-invoke provider.load()."""
        p1 = make_provider("A", return_value="hit")
        service = DefinitionService(test_config, providers=[p1])

        service.ensure_loaded()
        p1.load.assert_called_once()

        service.close()
        service.get_definition("x")

        assert p1.load.call_count == 2

    def test_close_is_idempotent(self, test_config):
        """Two successive close() calls must not raise.

        Required so the tab-level ``release_dictionary_resources`` can be
        invoked repeatedly (e.g. user opens Settings, cancels, reopens)
        without surprises — Issue #30 follow-up that hardens the release
        path used by SingleEpisodeTab and BatchProcessingTab.
        """
        p1 = make_provider("A")
        service = DefinitionService(test_config, providers=[p1])

        service.close()
        service.close()  # must not raise

        assert p1.close.call_count == 2
