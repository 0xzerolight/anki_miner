"""Tests for definition_service module."""

from unittest.mock import MagicMock, patch

import requests

from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.providers.jisho_provider import JishoProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINI_JMDICT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
  <entry>
    <k_ele><keb>食べる</keb></k_ele>
    <r_ele><reb>たべる</reb></r_ele>
    <sense>
      <gloss>to eat</gloss>
    </sense>
    <sense>
      <gloss>to consume</gloss>
      <gloss>to devour</gloss>
    </sense>
  </entry>
  <entry>
    <k_ele><keb>飲む</keb></k_ele>
    <r_ele><reb>のむ</reb></r_ele>
    <sense>
      <gloss>to drink</gloss>
    </sense>
  </entry>
</JMdict>
"""

MANY_SENSES_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
  <entry>
    <k_ele><keb>取る</keb></k_ele>
    <r_ele><reb>とる</reb></r_ele>
    <sense><gloss>to take</gloss></sense>
    <sense><gloss>to pick up</gloss></sense>
    <sense><gloss>to harvest</gloss></sense>
    <sense><gloss>to earn</gloss></sense>
    <sense><gloss>to choose</gloss></sense>
    <sense><gloss>to steal</gloss></sense>
    <sense><gloss>to remove</gloss></sense>
  </entry>
</JMdict>
"""

COLLISION_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
  <entry>
    <k_ele><keb>生</keb></k_ele>
    <r_ele><reb>なま</reb></r_ele>
    <sense><gloss>raw</gloss></sense>
  </entry>
  <entry>
    <k_ele><keb>生</keb></k_ele>
    <r_ele><reb>せい</reb></r_ele>
    <sense><gloss>life</gloss></sense>
  </entry>
</JMdict>
"""


def _write_xml(tmp_path, content):
    """Write XML content to the standard JMdict path and return the path."""
    path = tmp_path / "JMdict_e"
    path.write_text(content, encoding="utf-8")
    return path


def _jisho_response(senses):
    """Build a mock Jisho API JSON response from a list of sense dicts."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"senses": senses}]}
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadOfflineDictionary:
    """Tests for load_offline_dictionary method."""

    def test_returns_false_when_offline_disabled(self, test_config):
        """Should return False immediately when use_offline_dict is False."""
        from dataclasses import replace

        config = replace(test_config, use_offline_dict=False)
        service = DefinitionService(config)

        result = service.load_offline_dictionary()

        assert result is False
        assert service._jmdict is None

    def test_returns_false_when_file_missing(self, test_config):
        """Should return False when the JMdict file does not exist."""
        service = DefinitionService(test_config)

        result = service.load_offline_dictionary()

        assert result is False

    def test_loads_valid_xml(self, test_config, tmp_path):
        """Should parse a valid JMdict XML and populate the dictionary."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)

        result = service.load_offline_dictionary()

        assert result is True
        assert service._jmdict is not None
        assert "食べる" in service._jmdict
        assert "飲む" in service._jmdict

    def test_returns_false_on_parse_error(self, test_config, tmp_path):
        """Should return False when XML is malformed."""
        path = tmp_path / "JMdict_e"
        path.write_text("<<< not valid xml >>>", encoding="utf-8")
        service = DefinitionService(test_config)

        result = service.load_offline_dictionary()

        assert result is False

    def test_stores_multiple_readings_per_entry(self, test_config, tmp_path):
        """Both kanji and kana readings should be stored as separate keys."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        # Both the kanji form and the kana reading should map to the same defs
        assert service._jmdict["食べる"] == service._jmdict["たべる"]
        assert service._jmdict["飲む"] == service._jmdict["のむ"]

    def test_first_reading_wins_on_collision(self, test_config, tmp_path):
        """When two entries share a reading, the first entry's defs are kept."""
        _write_xml(tmp_path, COLLISION_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        # The kanji "生" appears in both entries; the first ("raw") should win
        assert service._jmdict["生"] == ["raw"]
        # Each unique kana reading should still have its own entry
        assert service._jmdict["なま"] == ["raw"]
        assert service._jmdict["せい"] == ["life"]


class TestGetDefinition:
    """Tests for get_definition method."""

    def test_offline_hit_returns_offline_result(self, test_config, tmp_path):
        """Should return offline definition without calling Jisho."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        with patch.object(JishoProvider, "lookup") as mock_jisho:
            result = service.get_definition("食べる")

        mock_jisho.assert_not_called()
        assert result is not None
        assert "to eat" in result

    def test_returns_none_when_offline_loaded_but_word_missing(self, test_config, tmp_path):
        """Should return None when offline dict is loaded but word is not found.

        When use_offline_dict=True and _jmdict is loaded, the service does NOT
        fall back to Jisho -- it trusts the offline dictionary and returns None.
        """
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        with patch.object(JishoProvider, "lookup") as mock_jisho:
            result = service.get_definition("走る")

        mock_jisho.assert_not_called()
        assert result is None

    def test_falls_back_to_jisho_when_jmdict_none(self, test_config):
        """Should query Jisho when use_offline_dict=True but dict failed to load."""
        service = DefinitionService(test_config)
        # _jmdict is None (never loaded), so falls back to Jisho

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=_jisho_response([{"english_definitions": ["to run"]}]),
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("走る")

        assert result is not None
        assert "to run" in result

    def test_uses_jisho_directly_when_no_offline_dict(self, test_config):
        """Should query Jisho directly when offline dictionary is not loaded."""
        from dataclasses import replace

        config = replace(test_config, use_offline_dict=False)
        service = DefinitionService(config)

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=_jisho_response([{"english_definitions": ["to eat"]}]),
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("食べる")

        assert result is not None
        assert "to eat" in result


class TestGetDefinitionOffline:
    """Tests for offline definition lookup via the JMdict provider."""

    def test_returns_formatted_html_with_numbered_list(self, test_config, tmp_path):
        """Should return definitions as numbered HTML lines."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        result = service.get_definition("食べる")

        assert result == "1. to eat<br>2. to consume; to devour"

    def test_unknown_word_returns_none(self, test_config, tmp_path):
        """Should return None when the word is not in the dictionary."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        result = service.get_definition("走る")

        assert result is None

    def test_limits_to_five_definitions(self, test_config, tmp_path):
        """Should truncate to at most 5 definitions."""
        _write_xml(tmp_path, MANY_SENSES_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        result = service.get_definition("取る")

        # The word has 7 senses; only 5 should appear
        assert result is not None
        lines = result.split("<br>")
        assert len(lines) == 5
        assert lines[0].startswith("1.")
        assert lines[4].startswith("5.")
        # Senses 6 and 7 should be absent
        assert "steal" not in result
        assert "remove" not in result

    def test_returns_none_when_jmdict_not_loaded(self, test_config):
        """Should return None when the offline dictionary has not been loaded."""
        service = DefinitionService(test_config)

        # JMdict not loaded, Jisho is fallback but we mock it to return None
        with (
            patch("anki_miner.services.providers.jisho_provider.requests.get") as mock_get,
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": []}
            mock_get.return_value = mock_resp

            result = service.get_definition("食べる")

        assert result is None


class TestGetDefinitionJisho:
    """Tests for Jisho API lookup via the JishoProvider through DefinitionService."""

    def _make_service(self, test_config):
        """Create a DefinitionService with use_offline_dict=False (Jisho only)."""
        from dataclasses import replace

        config = replace(test_config, use_offline_dict=False)
        return DefinitionService(config)

    def test_success_returns_formatted_html(self, test_config):
        """Should return numbered HTML definitions on successful API call."""
        service = self._make_service(test_config)
        mock_resp = _jisho_response(
            [
                {"english_definitions": ["to eat", "to consume"]},
                {"english_definitions": ["to live on"]},
            ]
        )

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=mock_resp,
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("食べる")

        assert result == "1. to eat; to consume<br>2. to live on"

    def test_non_200_status_returns_none(self, test_config):
        """Should return None when API responds with non-200 status."""
        service = self._make_service(test_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=mock_resp,
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("食べる")

        assert result is None

    def test_empty_results_returns_none(self, test_config):
        """Should return None when API returns empty results."""
        service = self._make_service(test_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=mock_resp,
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("xyzzy")

        assert result is None

    def test_timeout_returns_none(self, test_config):
        """Should return None when the request times out."""
        service = self._make_service(test_config)

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                side_effect=requests.exceptions.Timeout(),
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("食べる")

        assert result is None

    def test_request_exception_returns_none(self, test_config):
        """Should return None on generic request exception."""
        service = self._make_service(test_config)

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                side_effect=requests.exceptions.ConnectionError(),
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("食べる")

        assert result is None

    def test_rate_limiting_delay_applied(self, test_config):
        """Should call time.sleep with the configured delay."""
        service = self._make_service(test_config)
        mock_resp = _jisho_response([{"english_definitions": ["to eat"]}])

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=mock_resp,
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep") as mock_sleep,
        ):
            service.get_definition("食べる")

        mock_sleep.assert_called_once_with(test_config.jisho_delay)

    def test_limits_to_five_senses(self, test_config):
        """Should include at most 5 senses from the Jisho response."""
        service = self._make_service(test_config)
        senses = [{"english_definitions": [f"meaning {i}"]} for i in range(1, 8)]
        mock_resp = _jisho_response(senses)

        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=mock_resp,
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep"),
        ):
            result = service.get_definition("取る")

        assert result is not None
        lines = result.split("<br>")
        assert len(lines) == 5
        assert "meaning 1" in lines[0]
        assert "meaning 5" in lines[4]
        assert "meaning 6" not in result
        assert "meaning 7" not in result


class TestGetDefinitionsBatch:
    """Tests for get_definitions_batch method."""

    def test_returns_definitions_in_order(self, test_config, tmp_path):
        """Should return definitions matching the input word order."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        results = service.get_definitions_batch(["食べる", "走る", "飲む"])

        assert len(results) == 3
        # "食べる" and "飲む" are in the dictionary; "走る" is not
        assert results[0] is not None
        assert "to eat" in results[0]
        assert results[1] is None
        assert results[2] is not None
        assert "to drink" in results[2]

    def test_progress_callback_called_correctly(self, test_config, tmp_path, recording_progress):
        """Should invoke progress callbacks with correct counts and statuses."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        words = ["食べる", "走る"]
        service.get_definitions_batch(words, progress_callback=recording_progress)

        # on_start called once with total count and description
        assert len(recording_progress.starts) == 1
        assert recording_progress.starts[0] == (2, "Fetching definitions")

        # on_progress called for each word (1-indexed)
        assert len(recording_progress.progresses) == 2
        assert recording_progress.progresses[0] == (1, "Definition found: 食べる")
        assert recording_progress.progresses[1] == (2, "No definition: 走る")

        # on_complete called once
        assert recording_progress.completes == 1

    def test_empty_list_returns_empty_list(self, test_config):
        """Should return an empty list when given no words."""
        service = DefinitionService(test_config)

        results = service.get_definitions_batch([])

        assert results == []


class TestCustomProviders:
    """Tests for DefinitionService with explicitly provided custom providers."""

    def _mock_provider(self, name, available=True, lookup_return=None):
        """Create a mock DictionaryProvider."""
        provider = MagicMock()
        provider.name = name
        provider.is_available.return_value = available
        provider.lookup.return_value = lookup_return
        provider.load.return_value = True
        return provider

    def test_tries_providers_in_order(self, test_config):
        """Should try providers in order and return second when first misses."""
        p1 = self._mock_provider("first", lookup_return=None)
        p2 = self._mock_provider("second", lookup_return="1. to run")

        service = DefinitionService(test_config, providers=[p1, p2])
        result = service.get_definition("走る")

        assert result == "1. to run"
        p1.lookup.assert_called_once_with("走る")
        p2.lookup.assert_called_once_with("走る")

    def test_first_hit_wins(self, test_config):
        """Should return the first provider's result and not call the second."""
        p1 = self._mock_provider("first", lookup_return="1. to eat")
        p2 = self._mock_provider("second", lookup_return="1. to consume")

        service = DefinitionService(test_config, providers=[p1, p2])
        result = service.get_definition("食べる")

        assert result == "1. to eat"
        p1.lookup.assert_called_once()
        p2.lookup.assert_not_called()

    def test_skips_unavailable_provider(self, test_config):
        """Should skip providers where is_available() returns False."""
        p1 = self._mock_provider("offline", available=False, lookup_return="1. def")
        p2 = self._mock_provider("online", available=True, lookup_return="1. to run")

        service = DefinitionService(test_config, providers=[p1, p2])
        result = service.get_definition("走る")

        assert result == "1. to run"
        p1.lookup.assert_not_called()
        p2.lookup.assert_called_once()

    def test_returns_none_when_all_miss(self, test_config):
        """Should return None when all providers return None."""
        p1 = self._mock_provider("first", lookup_return=None)
        p2 = self._mock_provider("second", lookup_return=None)

        service = DefinitionService(test_config, providers=[p1, p2])
        result = service.get_definition("不明")

        assert result is None

    def test_empty_provider_list(self, test_config):
        """Should return None when providers list is empty."""
        service = DefinitionService(test_config, providers=[])
        result = service.get_definition("食べる")

        assert result is None


class TestLoadProviders:
    """Tests for DefinitionService.load_providers method."""

    def _mock_provider(self, name, load_success=True, load_raises=None):
        """Create a mock provider with configurable load behavior."""
        provider = MagicMock()
        provider.name = name
        if load_raises:
            provider.load.side_effect = load_raises
        else:
            provider.load.return_value = load_success
        return provider

    def test_returns_success_dict(self, test_config):
        """Should return dict mapping provider names to load success status."""
        p1 = self._mock_provider("DictA", load_success=True)
        p2 = self._mock_provider("DictB", load_success=True)

        service = DefinitionService(test_config, providers=[p1, p2])
        results = service.load_providers()

        assert results == {"DictA": True, "DictB": True}

    def test_catches_provider_load_exception(self, test_config):
        """Should catch exceptions from provider.load() and mark as False."""
        p1 = self._mock_provider("Broken", load_raises=Exception("file corrupt"))

        service = DefinitionService(test_config, providers=[p1])
        results = service.load_providers()

        assert results == {"Broken": False}

    def test_partial_failure(self, test_config):
        """Should reflect mixed success/failure in results dict."""
        p1 = self._mock_provider("Good", load_success=True)
        p2 = self._mock_provider("Bad", load_raises=Exception("oops"))

        service = DefinitionService(test_config, providers=[p1, p2])
        results = service.load_providers()

        assert results == {"Good": True, "Bad": False}
