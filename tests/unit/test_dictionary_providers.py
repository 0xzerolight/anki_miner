"""Tests for JMdictProvider and JishoProvider."""

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services.providers.jisho_provider import JishoProvider
from anki_miner.services.providers.jmdict_provider import JMdictProvider

# Minimal JMdict XML for testing
MINI_JMDICT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
<entry>
<k_ele><keb>食べる</keb></k_ele>
<r_ele><reb>たべる</reb></r_ele>
<sense><gloss>to eat</gloss></sense>
<sense><gloss>to live on</gloss><gloss>to survive</gloss></sense>
</entry>
<entry>
<k_ele><keb>飲む</keb></k_ele>
<r_ele><reb>のむ</reb></r_ele>
<sense><gloss>to drink</gloss></sense>
</entry>
</JMdict>"""


class TestJMdictProvider:
    """Tests for JMdictProvider."""

    def test_load_valid_xml(self, tmp_path):
        """Test loading a valid JMdict XML file."""
        xml_file = tmp_path / "JMdict_e"
        xml_file.write_text(MINI_JMDICT_XML, encoding="utf-8")

        provider = JMdictProvider(xml_file)
        assert provider.load() is True
        assert provider.is_available() is True

    def test_load_missing_file_raises_setup_error(self, tmp_path):
        """Test that SetupError is raised when file is missing."""
        provider = JMdictProvider(tmp_path / "nonexistent")
        with pytest.raises(SetupError):
            provider.load()

    def test_lookup_found(self, tmp_path):
        """Test lookup returns definition for a known word."""
        xml_file = tmp_path / "JMdict_e"
        xml_file.write_text(MINI_JMDICT_XML, encoding="utf-8")

        provider = JMdictProvider(xml_file)
        provider.load()

        result = provider.lookup("食べる")
        assert result is not None
        assert "to eat" in result

    def test_lookup_by_reading(self, tmp_path):
        """Test lookup works with kana reading as well."""
        xml_file = tmp_path / "JMdict_e"
        xml_file.write_text(MINI_JMDICT_XML, encoding="utf-8")

        provider = JMdictProvider(xml_file)
        provider.load()

        result = provider.lookup("たべる")
        assert result is not None
        assert "to eat" in result

    def test_lookup_not_found(self, tmp_path):
        """Test lookup returns None for an unknown word."""
        xml_file = tmp_path / "JMdict_e"
        xml_file.write_text(MINI_JMDICT_XML, encoding="utf-8")

        provider = JMdictProvider(xml_file)
        provider.load()

        assert provider.lookup("存在しない") is None

    def test_is_available_before_load(self, tmp_path):
        """Test is_available returns False before loading."""
        provider = JMdictProvider(tmp_path / "JMdict_e")
        assert provider.is_available() is False

    def test_limits_to_five_definitions(self, tmp_path):
        """Test that output is limited to 5 definitions."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<JMdict>
<entry>
<k_ele><keb>多義語</keb></k_ele>
<r_ele><reb>たぎご</reb></r_ele>
<sense><gloss>def1</gloss></sense>
<sense><gloss>def2</gloss></sense>
<sense><gloss>def3</gloss></sense>
<sense><gloss>def4</gloss></sense>
<sense><gloss>def5</gloss></sense>
<sense><gloss>def6</gloss></sense>
<sense><gloss>def7</gloss></sense>
</entry>
</JMdict>"""
        xml_file = tmp_path / "JMdict_e"
        xml_file.write_text(xml, encoding="utf-8")

        provider = JMdictProvider(xml_file)
        provider.load()

        result = provider.lookup("多義語")
        assert result is not None
        assert "5." in result
        assert "6." not in result

    def test_name_property(self, tmp_path):
        """Test the name property."""
        provider = JMdictProvider(tmp_path / "JMdict_e")
        assert provider.name == "JMdict Offline"

    def test_load_malformed_xml_raises_setup_error(self, tmp_path):
        """Test that malformed XML raises SetupError."""
        xml_file = tmp_path / "JMdict_e"
        xml_file.write_text("<<< not valid xml >>>", encoding="utf-8")

        provider = JMdictProvider(xml_file)
        with pytest.raises(SetupError, match="Error parsing JMdict XML"):
            provider.load()

    def test_lookup_when_not_loaded(self, tmp_path):
        """Test that lookup returns None when dictionary is not loaded."""
        provider = JMdictProvider(tmp_path / "JMdict_e")
        assert provider.lookup("食べる") is None


class TestJishoProvider:
    """Tests for JishoProvider."""

    def test_lookup_success(self):
        """Test successful lookup via mocked API."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "senses": [
                        {"english_definitions": ["to eat", "to consume"]},
                        {"english_definitions": ["to live on"]},
                    ]
                }
            ]
        }

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get", return_value=mock_response
        ):
            result = provider.lookup("食べる")

        assert result is not None
        assert "to eat" in result
        assert "to consume" in result

    def test_lookup_empty_results(self):
        """Test lookup when API returns no results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get", return_value=mock_response
        ):
            result = provider.lookup("nonexistent")

        assert result is None

    def test_lookup_non_200(self):
        """Test lookup when API returns non-200 status."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get", return_value=mock_response
        ):
            result = provider.lookup("食べる")

        assert result is None

    def test_lookup_timeout(self):
        """Test lookup handles timeout gracefully."""
        import requests

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get",
            side_effect=requests.exceptions.Timeout,
        ):
            result = provider.lookup("食べる")

        assert result is None

    def test_is_available_always_true(self):
        """Test is_available always returns True."""
        provider = JishoProvider(delay=0)
        assert provider.is_available() is True

    def test_load_always_true(self):
        """Test load always returns True."""
        provider = JishoProvider(delay=0)
        assert provider.load() is True

    def test_name_property(self):
        """Test the name property."""
        provider = JishoProvider(delay=0)
        assert provider.name == "Jisho API"

    def test_rate_limiting(self):
        """Test that rate limiting delay is applied."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": []}

        provider = JishoProvider(delay=0.1)
        with (
            patch(
                "anki_miner.services.providers.jisho_provider.requests.get",
                return_value=mock_response,
            ),
            patch("anki_miner.services.providers.jisho_provider.time.sleep") as mock_sleep,
        ):
            provider.lookup("test")
            mock_sleep.assert_called_once_with(0.1)

    def test_connection_error_returns_none(self):
        """Test that ConnectionError is handled gracefully."""
        import requests

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get",
            side_effect=requests.exceptions.ConnectionError,
        ):
            result = provider.lookup("食べる")

        assert result is None

    def test_response_missing_senses_key(self):
        """Test that response without 'senses' key returns empty result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"japanese": [{"word": "食べる"}]}]}

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get", return_value=mock_response
        ):
            result = provider.lookup("食べる")

        assert result is None
