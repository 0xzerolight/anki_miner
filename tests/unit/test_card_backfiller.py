"""Tests for services/card_backfiller.py (Card Backfill tool core)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.services.card_backfiller import (
    BACKFILL_TAG,
    BackfillOptions,
    BackfillPlan,
    _is_empty,
    _reading_from_furigana,
    scan_backfill,
)
from anki_miner.services.morphology import SyntheticToken

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

# A realistic markup-only pitch graph: SVG has no text nodes, so text-only
# emptiness would misread it as empty (the judge-panel round-1 catch).
_SVG_GRAPH = '<svg viewBox="0 0 100 40"><circle cx="5" cy="5" r="4"/><path d="M0 0"/></svg>'


class FakeAnkiService:
    """Records find_notes queries; serves canned notesInfo dicts."""

    def __init__(self, notes: dict[int, dict] | None = None):
        self.notes = notes or {}
        self.queries: list[str] = []

    def find_notes(self, query: str) -> list[int]:
        self.queries.append(query)
        return sorted(self.notes)

    def notes_info(self, note_ids: list[int]) -> list[dict]:
        return [self.notes.get(nid, {}) for nid in note_ids]


class FakePitchService:
    def __init__(self, table: dict[tuple[str, str], str] | None = None, available: bool = True):
        self.table = table or {}
        self.available = available

    def is_available(self) -> bool:
        return self.available

    def lookup_detailed(self, word, reading="", pos=None, fmt="jp"):
        pattern = self.table.get((word, reading))
        return (pattern, "平板" if pattern else None)

    def lookup_entry(self, word, reading=""):
        if (word, reading) in self.table:
            return SimpleNamespace(nasal=(), devoice=())
        return None


class FakeFrequencyService:
    def __init__(self, table: dict[tuple[str, str], list] | None = None, available: bool = True):
        self.table = table or {}
        self.available = available

    def is_available(self) -> bool:
        return self.available

    def lookup_all(self, term, reading):
        return self.table.get((term, reading), [])


class FakeDefinitionService:
    def __init__(self, defs: dict[str, str] | None = None, glossaries: dict[str, str] | None = None):
        self.defs = defs or {}
        self.glossaries = glossaries or {}

    def get_definitions_batch(self, pairs, progress_callback=None, fallback_context=None):
        return [self.defs.get(word) for word, _reading in pairs]

    def get_glossaries_batch(self, pairs, progress_callback=None):
        return [self.glossaries.get(word) for word, _reading in pairs]


def _services(pitch=None, freq=None, defs=None):
    return SimpleNamespace(
        pitch_accent_service=pitch,
        frequency_service=freq,
        definition_service=defs or FakeDefinitionService(),
    )


def _note(note_id: int, **field_values: str) -> dict:
    return {
        "noteId": note_id,
        "fields": {name: {"value": value} for name, value in field_values.items()},
    }


@pytest.fixture
def backfill_config(test_config):
    """test_config with every backfillable field mapped (reading group included)."""
    return replace(
        test_config,
        anki_fields={
            **test_config.anki_fields,
            "expression_reading": "ExpressionReading",
            "expression_furigana": "ExpressionFurigana",
            "pitch_graph": "PitchGraph",
            "pitch_text": "PitchText",
            "frequency": "Frequency",
            "frequency_sort": "FrequencySort",
            "definition": "definition",
            "glossary": "Glossary",
        },
    )


@pytest.fixture(autouse=True)
def _stub_tagger_and_style(monkeypatch):
    """Deterministic tagger + style-block seams (no MeCab, no dict registry I/O)."""
    lemma_map: dict[str, str] = {"食べた": "食べる"}
    kana_map: dict[str, str] = {"猫": "ネコ", "食べる": "タベル"}

    def fake_tagger(text):
        return [
            SyntheticToken(
                text,
                "名詞",
                "*",
                lemma_map.get(text, text),
                kana_map.get(text, text),
            )
        ]

    monkeypatch.setattr(
        "anki_miner.services.card_backfiller.get_shared_tagger",
        lambda: fake_tagger,
    )
    monkeypatch.setattr(
        "anki_miner.services.card_backfiller.collect_dictionary_css",
        lambda config: "DICTCSS",
    )
    monkeypatch.setattr(
        "anki_miner.services.card_backfiller.build_card_style_block",
        lambda *, dict_css, card_html: "<style>ol[data-count]{}</style>",
    )


def _options(keys, deck=None, overwrite=False):
    return BackfillOptions(field_keys=frozenset(keys), deck=deck, overwrite=overwrite)


def _changes_by_key(plan: BackfillPlan, note_id: int) -> dict[str, str]:
    for note in plan.notes:
        if note.note_id == note_id:
            return {c.field_key: c.new_value for c in note.changes}
    return {}


# ---------------------------------------------------------------------------
# _is_empty
# ---------------------------------------------------------------------------


class TestIsEmpty:
    @pytest.mark.parametrize(
        "value",
        ["", "   ", "&nbsp;", "&nbsp; &nbsp;", "[sound:x.mp3]", "[anki:play:a:0]"],
    )
    def test_empty_values(self, value):
        assert _is_empty(value)

    @pytest.mark.parametrize(
        "value",
        [
            "text",
            "<div>text</div>",
            _SVG_GRAPH,  # markup-only field counts as FILLED
            "<br>",  # documented tradeoff: lone <br> counts as filled
            "<div></div>",
        ],
    )
    def test_filled_values(self, value):
        assert not _is_empty(value)

    def test_sound_ref_plus_markup_is_filled(self):
        assert not _is_empty("[sound:x.mp3]<svg></svg>")


# ---------------------------------------------------------------------------
# _reading_from_furigana
# ---------------------------------------------------------------------------


class TestReadingFromFurigana:
    def test_single_group(self):
        assert _reading_from_furigana("漢字[かんじ]") == "かんじ"

    def test_separator_space_dropped_interior_kana_kept(self):
        assert _reading_from_furigana("入[い]り 口[ぐち]") == "いりぐち"

    def test_rendaku_pair(self):
        assert _reading_from_furigana("取[と]り 引[ひ]き") == "とりひき"

    def test_plain_kana_passes_through(self):
        assert _reading_from_furigana("ねこ") == "ねこ"

    def test_katakana_bracket_content_folds_to_hiragana(self):
        assert _reading_from_furigana("馬鹿[バカ]") == "ばか"

    def test_html_wrapped(self):
        assert _reading_from_furigana("<div>漢字[かんじ]</div>") == "かんじ"

    def test_mixed_plain_kana_and_bracket(self):
        assert _reading_from_furigana("バカ 力[りょく]") == "ばかりょく"

    @pytest.mark.parametrize("value", ["", "漢字[", "漢字]", "[かんじ]漢字["])
    def test_malformed_returns_none(self, value):
        assert _reading_from_furigana(value) is None


# ---------------------------------------------------------------------------
# scan_backfill
# ---------------------------------------------------------------------------


class TestScanQuery:
    def test_note_type_scoped_query(self, backfill_config):
        anki = FakeAnkiService()
        scan_backfill(anki, backfill_config, _services(), _options({"frequency"}))
        assert anki.queries == ['note:"test\\_note\\_type"']

    def test_deck_scope_appended_and_escaped(self, backfill_config):
        anki = FakeAnkiService()
        scan_backfill(
            anki,
            backfill_config,
            _services(),
            _options({"frequency"}, deck='Core_2k "B" *'),
        )
        assert anki.queries == ['note:"test\\_note\\_type" deck:"Core\\_2k \\"B\\" \\*"']

    def test_word_field_unmapped_raises(self, backfill_config):
        config = replace(backfill_config, anki_fields={**backfill_config.anki_fields, "word": ""})
        with pytest.raises(ValueError, match="[Ee]xpression field"):
            scan_backfill(FakeAnkiService(), config, _services(), _options({"frequency"}))


class TestScanIdentity:
    def test_blank_word_field_skips_with_count(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="", Frequency="")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        assert plan.skipped_no_identity == 1
        assert plan.notes == ()

    def test_missing_word_field_entry_skips_not_raises(self, backfill_config):
        anki = FakeAnkiService({1: {"noteId": 1, "fields": {"Other": {"value": "x"}}}})
        plan = scan_backfill(anki, backfill_config, _services(), _options({"frequency"}))
        assert plan.skipped_no_identity == 1

    def test_value_key_missing_skips_not_raises(self, backfill_config):
        anki = FakeAnkiService({1: {"noteId": 1, "fields": {"word": {}}}})
        plan = scan_backfill(anki, backfill_config, _services(), _options({"frequency"}))
        assert plan.skipped_no_identity == 1

    def test_deleted_note_skipped(self, backfill_config):
        anki = FakeAnkiService({1: {}})
        plan = scan_backfill(anki, backfill_config, _services(), _options({"frequency"}))
        assert plan.scanned == 1
        assert plan.notes == ()


class TestScanFrequency:
    def test_fills_empty_frequency_fields(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", Frequency="", FrequencySort="")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency", "frequency_sort"}))
        changes = _changes_by_key(plan, 1)
        assert "JPDB" in changes["frequency"]
        assert changes["frequency_sort"] == "42"

    def test_reading_scoped_lookup_uses_stored_reading(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="辛い", ExpressionReading="つらい", Frequency="")})
        freq = FakeFrequencyService({("辛い", "つらい"): [("JPDB", 7, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        assert "JPDB" in _changes_by_key(plan, 1)["frequency"]

    def test_katakana_stored_reading_folds_to_hiragana(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="辛い", ExpressionReading="ツライ", Frequency="")})
        freq = FakeFrequencyService({("辛い", "つらい"): [("JPDB", 7, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        assert "JPDB" in _changes_by_key(plan, 1)["frequency"]

    def test_whole_result_lemma_fallback(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="食べた", ExpressionReading="たべた", Frequency="")})
        freq = FakeFrequencyService({("食べる", "たべた"): [("JPDB", 9, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        assert "JPDB" in _changes_by_key(plan, 1)["frequency"]

    def test_miss_writes_sort_sentinel_and_counts_it(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", Frequency="", FrequencySort="")})
        freq = FakeFrequencyService({})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency", "frequency_sort"}))
        changes = _changes_by_key(plan, 1)
        assert "frequency" not in changes  # miss never proposes the display field
        assert changes["frequency_sort"] == "9999999"
        assert plan.sentinel_only_sorts == 1

    def test_service_unavailable_reported_not_raised(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", Frequency="")})
        plan = scan_backfill(
            anki,
            backfill_config,
            _services(freq=FakeFrequencyService(available=False)),
            _options({"frequency"}),
        )
        assert "frequency" in plan.unavailable_fields
        assert plan.notes == ()

    def test_service_none_reported_not_raised(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", Frequency="")})
        plan = scan_backfill(anki, backfill_config, _services(freq=None), _options({"frequency"}))
        assert "frequency" in plan.unavailable_fields


class TestScanPitch:
    def test_fills_pitch_fields_from_lemma(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", PitchGraph="", PitchText="")})
        pitch = FakePitchService({("猫", "ねこ"): "0"})
        plan = scan_backfill(anki, backfill_config, _services(pitch=pitch), _options({"pitch_graph", "pitch_text"}))
        changes = _changes_by_key(plan, 1)
        assert "<svg" in changes["pitch_graph"]
        assert changes["pitch_text"]

    def test_lemma_miss_retries_mined_form(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="食べた", ExpressionReading="たべた", PitchGraph="")})
        pitch = FakePitchService({("食べた", "たべた"): "2"})
        plan = scan_backfill(anki, backfill_config, _services(pitch=pitch), _options({"pitch_graph", "pitch_text"}))
        assert "<svg" in _changes_by_key(plan, 1)["pitch_graph"]

    def test_existing_svg_graph_not_reproposed_without_overwrite(self, backfill_config):
        """The round-1 judge catch: markup-only graph must count as filled."""
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", PitchGraph=_SVG_GRAPH, PitchText="")})
        pitch = FakePitchService({("猫", "ねこ"): "0"})
        plan = scan_backfill(anki, backfill_config, _services(pitch=pitch), _options({"pitch_graph", "pitch_text"}))
        changes = _changes_by_key(plan, 1)
        assert "pitch_graph" not in changes
        assert "pitch_text" in changes

    def test_pitch_miss_proposes_nothing(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", PitchGraph="")})
        plan = scan_backfill(
            anki, backfill_config, _services(pitch=FakePitchService()), _options({"pitch_graph", "pitch_text"})
        )
        assert plan.notes == ()


class TestScanDefinitionGlossary:
    def test_fills_empty_definition(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", definition="")})
        defs = FakeDefinitionService(defs={"猫": "<p>cat</p>"})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"definition"}))
        assert "<p>cat</p>" in _changes_by_key(plan, 1)["definition"]

    def test_glossary_carrier_gets_style_block(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Glossary="", definition="")})
        defs = FakeDefinitionService(glossaries={"猫": "<div>gloss</div>"})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}))
        value = _changes_by_key(plan, 1)["glossary"]
        assert value.startswith("<style>ol[data-count]{}</style>")
        assert value.endswith("<div>gloss</div>")

    def test_no_style_block_when_other_field_already_styled(self, backfill_config):
        anki = FakeAnkiService(
            {
                1: _note(
                    1, word="猫", ExpressionReading="ねこ", Glossary="", definition="<style>ol[data-count]{}</style>x"
                )
            }
        )
        defs = FakeDefinitionService(glossaries={"猫": "<div>gloss</div>"})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}))
        assert _changes_by_key(plan, 1)["glossary"] == "<div>gloss</div>"

    def test_overwrite_of_styled_carrier_reattaches_single_fresh_block(self, backfill_config):
        """Overwrite replaces a styled carrier: fresh block re-attached, no double sheet."""
        anki = FakeAnkiService(
            {
                1: _note(
                    1,
                    word="猫",
                    ExpressionReading="ねこ",
                    Glossary="<style>ol[data-count]{}</style><div>old</div>",
                    definition="",
                )
            }
        )
        defs = FakeDefinitionService(glossaries={"猫": "<div>new</div>"})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}, overwrite=True))
        value = _changes_by_key(plan, 1)["glossary"]
        assert value == "<style>ol[data-count]{}</style><div>new</div>"

    def test_definition_is_carrier_when_glossary_unmapped(self, backfill_config):
        config = replace(backfill_config, anki_fields={**backfill_config.anki_fields, "glossary": ""})
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", definition="")})
        defs = FakeDefinitionService(defs={"猫": "<p>cat</p>"})
        plan = scan_backfill(anki, config, _services(defs=defs), _options({"definition"}))
        value = _changes_by_key(plan, 1)["definition"]
        assert value.startswith("<style>ol[data-count]{}</style>")

    def test_glossary_lemma_retry_on_miss(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="食べた", ExpressionReading="たべた", Glossary="", definition="x")})
        defs = FakeDefinitionService(glossaries={"食べる": "<div>eat</div>"})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}))
        assert "<div>eat</div>" in _changes_by_key(plan, 1)["glossary"]


class TestScanReadingFurigana:
    def test_cross_fills_reading_from_furigana(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="漢字", ExpressionReading="", ExpressionFurigana="漢字[かんじ]")})
        plan = scan_backfill(
            anki, backfill_config, _services(), _options({"expression_reading", "expression_furigana"})
        )
        changes = _changes_by_key(plan, 1)
        assert changes["expression_reading"] == "かんじ"
        assert "expression_furigana" not in changes

    def test_cross_fills_furigana_from_reading(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="漢字", ExpressionReading="かんじ", ExpressionFurigana="")})
        plan = scan_backfill(
            anki, backfill_config, _services(), _options({"expression_reading", "expression_furigana"})
        )
        changes = _changes_by_key(plan, 1)
        assert changes["expression_furigana"] == "漢字[かんじ]"
        assert "expression_reading" not in changes

    def test_tokenizer_reading_never_persisted(self, backfill_config):
        """Path-(c) synthesized readings drive lookups only; both fields empty -> no writes."""
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="", ExpressionFurigana="")})
        plan = scan_backfill(
            anki, backfill_config, _services(), _options({"expression_reading", "expression_furigana"})
        )
        assert plan.notes == ()

    def test_tokenizer_reading_still_drives_lookups(self, backfill_config):
        """Same empty-reading note: path-(c) reading (ねこ from fake kana) keys the freq lookup."""
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="", ExpressionFurigana="", Frequency="")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        assert "JPDB" in _changes_by_key(plan, 1)["frequency"]

    def test_values_html_escaped(self, backfill_config):
        anki = FakeAnkiService(
            {1: _note(1, word="A&B", ExpressionReading="", ExpressionFurigana="えー<b>あんど</b>びー")}
        )
        plan = scan_backfill(
            anki, backfill_config, _services(), _options({"expression_reading", "expression_furigana"})
        )
        changes = _changes_by_key(plan, 1)
        assert "<" not in changes.get("expression_reading", "")


class TestScanFillPolicy:
    def test_filled_target_skipped_without_overwrite(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Frequency="<ul><li>old</li></ul>")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        assert plan.notes == ()

    def test_overwrite_replaces_differing_value(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Frequency="<ul><li>old</li></ul>")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}, overwrite=True))
        assert "JPDB" in _changes_by_key(plan, 1)["frequency"]

    def test_overwrite_skips_identical_value(self, backfill_config):
        from anki_miner.services.frequency.render import render_frequency_html

        current = render_frequency_html([("JPDB", 42, None)])
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Frequency=current)})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}, overwrite=True))
        assert plan.notes == ()

    def test_unmapped_selected_key_ignored(self, backfill_config):
        config = replace(backfill_config, anki_fields={**backfill_config.anki_fields, "frequency_sort": ""})
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Frequency="")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, config, _services(freq=freq), _options({"frequency", "frequency_sort"}))
        changes = _changes_by_key(plan, 1)
        assert "frequency" in changes
        assert "frequency_sort" not in changes


class TestScanProgressCancel:
    def test_progress_reported(self, backfill_config):
        anki = FakeAnkiService({i: _note(i, word="猫", Frequency="") for i in range(1, 4)})
        seen = []
        scan_backfill(
            anki,
            backfill_config,
            _services(freq=FakeFrequencyService()),
            _options({"frequency"}),
            progress=lambda done, total: seen.append((done, total)),
        )
        assert seen and seen[-1] == (3, 3)

    def test_cancellation_stops_between_chunks(self, backfill_config, monkeypatch):
        monkeypatch.setattr("anki_miner.services.card_backfiller._CHUNK", 1)
        anki = FakeAnkiService({i: _note(i, word="猫", ExpressionReading="ねこ", Frequency="") for i in range(1, 4)})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        calls = iter([False, True, True, True])
        plan = scan_backfill(
            anki,
            backfill_config,
            _services(freq=freq),
            _options({"frequency"}),
            is_cancelled=lambda: next(calls),
        )
        assert plan.scanned < 3

    def test_old_display_is_stripped_and_capped(self, backfill_config):
        long_html = "<div>" + "x" * 500 + "</div>"
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Frequency=long_html)})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}, overwrite=True))
        change = plan.notes[0].changes[0]
        assert "<div>" not in change.old_display
        assert len(change.old_display) <= 203  # 200 + ellipsis

    def test_total_field_changes_and_tag_constant(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Frequency="", FrequencySort="")})
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency", "frequency_sort"}))
        assert plan.total_field_changes == 2
        assert BACKFILL_TAG == "anki-miner::backfill"
