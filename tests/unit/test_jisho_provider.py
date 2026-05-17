"""Tests for JishoProvider."""

from unittest.mock import MagicMock, patch

import requests

from anki_miner.services.providers.jisho_provider import JishoProvider


class TestJishoProvider:
    """Tests for JishoProvider."""

    def test_name_property(self):
        """Test the name property."""
        provider = JishoProvider(delay=0)
        assert provider.name == "Jisho API"

    def test_is_available_always_true(self):
        """Test is_available always returns True."""
        provider = JishoProvider(delay=0)
        assert provider.is_available() is True

    def test_load_always_true(self):
        """Test load always returns True."""
        provider = JishoProvider(delay=0)
        assert provider.load() is True

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
        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get",
            side_effect=requests.exceptions.Timeout,
        ):
            result = provider.lookup("食べる")

        assert result is None

    def test_connection_error_returns_none(self):
        """Test that ConnectionError is handled gracefully."""
        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.providers.jisho_provider.requests.get",
            side_effect=requests.exceptions.ConnectionError,
        ):
            result = provider.lookup("食べる")

        assert result is None

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


def test_jisho_provider_is_online():
    provider = JishoProvider()
    assert provider.is_online is True


def test_lookup_returns_yomitan_envelope(monkeypatch):
    fake = {
        "data": [
            {
                "senses": [
                    {"english_definitions": ["to eat"]},
                    {"english_definitions": ["to live on", "subsist on"]},
                ]
            }
        ]
    }

    class _R:
        status_code = 200

        def json(self):
            return fake

    monkeypatch.setattr(
        "anki_miner.services.providers.jisho_provider.requests.get",
        lambda *a, **k: _R(),
    )
    provider = JishoProvider(delay=0)

    result = provider.lookup("食べる")

    assert result is not None
    assert result.startswith('<div class="yomitan-glossary">')
    assert '<li data-dictionary="Jisho API">' in result
    assert "1. to eat" in result
    assert "2. to live on; subsist on" in result
    assert result.endswith("</div>")
