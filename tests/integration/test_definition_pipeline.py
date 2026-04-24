"""Integration tests for the definition lookup pipeline."""

from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.providers.jisho_provider import JishoProvider

JMDICT_FIXTURE = Path(__file__).parent.parent / "fixtures" / "jmdict_test.xml"


@pytest.fixture
def config_with_jmdict(tmp_path):
    """Config pointing to the real test JMdict fixture."""
    return AnkiMinerConfig(
        jmdict_path=JMDICT_FIXTURE,
        use_offline_dict=True,
        media_temp_folder=tmp_path / "media",
    )


class TestDefinitionPipeline:
    """Integration tests using real DefinitionService with jmdict_test.xml."""

    def test_load_and_lookup(self, config_with_jmdict):
        """Should load real JMdict XML and look up a known word."""
        service = DefinitionService(config_with_jmdict)
        assert service.load_offline_dictionary() is True

        with patch.object(JishoProvider, "lookup") as mock_jisho:
            result = service.get_definition("食べる")
        assert result is not None
        assert "to eat" in result
        mock_jisho.assert_not_called()

        with patch.object(JishoProvider, "lookup") as mock_jisho:
            result2 = service.get_definition("学生")
        assert result2 is not None
        assert "student" in result2
        mock_jisho.assert_not_called()

    def test_fallback_to_jisho_when_word_not_in_offline(self, config_with_jmdict):
        """When offline dict is loaded but misses, the service must try Jisho."""
        service = DefinitionService(config_with_jmdict)
        service.load_offline_dictionary()

        # "飲む" is NOT in jmdict_test.xml; Jisho should be queried as fallback.
        with patch.object(JishoProvider, "lookup", return_value="1. to drink") as mock_jisho:
            result = service.get_definition("飲む")

        mock_jisho.assert_called_once_with("飲む")
        assert result == "1. to drink"

    def test_batch_mixed_results(self, config_with_jmdict):
        """Batch lookup with mix of JMdict hits and Jisho fallbacks."""
        service = DefinitionService(config_with_jmdict)
        service.load_offline_dictionary()

        # 飲む is missing from the fixture, so its value comes from the Jisho fallback.
        with patch.object(JishoProvider, "lookup", return_value="1. to drink"):
            results = service.get_definitions_batch(["食べる", "飲む", "走る"])

        assert len(results) == 3
        assert results[0] is not None  # 食べる found in JMdict
        assert "to eat" in results[0]
        assert results[1] == "1. to drink"  # 飲む via Jisho fallback
        assert results[2] is not None  # 走る found in JMdict
        assert "to run" in results[2]
