"""Tests for services/card_backfiller.py (Card Backfill tool core)."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.services.card_backfiller import (
    BACKFILL_TAG,
    BackfillOptions,
    BackfillPlan,
    BackfillResult,
    FieldChange,
    NotePlan,
    _is_empty,
    _reading_from_furigana,
    apply_backfill,
    scan_backfill,
)
from anki_miner.services.morphology import SyntheticToken

# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

# A realistic markup-only pitch graph: SVG has no text nodes, so text-only
# emptiness would misread it as empty (the judge-panel round-1 catch).
_SVG_GRAPH = '<svg viewBox="0 0 100 40"><circle cx="5" cy="5" r="4"/><path d="M0 0"/></svg>'

# Realistic miner-markup proposal for the style-attach tests: the attach guard
# requires the yomitan-glossary + data-count fingerprint, and the per-field
# dict-CSS filter keys on the data-dictionary envelope title ("D" matches the
# stubbed collect_dictionary_css_entries below).
_GLOSS_HTML = '<div class="yomitan-glossary"><ol data-count="1"><li data-dictionary="D">gloss</li></ol></div>'


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
        "anki_miner.services.card_backfiller.collect_dictionary_css_entries",
        lambda config: [("D", "DICTCSS")],
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

    def test_glossary_proposal_gets_trailing_style_block(self, backfill_config):
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Glossary="", definition="")})
        defs = FakeDefinitionService(glossaries={"猫": _GLOSS_HTML})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}))
        value = _changes_by_key(plan, 1)["glossary"]
        # Trailing, never leading (leading is head-hoisted by DOMParser on JS
        # note types); dict CSS filtered to the dict present in the field.
        assert value.startswith(_GLOSS_HTML)
        assert value.endswith("</style>")
        assert value.count("<style>") == 1
        assert "DICTCSS" in value

    def test_block_attached_even_when_other_field_already_styled(self, backfill_config):
        # Per-field self-containment: the OLD cross-field gate ("other field
        # already holds the base sheet") is gone — a styled definition field
        # never styles the glossary field on field-isolating note types.
        anki = FakeAnkiService(
            {
                1: _note(
                    1, word="猫", ExpressionReading="ねこ", Glossary="", definition="<style>ol[data-count]{}</style>x"
                )
            }
        )
        defs = FakeDefinitionService(glossaries={"猫": _GLOSS_HTML})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}))
        value = _changes_by_key(plan, 1)["glossary"]
        assert value.startswith(_GLOSS_HTML)
        assert value.endswith("</style>")

    def test_overwrite_of_styled_carrier_reattaches_single_fresh_block(self, backfill_config):
        """Overwrite replaces a styled field: one fresh trailing block, no double sheet."""
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
        defs = FakeDefinitionService(glossaries={"猫": _GLOSS_HTML})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"glossary"}, overwrite=True))
        value = _changes_by_key(plan, 1)["glossary"]
        assert value.startswith(_GLOSS_HTML)
        assert value.count("<style>") == 1
        assert value.endswith("</style>")

    def test_definition_proposal_styled_when_glossary_unmapped(self, backfill_config):
        config = replace(backfill_config, anki_fields={**backfill_config.anki_fields, "glossary": ""})
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", definition="")})
        defs = FakeDefinitionService(defs={"猫": _GLOSS_HTML})
        plan = scan_backfill(anki, config, _services(defs=defs), _options({"definition"}))
        value = _changes_by_key(plan, 1)["definition"]
        assert value.startswith(_GLOSS_HTML)
        assert value.endswith("</style>")

    def test_both_proposed_fields_each_get_their_own_block(self, backfill_config):
        # Backfill bytes must equal fresh-mine bytes: with both miner fields
        # proposed, EACH carries its own trailing block.
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", Glossary="", definition="")})
        defs = FakeDefinitionService(defs={"猫": _GLOSS_HTML}, glossaries={"猫": _GLOSS_HTML})
        plan = scan_backfill(anki, backfill_config, _services(defs=defs), _options({"definition", "glossary"}))
        changes = _changes_by_key(plan, 1)
        for key in ("definition", "glossary"):
            assert changes[key].startswith(_GLOSS_HTML)
            assert changes[key].endswith("</style>")
            assert changes[key].count("<style>") == 1

    def test_markupless_definition_gets_no_block(self, backfill_config):
        # A plain-text proposal (no miner markup) is written verbatim — a block
        # on it would be field-LEADING after an empty body, and there is nothing
        # for the CSS to style anyway.
        config = replace(backfill_config, anki_fields={**backfill_config.anki_fields, "glossary": ""})
        anki = FakeAnkiService({1: _note(1, word="猫", ExpressionReading="ねこ", definition="")})
        defs = FakeDefinitionService(defs={"猫": "<p>cat</p>"})
        plan = scan_backfill(anki, config, _services(defs=defs), _options({"definition"}))
        assert _changes_by_key(plan, 1)["definition"] == "<p>cat</p>"

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


# ---------------------------------------------------------------------------
# apply_backfill
# ---------------------------------------------------------------------------


class RecordingAnkiService(FakeAnkiService):
    """FakeAnkiService that also records writes and tag calls."""

    def __init__(self, notes=None, fail_tags: bool = False):
        super().__init__(notes)
        self.updates: list[list[tuple[int, dict[str, str]]]] = []
        self.tag_calls: list[tuple[list[int], str]] = []
        self.fail_tags = fail_tags

    def update_notes_fields(self, updates):
        self.updates.append(list(updates))
        return [note_id for note_id, _fields in updates]

    def add_tags(self, note_ids, tags):
        if self.fail_tags:
            raise AnkiConnectionError("tags down")
        self.tag_calls.append((list(note_ids), tags))


def _plan(notes, overwrite=False):
    return BackfillPlan(
        options=_options({"frequency"}, overwrite=overwrite),
        notes=tuple(notes),
        scanned=len(notes),
        skipped_no_identity=0,
        unavailable_fields=(),
        sentinel_only_sorts=0,
        expression_field="",
        config_version=0,
    )


def _note_plan(note_id, changes):
    return NotePlan(note_id, f"word{note_id}", tuple(FieldChange(*c) for c in changes))


class TestApplyBackfill:
    def test_backfill_counts_only_confirmed_and_never_tags_failed(self):
        anki = RecordingAnkiService(
            {
                1: _note(1, word="word1", Frequency=""),
                2: _note(2, word="word2", Frequency=""),
            }
        )
        anki.update_notes_fields = lambda updates: [1]
        plan = _plan(
            [
                _note_plan(1, [("frequency", "Frequency", "", "a")]),
                _note_plan(2, [("frequency", "Frequency", "", "b")]),
            ]
        )

        result = apply_backfill(anki, plan)

        assert result.notes_updated == 1
        assert result.fields_filled == 1
        assert result.failed == 1
        assert result.tagged == 1
        assert anki.tag_calls == [([1], BACKFILL_TAG)]

    def test_stale_backfill_note_skipped(self, backfill_config):
        word_field = backfill_config.anki_fields["word"]
        anki = RecordingAnkiService(
            {
                1: _note(
                    1,
                    **{
                        word_field: "猫",
                        "ExpressionReading": "ねこ",
                        "Frequency": "",
                    },
                )
            }
        )
        freq = FakeFrequencyService({("猫", "ねこ"): [("JPDB", 42, None)]})
        plan = scan_backfill(anki, backfill_config, _services(freq=freq), _options({"frequency"}))
        anki.notes[1] = _note(
            1,
            **{
                word_field: "犬",
                "ExpressionReading": "いぬ",
                "Frequency": "",
            },
        )

        result = apply_backfill(anki, plan)

        assert anki.updates == []
        assert anki.tag_calls == []
        assert result.notes_updated == 0
        assert result.fields_filled == 0
        assert result.skipped_stale == 1

    def test_groups_multi_field_changes_per_note(self):
        anki = RecordingAnkiService({1: _note(1, Frequency="", FrequencySort="")})
        plan = _plan(
            [
                _note_plan(
                    1, [("frequency", "Frequency", "", "<ul>f</ul>"), ("frequency_sort", "FrequencySort", "", "42")]
                )
            ]
        )
        result = apply_backfill(anki, plan)
        assert anki.updates == [[(1, {"Frequency": "<ul>f</ul>", "FrequencySort": "42"})]]
        assert result.notes_updated == 1
        assert result.fields_filled == 2
        assert result.skipped_stale == 0

    def test_recheck_drops_no_longer_empty_in_fill_mode(self):
        anki = RecordingAnkiService({1: _note(1, Frequency="user filled this meanwhile")})
        plan = _plan([_note_plan(1, [("frequency", "Frequency", "", "<ul>f</ul>")])])
        result = apply_backfill(anki, plan)
        assert anki.updates == []
        assert result.skipped_stale == 1
        assert result.notes_updated == 0

    def test_overwrite_mode_writes_over_filled_target(self):
        anki = RecordingAnkiService({1: _note(1, Frequency="old")})
        plan = _plan([_note_plan(1, [("frequency", "Frequency", "old", "<ul>f</ul>")])], overwrite=True)
        result = apply_backfill(anki, plan)
        assert anki.updates == [[(1, {"Frequency": "<ul>f</ul>"})]]
        assert result.skipped_stale == 0

    def test_deleted_note_dropped_and_counted(self):
        anki = RecordingAnkiService({})  # notes_info -> {}
        plan = _plan([_note_plan(1, [("frequency", "Frequency", "", "<ul>f</ul>")])], overwrite=True)
        result = apply_backfill(anki, plan)
        assert anki.updates == []
        assert result.skipped_stale == 1

    def test_tags_attempted_ids_after_update(self):
        anki = RecordingAnkiService({1: _note(1, Frequency=""), 2: _note(2, Frequency="")})
        plan = _plan(
            [
                _note_plan(1, [("frequency", "Frequency", "", "a")]),
                _note_plan(2, [("frequency", "Frequency", "", "b")]),
            ]
        )
        result = apply_backfill(anki, plan)
        assert anki.tag_calls == [([1, 2], BACKFILL_TAG)]
        assert result.tagged == 2

    def test_custom_tag_passed_through(self):
        anki = RecordingAnkiService({1: _note(1, Frequency="")})
        plan = _plan([_note_plan(1, [("frequency", "Frequency", "", "a")])])
        apply_backfill(anki, plan, tag="custom::tag")
        assert anki.tag_calls == [([1], "custom::tag")]

    def test_tag_failure_logged_not_fatal(self):
        anki = RecordingAnkiService({1: _note(1, Frequency="")}, fail_tags=True)
        plan = _plan([_note_plan(1, [("frequency", "Frequency", "", "a")])])
        result = apply_backfill(anki, plan)
        assert result.notes_updated == 1
        assert result.tagged == 0

    def test_cancellation_between_chunks_keeps_committed(self, monkeypatch):
        monkeypatch.setattr("anki_miner.services.card_backfiller._CHUNK", 1)
        anki = RecordingAnkiService({1: _note(1, Frequency=""), 2: _note(2, Frequency="")})
        plan = _plan(
            [
                _note_plan(1, [("frequency", "Frequency", "", "a")]),
                _note_plan(2, [("frequency", "Frequency", "", "b")]),
            ]
        )
        calls = iter([False, True])
        result = apply_backfill(anki, plan, is_cancelled=lambda: next(calls))
        assert len(anki.updates) == 1
        assert result.notes_updated == 1

    def test_empty_plan_no_calls(self):
        anki = RecordingAnkiService()
        result = apply_backfill(anki, _plan([]))
        assert anki.updates == [] and anki.tag_calls == []
        assert result == BackfillResult(0, 0, 0, 0)

    def test_progress_reported(self):
        anki = RecordingAnkiService({1: _note(1, Frequency="")})
        plan = _plan([_note_plan(1, [("frequency", "Frequency", "", "a")])])
        seen = []
        apply_backfill(anki, plan, progress=lambda done, total: seen.append((done, total)))
        assert seen[-1] == (1, 1)
