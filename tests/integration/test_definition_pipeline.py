"""Integration tests for the definition lookup pipeline."""

from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.services.definition_service import DefinitionService

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

        result = service.get_definition("食べる")
        assert result is not None
        assert "to eat" in result

        result2 = service.get_definition("学生")
        assert result2 is not None
        assert "student" in result2

    def test_fallback_to_jisho_when_word_not_in_offline(self, config_with_jmdict):
        """When offline dict is loaded but word is missing, should return None
        (because use_offline_dict=True and _jmdict is loaded)."""
        service = DefinitionService(config_with_jmdict)
        service.load_offline_dictionary()

        # "飲む" is NOT in jmdict_test.xml
        result = service.get_definition("飲む")
        assert result is None

    def test_batch_mixed_results(self, config_with_jmdict):
        """Batch lookup with mix of found and not-found words."""
        service = DefinitionService(config_with_jmdict)
        service.load_offline_dictionary()

        results = service.get_definitions_batch(["食べる", "飲む", "走る"])

        assert len(results) == 3
        assert results[0] is not None  # 食べる found
        assert "to eat" in results[0]
        assert results[1] is None  # 飲む not in fixture
        assert results[2] is not None  # 走る found
        assert "to run" in results[2]
