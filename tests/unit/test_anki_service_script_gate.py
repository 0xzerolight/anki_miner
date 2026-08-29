"""The two vocabulary-scan gates ask the profile, not a Japanese regex."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

from anki_miner.languages.registry import config_language
from anki_miner.services.anki_service import AnkiService


class _HangulScript:
    def filter_options(self):
        return ()

    def matches(self, option_id, form):
        return False

    def contains_target_script(self, text):
        return any("가" <= ch <= "힯" for ch in text)


def _notes(*values):
    return [{"fields": {"Expression": {"value": v, "order": 0}}} for v in values]


def test_config_language_reads_the_field(test_config, monkeypatch):
    """The field is honoured — for a code whose profile is registered. An
    unregistered one degrades to ja instead of raising (see
    test_config_language.py); the stub is what makes zh a real code here."""
    from tests.unit.languages.stub_registry import register_stub_profile

    assert config_language(test_config) == "ja"
    register_stub_profile(monkeypatch, "zh")
    assert config_language(replace(test_config, language="zh")) == "zh"


def test_config_language_falls_back_for_a_mock_config():
    """Four pre-existing test files pass a bare MagicMock as the config."""
    assert config_language(MagicMock()) == "ja"
    assert config_language(object()) == "ja"


def test_default_gate_comes_from_the_configured_language(test_config):
    service = AnkiService(test_config)
    assert service._is_target_script("猫") is True
    assert service._is_target_script("사과") is False
    assert service._is_target_script("cat") is False


def test_injected_script_replaces_the_profile_one(test_config):
    service = AnkiService(test_config, script=_HangulScript())
    assert service._is_target_script("사과") is True
    assert service._is_target_script("猫") is False


def test_scan_keeps_only_forms_the_script_accepts(test_config):
    service = AnkiService(test_config, script=_HangulScript())
    with patch("anki_miner.services.anki_service.post_action") as post:
        post.side_effect = [[1, 2, 3], _notes("사과", "猫", "cat")]
        found = service._collect_first_field_forms("deck:test")
    assert found == {"사과"}
