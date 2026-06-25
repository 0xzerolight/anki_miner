"""Tests for JishoProvider."""

from unittest.mock import MagicMock, patch

import requests

from anki_miner.services.dictionary.providers.jisho_provider import JishoProvider


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
        with patch("anki_miner.services.dictionary.providers.jisho_provider.requests.get", return_value=mock_response):
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
        with patch("anki_miner.services.dictionary.providers.jisho_provider.requests.get", return_value=mock_response):
            result = provider.lookup("nonexistent")

        assert result is None

    def test_lookup_non_200(self):
        """Test lookup when API returns non-200 status."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        provider = JishoProvider(delay=0)
        with patch("anki_miner.services.dictionary.providers.jisho_provider.requests.get", return_value=mock_response):
            result = provider.lookup("食べる")

        assert result is None

    def test_lookup_timeout(self):
        """Test lookup handles timeout gracefully."""
        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
            side_effect=requests.exceptions.Timeout,
        ):
            result = provider.lookup("食べる")

        assert result is None

    def test_connection_error_returns_none(self):
        """Test that ConnectionError is handled gracefully."""
        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
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
                "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
                return_value=mock_response,
            ),
            patch("anki_miner.services.dictionary.providers.jisho_provider.time.sleep") as mock_sleep,
        ):
            provider.lookup("test")
            mock_sleep.assert_called_once_with(0.1)

    def test_response_missing_senses_key(self):
        """Test that response without 'senses' key returns empty result."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"japanese": [{"word": "食べる"}]}]}

        provider = JishoProvider(delay=0)
        with patch("anki_miner.services.dictionary.providers.jisho_provider.requests.get", return_value=mock_response):
            result = provider.lookup("食べる")

        assert result is None

    def test_lookup_returns_yomitan_envelope(self):
        """Jisho output must mirror the offline IndexedDictProvider markup so the
        card-style presets style Jisho-fallback cards identically: a `<i>` chip,
        a `gloss-list` with one `gloss-item`/`gloss-content` per sense, and no
        hand-written "1." prefix (the preset numbers senses via the list)."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "senses": [
                        {"english_definitions": ["to eat"]},
                        {"english_definitions": ["to live on", "subsist on"]},
                    ]
                }
            ]
        }

        with patch(
            "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
            return_value=mock_response,
        ):
            provider = JishoProvider(delay=0)
            result = provider.lookup("食べる")

        assert result is not None
        assert result.startswith('<div class="yomitan-glossary"><ol data-count="1">')
        assert '<li data-dictionary="Jisho API">' in result
        assert "<i>(Jisho API)</i>" in result
        assert '<ul class="gloss-list" data-count="2">' in result
        assert '<li class="gloss-item"><div class="gloss-content">to eat</div></li>' in result
        assert '<li class="gloss-item"><div class="gloss-content">to live on; subsist on</div></li>' in result
        # No hand-written ordinal — numbering is the preset's job now.
        assert "1. to eat" not in result
        assert result.endswith("</li></ol></div>")

    def test_lookup_single_sense_has_count_one(self):
        """A single sense yields data-count="1" so the preset drops the ordinal,
        matching the offline single-sense behavior."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": [{"senses": [{"english_definitions": ["to eat"]}]}]}

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
            return_value=mock_response,
        ):
            result = provider.lookup("食べる")

        assert result is not None
        assert '<ul class="gloss-list" data-count="1">' in result

    def test_lookup_escapes_html_in_definitions(self):
        """T-36: API-sourced definition strings must be HTML-escaped before
        interpolation. Anki's QtWebEngine renders the stored card HTML at review
        time, so an unescaped `<img onerror=...>` from Jisho is stored XSS that
        can reach AnkiConnect on localhost. Mirrors the offline path's leaf-text
        escaping in yomitan_renderer."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "senses": [
                        {"english_definitions": ['<img src=x onerror="alert(1)">']},
                    ]
                }
            ]
        }

        provider = JishoProvider(delay=0)
        with patch(
            "anki_miner.services.dictionary.providers.jisho_provider.requests.get",
            return_value=mock_response,
        ):
            result = provider.lookup("食べる")

        assert result is not None
        # The live `<img ...>` element must NOT appear: the `<` `>` `"` that make
        # it an executable tag must all be entity-escaped, rendering it inert
        # text. (The bare substring "onerror=" survives as escaped text, which is
        # harmless — it can only execute inside a real, unescaped tag.)
        assert "<img" not in result
        assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in result


def test_jisho_provider_is_online():
    provider = JishoProvider()
    assert provider.is_online is True
