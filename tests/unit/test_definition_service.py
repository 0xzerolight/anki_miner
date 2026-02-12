"""Tests for definition_service module."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from anki_miner.exceptions import SetupError
from anki_miner.services.definition_service import DefinitionService

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

    def test_raises_setup_error_when_file_missing(self, test_config):
        """Should raise SetupError when the JMdict file does not exist."""
        service = DefinitionService(test_config)

        with pytest.raises(SetupError, match="JMdict file not found"):
            service.load_offline_dictionary()

    def test_loads_valid_xml(self, test_config, tmp_path):
        """Should parse a valid JMdict XML and populate the dictionary."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)

        result = service.load_offline_dictionary()

        assert result is True
        assert service._jmdict is not None
        assert "食べる" in service._jmdict
        assert "飲む" in service._jmdict

    def test_raises_setup_error_on_parse_error(self, test_config, tmp_path):
        """Should raise SetupError when XML is malformed."""
        path = tmp_path / "JMdict_e"
        path.write_text("<<< not valid xml >>>", encoding="utf-8")
        service = DefinitionService(test_config)

        with pytest.raises(SetupError, match="Error parsing JMdict XML"):
            service.load_offline_dictionary()

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

        with patch("anki_miner.services.definition_service.requests.get") as mock_get:
            result = service.get_definition("食べる")

        mock_get.assert_not_called()
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

        with patch("anki_miner.services.definition_service.requests.get") as mock_get:
            result = service.get_definition("走る")

        mock_get.assert_not_called()
        assert result is None

    def test_falls_back_to_jisho_when_jmdict_none(self, test_config):
        """Should query Jisho when use_offline_dict=True but dict failed to load."""
        service = DefinitionService(test_config)
        # _jmdict is None (never loaded)

        mock_resp = _jisho_response(
            [
                {"english_definitions": ["to run"]},
            ]
        )

        with (
            patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp),
            patch("anki_miner.services.definition_service.time.sleep"),
        ):
            result = service.get_definition("走る")

        assert result is not None
        assert "to run" in result

    def test_uses_jisho_directly_when_no_offline_dict(self, test_config):
        """Should query Jisho directly when offline dictionary is not loaded."""
        from dataclasses import replace

        config = replace(test_config, use_offline_dict=False)
        service = DefinitionService(config)

        mock_resp = _jisho_response(
            [
                {"english_definitions": ["to eat"]},
            ]
        )

        with (
            patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp),
            patch("anki_miner.services.definition_service.time.sleep"),
        ):
            result = service.get_definition("食べる")

        assert result is not None
        assert "to eat" in result


class TestGetDefinitionOffline:
    """Tests for _get_definition_offline method."""

    def test_returns_formatted_html_with_numbered_list(self, test_config, tmp_path):
        """Should return definitions as numbered HTML lines."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        result = service._get_definition_offline("食べる")

        assert result == "1. to eat<br>2. to consume; to devour"

    def test_unknown_word_returns_none(self, test_config, tmp_path):
        """Should return None when the word is not in the dictionary."""
        _write_xml(tmp_path, MINI_JMDICT_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        result = service._get_definition_offline("走る")

        assert result is None

    def test_limits_to_five_definitions(self, test_config, tmp_path):
        """Should truncate to at most 5 definitions."""
        _write_xml(tmp_path, MANY_SENSES_XML)
        service = DefinitionService(test_config)
        service.load_offline_dictionary()

        result = service._get_definition_offline("取る")

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

        result = service._get_definition_offline("食べる")

        assert result is None


class TestGetDefinitionJisho:
    """Tests for _get_definition_jisho method."""

    def test_success_returns_formatted_html(self, test_config):
        """Should return numbered HTML definitions on successful API call."""
        service = DefinitionService(test_config)
        mock_resp = _jisho_response(
            [
                {"english_definitions": ["to eat", "to consume"]},
                {"english_definitions": ["to live on"]},
            ]
        )

        with patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp):
            result = service._get_definition_jisho("食べる", apply_delay=False)

        assert result == "1. to eat; to consume<br>2. to live on"

    def test_non_200_status_returns_none(self, test_config):
        """Should return None when API responds with non-200 status."""
        service = DefinitionService(test_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp):
            result = service._get_definition_jisho("食べる", apply_delay=False)

        assert result is None

    def test_empty_results_returns_none(self, test_config):
        """Should return None when API returns empty results."""
        service = DefinitionService(test_config)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": []}

        with patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp):
            result = service._get_definition_jisho("xyzzy", apply_delay=False)

        assert result is None

    def test_timeout_returns_none(self, test_config):
        """Should return None when the request times out."""
        service = DefinitionService(test_config)

        with patch(
            "anki_miner.services.definition_service.requests.get",
            side_effect=requests.exceptions.Timeout(),
        ):
            result = service._get_definition_jisho("食べる", apply_delay=False)

        assert result is None

    def test_request_exception_returns_none(self, test_config):
        """Should return None on generic request exception."""
        service = DefinitionService(test_config)

        with patch(
            "anki_miner.services.definition_service.requests.get",
            side_effect=requests.exceptions.ConnectionError(),
        ):
            result = service._get_definition_jisho("食べる", apply_delay=False)

        assert result is None

    def test_rate_limiting_delay_applied(self, test_config):
        """Should call time.sleep with jisho_delay when apply_delay is True."""
        service = DefinitionService(test_config)
        mock_resp = _jisho_response([{"english_definitions": ["to eat"]}])

        with (
            patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp),
            patch("anki_miner.services.definition_service.time.sleep") as mock_sleep,
        ):
            service._get_definition_jisho("食べる", apply_delay=True)

        mock_sleep.assert_called_once_with(test_config.jisho_delay)

    def test_delay_skipped_when_apply_delay_false(self, test_config):
        """Should NOT call time.sleep when apply_delay is False."""
        service = DefinitionService(test_config)
        mock_resp = _jisho_response([{"english_definitions": ["to eat"]}])

        with (
            patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp),
            patch("anki_miner.services.definition_service.time.sleep") as mock_sleep,
        ):
            service._get_definition_jisho("食べる", apply_delay=False)

        mock_sleep.assert_not_called()

    def test_limits_to_five_senses(self, test_config):
        """Should include at most 5 senses from the Jisho response."""
        service = DefinitionService(test_config)
        senses = [{"english_definitions": [f"meaning {i}"]} for i in range(1, 8)]
        mock_resp = _jisho_response(senses)

        with patch("anki_miner.services.definition_service.requests.get", return_value=mock_resp):
            result = service._get_definition_jisho("取る", apply_delay=False)

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
