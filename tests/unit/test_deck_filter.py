"""Tests for the deck filter service (scan/apply, GUI-free)."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.services.deck_filter import (
    DECKFILTER_TAG,
    DeckFilterOptions,
    apply_deck_filter,
    inspect_deck,
    scan_deck_filter,
)
from anki_miner.services.word_filter import WordFilterService

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _note(note_id, model, fields, tags=()):
    """Build a notesInfo-shaped note dict from {name: value}."""
    return {
        "noteId": note_id,
        "modelName": model,
        "tags": list(tags),
        "fields": {name: {"value": value, "order": i} for i, (name, value) in enumerate(fields.items())},
    }


class FakeAnkiService:
    """Serves canned notes; records queries, deck creation, and raw adds."""

    def __init__(self, notes=None, vocab=None, add_results=None):
        self.notes = notes or {}
        self.vocab = vocab if vocab is not None else set()
        self.queries = []
        self.vocab_queries = []
        self.ensured_decks = []
        self.added_notes = []
        self.invalidated = 0
        self._add_results = add_results

    def find_notes(self, query):
        self.queries.append(query)
        return sorted(self.notes)

    def notes_info(self, note_ids):
        return [self.notes.get(nid, {}) for nid in note_ids]

    def get_vocabulary_excluding_deck(self, deck):
        self.vocab_queries.append(deck)
        return set(self.vocab)

    def ensure_deck(self, deck_name):
        self.ensured_decks.append(deck_name)

    def add_notes_raw(self, notes):
        self.added_notes.append(list(notes))
        return self._add_results.pop(0) if self._add_results is not None else list(range(1000, 1000 + len(notes)))

    def invalidate_existing_vocabulary_cache(self):
        self.invalidated += 1


class FakeKnownWordDB:
    def __init__(self, user=None, known=None, available=True):
        self.user = user or set()
        self.known = known or set()
        self.available = available
        self.sync_calls = 0

    def is_available(self):
        return self.available

    def get_words_by_source(self, source):
        assert source == "user"
        return set(self.user)

    def get_known_words(self):
        return set(self.known)

    def sync_with_anki(self, *args, **kwargs):  # pragma: no cover - must not run
        self.sync_calls += 1
        raise AssertionError("scan must never sync_with_anki")


class FakeFrequencyService:
    """Numeric frequency table keyed on (term, reading-or-None)."""

    def __init__(self, table=None, available=True, numeric=True):
        self.table = table or {}
        self.available = available
        self.numeric = numeric

    def is_available(self):
        return self.available

    def has_numeric_source(self):
        return self.numeric

    def lookup_all_many(self, pairs):
        return [list(self.table.get(pair, [])) for pair in pairs]


class FakeWordListService:
    def __init__(self, blacklist=None, whitelist=None, available=True):
        self.blacklist = blacklist or set()
        self.whitelist = whitelist or set()
        self.available = available

    def is_available(self):
        return self.available

    def is_blacklisted(self, form):
        return form in self.blacklist

    def is_whitelisted(self, form):
        return form in self.whitelist


class FakeWordsetService:
    def __init__(self, excluded=None, available=True):
        self.excluded = excluded or set()
        self.available = available

    def is_available(self):
        return self.available

    def is_excluded(self, form):
        return form in self.excluded


def _services(config, **overrides):
    bundle = SimpleNamespace(
        word_filter=WordFilterService(config),
        frequency_service=None,
        word_list_service=None,
        wordset_service=None,
        known_word_db=None,
        tagger=None,
    )
    for name, value in overrides.items():
        setattr(bundle, name, value)
    return bundle


def _options(**overrides):
    defaults = {"source_deck": "Premade", "target_deck": "Premade (Filtered)"}
    defaults.update(overrides)
    return DeckFilterOptions(**defaults)


def _drops(plan):
    return dict(plan.drops)


# ---------------------------------------------------------------------------
# inspect_deck
# ---------------------------------------------------------------------------


class TestInspectDeck:
    def test_models_ordered_by_note_count_fields_unioned(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "一", "Meaning": "one"}),
                2: _note(2, "Core", {"Expression": "二", "Meaning": "two"}),
                3: _note(3, "Extra", {"Front": "三", "Back": "three"}),
            }
        )
        inspection = inspect_deck(anki, "Premade")

        assert inspection.note_count == 3
        assert inspection.models == ("Core", "Extra")
        assert inspection.field_names == ("Expression", "Meaning", "Front", "Back")
        assert inspection.first_field_by_model == {"Core": "Expression", "Extra": "Front"}
        assert anki.queries == ['deck:"Premade"']

    def test_deck_name_escaped_in_query(self, test_config):
        anki = FakeAnkiService()
        inspect_deck(anki, 'Core_2k "v3"')
        assert anki.queries == ['deck:"Core\\_2k \\"v3\\""']

    def test_empty_deck(self, test_config):
        inspection = inspect_deck(FakeAnkiService(), "Premade")
        assert inspection.note_count == 0
        assert inspection.models == ()
        assert inspection.field_names == ()


# ---------------------------------------------------------------------------
# scan_deck_filter
# ---------------------------------------------------------------------------


class TestScanBasics:
    def test_keeps_unknown_japanese_notes(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "頷く", "Meaning": "nod"}, tags=("core",)),
                2: _note(2, "Core", {"Expression": "食べる", "Meaning": "eat"}),
            }
        )
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options())

        assert plan.scanned == 2
        assert [kept.expression for kept in plan.kept] == ["頷く", "食べる"]
        assert plan.kept[0].note_id == 1
        assert plan.kept[0].model_name == "Core"
        assert plan.kept[0].tags == ("core",)
        assert plan.kept[0].fields == {"Expression": "頷く", "Meaning": "nod"}
        assert plan.drops == ()
        assert plan.config_version == test_config.config_version

    def test_first_field_is_default_expression(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Front": "走る", "Back": "run"})})
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options())
        assert [kept.expression for kept in plan.kept] == ["走る"]

    def test_picked_field_missing_falls_back_to_first_field(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "走る", "Meaning": "run"}),
                2: _note(2, "Extra", {"Front": "歩く", "Back": "walk"}),
            }
        )
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options(expression_field="Expression"))
        assert sorted(kept.expression for kept in plan.kept) == ["歩く", "走る"]

    def test_html_wrapped_expression_normalized(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "<b>頷く</b>", "Meaning": ""})})
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options())
        assert [kept.expression for kept in plan.kept] == ["頷く"]

    def test_reading_field_used_when_picked(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く", "Reading": "ウナズク"})})
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options(reading_field="Reading"))
        assert plan.kept[0].reading == "うなずく"


class TestScanDrops:
    def test_empty_and_non_japanese_and_duplicates(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "", "Meaning": "x"}),
                2: _note(2, "Core", {"Expression": "hello", "Meaning": "x"}),
                3: _note(3, "Core", {"Expression": "頷く", "Meaning": "x"}),
                4: _note(4, "Core", {"Expression": "頷く", "Meaning": "dup"}),
            }
        )
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options())

        assert [kept.expression for kept in plan.kept] == ["頷く"]
        assert _drops(plan) == {"no_expression": 1, "not_japanese": 1, "duplicate_in_source": 1}
        assert plan.kept[0].note_id == 3  # first occurrence wins

    def test_known_words_dropped_via_deck_excluding_vocab(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "頷く"}),
                2: _note(2, "Core", {"Expression": "食べる"}),
            },
            vocab={"食べる"},
        )
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options())

        assert [kept.expression for kept in plan.kept] == ["頷く"]
        assert _drops(plan) == {"known": 1}
        assert anki.vocab_queries == ["Premade"]

    def test_user_ignore_list_always_applied(self, test_config):
        db = FakeKnownWordDB(user={"頷く"})
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く"})})
        config = replace(test_config, use_known_words_db=False)
        plan = scan_deck_filter(anki, config, _services(config, known_word_db=db), _options())

        assert plan.kept == ()
        assert _drops(plan) == {"known": 1}
        assert db.sync_calls == 0

    def test_db_cache_applied_only_when_enabled(self, test_config):
        db = FakeKnownWordDB(known={"頷く"})
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く"})})

        config_off = replace(test_config, use_known_words_db=False)
        plan_off = scan_deck_filter(anki, config_off, _services(config_off, known_word_db=db), _options())
        assert [kept.expression for kept in plan_off.kept] == ["頷く"]

        config_on = replace(test_config, use_known_words_db=True)
        plan_on = scan_deck_filter(anki, config_on, _services(config_on, known_word_db=db), _options())
        assert plan_on.kept == ()
        assert db.sync_calls == 0

    def test_blacklist_and_wordset_and_script(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "頷く"}),
                2: _note(2, "Core", {"Expression": "田中"}),
                3: _note(3, "Core", {"Expression": "コーヒー"}),
                4: _note(4, "Core", {"Expression": "食べる"}),
            }
        )
        config = replace(test_config, exclude_katakana_only_words=True)
        services = _services(
            config,
            word_list_service=FakeWordListService(blacklist={"食べる"}),
            wordset_service=FakeWordsetService(excluded={"田中"}),
        )
        plan = scan_deck_filter(anki, config, services, _options())

        assert [kept.expression for kept in plan.kept] == ["頷く"]
        assert _drops(plan) == {"blacklist": 1, "script_type": 1, "name_wordset": 1}


class TestScanFrequency:
    def test_band_drops_and_unranked_counted_separately(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "頷く", "Reading": "うなずく"}),
                2: _note(2, "Core", {"Expression": "食べる", "Reading": "たべる"}),
                3: _note(3, "Core", {"Expression": "彷徨う", "Reading": "さまよう"}),
            }
        )
        config = replace(test_config, max_frequency_rank=5000, frequency_keep_unranked=False)
        freq = FakeFrequencyService(
            table={
                ("頷く", "うなずく"): [("jpdb", 3000, None)],
                ("食べる", "たべる"): [("jpdb", 90000, None)],
            }
        )
        plan = scan_deck_filter(
            anki,
            config,
            _services(config, frequency_service=freq),
            _options(reading_field="Reading"),
        )

        assert [kept.expression for kept in plan.kept] == ["頷く"]
        assert plan.kept[0].frequency_rank == 3000
        assert _drops(plan) == {"frequency_band": 1, "unranked": 1}

    def test_band_inert_without_numeric_source(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "彷徨う"})})
        config = replace(test_config, max_frequency_rank=5000, frequency_keep_unranked=False)
        freq = FakeFrequencyService(numeric=False)
        plan = scan_deck_filter(anki, config, _services(config, frequency_service=freq), _options())

        assert [kept.expression for kept in plan.kept] == ["彷徨う"]
        assert plan.drops == ()

    def test_whitelist_bypasses_band_and_marks_forced(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "彷徨う"}),
                2: _note(2, "Core", {"Expression": "頷く"}),
            }
        )
        config = replace(
            test_config,
            use_whitelist=True,
            max_frequency_rank=5000,
            frequency_keep_unranked=False,
        )
        services = _services(
            config,
            word_list_service=FakeWordListService(whitelist={"彷徨う"}),
            frequency_service=FakeFrequencyService(),
        )
        plan = scan_deck_filter(anki, config, services, _options())

        assert [(kept.expression, kept.forced) for kept in plan.kept] == [("彷徨う", True)]
        assert plan.forced_count == 1
        assert _drops(plan) == {"unranked": 1}


class TestScanCancel:
    def test_cancel_before_first_chunk_examines_nothing(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く"})})
        plan = scan_deck_filter(anki, test_config, _services(test_config), _options(), is_cancelled=lambda: True)
        assert plan.scanned == 0
        assert plan.kept == ()


# ---------------------------------------------------------------------------
# apply_deck_filter
# ---------------------------------------------------------------------------


def _plan_with(anki, config, notes):
    return scan_deck_filter(anki, config, _services(config), _options())


class TestApply:
    def test_copies_kept_notes_with_tag_and_options(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く", "Meaning": "nod"}, tags=("core",))})
        plan = _plan_with(anki, test_config, anki.notes)
        result = apply_deck_filter(anki, plan)

        assert anki.ensured_decks == ["Premade (Filtered)"]
        assert result.created == 1
        assert result.not_created == 0
        (batch,) = anki.added_notes
        (note,) = batch
        assert note == {
            "deckName": "Premade (Filtered)",
            "modelName": "Core",
            "fields": {"Expression": "頷く", "Meaning": "nod"},
            "tags": ["core", DECKFILTER_TAG],
            "options": {"allowDuplicate": True, "duplicateScope": "deck"},
        }
        assert anki.invalidated == 1

    def test_null_slots_counted_not_created(self, test_config):
        anki = FakeAnkiService(
            notes={
                1: _note(1, "Core", {"Expression": "頷く"}),
                2: _note(2, "Core", {"Expression": "食べる"}),
            },
            add_results=[[101, None]],
        )
        plan = _plan_with(anki, test_config, anki.notes)
        result = apply_deck_filter(anki, plan)

        assert result.created == 1
        assert result.not_created == 1

    def test_cancel_before_first_chunk_copies_nothing(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く"})})
        plan = _plan_with(anki, test_config, anki.notes)
        result = apply_deck_filter(anki, plan, is_cancelled=lambda: True)

        assert result.created == 0
        assert anki.added_notes == []
        assert anki.ensured_decks == ["Premade (Filtered)"]
        assert anki.invalidated == 1

    def test_invalidates_cache_even_when_add_raises(self, test_config):
        anki = FakeAnkiService(notes={1: _note(1, "Core", {"Expression": "頷く"})})
        plan = _plan_with(anki, test_config, anki.notes)
        anki.add_notes_raw = lambda notes: (_ for _ in ()).throw(RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            apply_deck_filter(anki, plan)

        assert anki.invalidated == 1

    def test_empty_plan_creates_deck_only(self, test_config):
        anki = FakeAnkiService()
        plan = _plan_with(anki, test_config, {})
        result = apply_deck_filter(anki, plan)

        assert result.created == 0
        assert anki.added_notes == []
        assert anki.ensured_decks == ["Premade (Filtered)"]
