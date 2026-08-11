"""Cross-entry-point tests for the Anki field-mapping contract."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.exceptions import SetupError
from anki_miner.services import anki_service as anki_service_module
from anki_miner.services import card_backfiller, validation_service
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.card_backfiller import BackfillOptions, scan_backfill
from anki_miner.services.validation_service import ValidationService


class _AvailableFrequency:
    def is_available(self) -> bool:
        return True


class _BackfillAnki:
    def __init__(self, note_type: str, ordered_fields: list[str]):
        self.note_type = note_type
        self.ordered_fields = ordered_fields
        self.scans = 0
        self.reads = 0
        self.writes = 0

    def note_type_names(self) -> list[str]:
        return [self.note_type]

    def note_type_field_names(self, note_type: str) -> set[str]:
        assert note_type == self.note_type
        return set(self.ordered_fields)

    def ordered_note_type_field_names(self, note_type: str) -> list[str]:
        assert note_type == self.note_type
        return list(self.ordered_fields)

    def find_notes(self, query: str) -> list[int]:
        self.scans += 1
        return []

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        self.reads += 1
        return []

    def update_notes_fields(self, updates: list[tuple[int, dict[str, str]]]) -> list[int]:
        self.writes += 1
        return []


def _config(test_config, **mapped_fields):
    fields = dict.fromkeys(test_config.anki_fields, "")
    fields.update(mapped_fields)
    return replace(test_config, anki_fields=fields)


def _services():
    return SimpleNamespace(
        pitch_accent_service=None,
        frequency_service=_AvailableFrequency(),
        definition_service=None,
    )


def _mining_preflight_error(config, ordered_fields, monkeypatch) -> str:
    service = AnkiService(config)
    monkeypatch.setattr(service, "note_type_names", lambda: [config.anki_note_type])
    monkeypatch.setattr(service, "ordered_note_type_field_names", lambda _note_type: ordered_fields)
    with pytest.raises(SetupError) as caught:
        service.verify_card_target()
    return str(caught.value)


def test_non_first_word_mapping_fails_validation_and_backfill_before_scan(test_config, monkeypatch):
    config = _config(test_config, word="Expression", frequency="Frequency")
    ordered_fields = ["Front", "Expression", "Frequency"]
    mining_error = _mining_preflight_error(config, ordered_fields, monkeypatch)

    monkeypatch.setattr(validation_service, "post_action", lambda *_args, **_kwargs: ordered_fields)
    assert ValidationService(config).check_field_names() == (False, mining_error)

    anki = _BackfillAnki(config.anki_note_type, ordered_fields)
    monkeypatch.setattr(card_backfiller, "get_shared_tagger", lambda: object())
    with pytest.raises(SetupError) as caught:
        scan_backfill(anki, config, _services(), BackfillOptions(frozenset({"frequency"})))

    assert str(caught.value) == mining_error
    assert (anki.scans, anki.reads, anki.writes) == (0, 0, 0)


@pytest.mark.parametrize(
    ("mapped_fields", "selected"),
    [
        ({"word": "Expression", "frequency": "Expression"}, {"frequency"}),
        (
            {
                "word": "Expression",
                "frequency": "Frequency",
                "frequency_sort": "Frequency",
            },
            {"frequency", "frequency_sort"},
        ),
    ],
)
def test_backfill_rejects_selected_target_collisions_before_scan(
    test_config,
    monkeypatch,
    mapped_fields,
    selected,
):
    config = _config(test_config, **mapped_fields)
    ordered_fields = ["Expression", "Frequency"]
    mining_error = _mining_preflight_error(config, ordered_fields, monkeypatch)
    anki = _BackfillAnki(config.anki_note_type, ordered_fields)

    with pytest.raises(SetupError) as caught:
        scan_backfill(anki, config, _services(), BackfillOptions(frozenset(selected)))

    assert str(caught.value) == mining_error
    assert (anki.scans, anki.reads, anki.writes) == (0, 0, 0)


def test_valid_ordered_mapping_passes_all_preflights(test_config, monkeypatch):
    config = _config(
        test_config,
        word="Expression",
        frequency="Frequency",
        frequency_sort="FrequencySort",
    )
    ordered_fields = ["Expression", "Frequency", "FrequencySort"]

    mining = AnkiService(config)
    monkeypatch.setattr(mining, "note_type_names", lambda: [config.anki_note_type])
    monkeypatch.setattr(mining, "ordered_note_type_field_names", lambda _note_type: ordered_fields)
    monkeypatch.setattr(
        anki_service_module,
        "post_action",
        lambda *_args, **_kwargs: [config.anki_deck_name],
    )
    mining.verify_card_target()

    monkeypatch.setattr(validation_service, "post_action", lambda *_args, **_kwargs: ordered_fields)
    assert ValidationService(config).check_field_names() == (True, "All configured fields exist")

    anki = _BackfillAnki(config.anki_note_type, ordered_fields)
    monkeypatch.setattr(card_backfiller, "get_shared_tagger", lambda: object())
    plan = scan_backfill(
        anki,
        config,
        _services(),
        BackfillOptions(frozenset({"frequency", "frequency_sort"})),
    )

    assert plan.notes == ()
    assert (anki.scans, anki.reads, anki.writes) == (1, 0, 0)
