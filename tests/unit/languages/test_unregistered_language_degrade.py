"""A whitelisted-but-unregistered language degrades to ja at every live site.

``AnkiMinerConfig.__post_init__`` accepts every ``_LANGUAGE_CODES`` entry, and a
build can carry a code it cannot resolve. ``config_language`` exists to make that
survivable: every seam reads the mining language through it and lands on Japanese
for a code with no builder. The two tagger sites read the RAW field instead, so a
hand-edited ``gui_config.json`` (or the Settings combo) raised ``ValueError`` out
of the parser constructor — killing every mining path, including the curation
dialog, which builds ``SubtitleParserService`` directly.

Stub-free on purpose: the point is a real whitelisted code against the real
registry, not a stub profile. Stage 3 registered ``ko``, so the fixture hides it
again rather than naming a code ``__post_init__`` would fold to ``ja``.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.gui.utils.service_factory import create_services
from anki_miner.services import card_backfiller, subtitle_parser
from anki_miner.services.subtitle_parser import SubtitleParserService
from anki_miner.services.tagger import get_shared_tagger
from tests.unit.languages.stub_registry import unregister_profile


@pytest.fixture(autouse=True)
def ko_is_unregistered(monkeypatch):
    """``ko`` is the unresolvable code every case below carries."""
    unregister_profile(monkeypatch, "ko")


class _FakeAnki:
    """Minimum surface ``_scan_backfill_impl`` touches before the note loop."""

    def __init__(self, note_type: str, fields: list[str]) -> None:
        self._note_type = note_type
        self._fields = fields

    def note_type_names(self) -> list[str]:
        return [self._note_type]

    def ordered_note_type_field_names(self, _note_type: str) -> list[str]:
        return list(self._fields)

    def find_notes(self, _query: str) -> list[int]:
        return []


def test_parser_construction_degrades_to_the_ja_tagger(test_config):
    parser = SubtitleParserService(replace(test_config, language="ko"))

    assert parser.tagger is get_shared_tagger()


def test_parser_construction_keeps_the_patchable_ja_seam(monkeypatch, test_config):
    """The degrade lands on the module-level name the pre-existing tests patch."""
    sentinel = object()
    monkeypatch.setattr(subtitle_parser, "get_shared_tagger", lambda: sentinel)

    assert SubtitleParserService(replace(test_config, language="ko")).tagger is sentinel


def test_backfill_scan_degrades_to_the_ja_tagger(monkeypatch, test_config):
    provider_calls: list[str] = []
    monkeypatch.setattr(card_backfiller, "get_tagger", lambda language: provider_calls.append(language))

    config = replace(test_config, language="ko")
    anki = _FakeAnki(config.anki_note_type, ["word", "expression_furigana"])
    card_backfiller.scan_backfill(
        anki,
        config,
        SimpleNamespace(pitch_accent_service=None, frequency_service=None, definition_service=None),
        card_backfiller.BackfillOptions(field_keys=frozenset({"expression_furigana"})),
    )

    assert provider_calls == []


def test_create_services_builds_a_ja_parser_for_an_unregistered_language(test_config):
    services = create_services(replace(test_config, language="ko"))

    assert type(services.subtitle_parser) is SubtitleParserService
    assert services.subtitle_parser.tagger is get_shared_tagger()
