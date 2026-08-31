"""Unit tests for anki_note_builder.build_note optional-field wiring.

Covers the opt-in pitch graph/overline card fields (6.3) and the duplicate
options wire format. All optional fields default-off via unmapped anki_fields
keys, so the default wire stays byte-identical.
"""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import CardPayload, MediaData, TokenizedWord
from anki_miner.services.anki_note_builder import build_note, configured_target_field_names


def _word(**overrides) -> TokenizedWord:
    """A verb TokenizedWord with target offsets carried."""
    defaults = {
        "surface": "帰っ",
        "lemma": "帰る",
        "reading": "カエッ",
        "sentence": "家に帰った。",
        "start_time": 1.0,
        "end_time": 3.0,
        "duration": 2.0,
        "orth_base": "帰る",
        "expression_furigana": "帰[かえ]る",
        "expression_reading": "かえる",
        "sentence_furigana": "",
        "sentence_reading": "",
        "pos": "動詞",
        # 家に = 2 chars, target 帰っ starts at index 2; full inflected 帰った
        # spans [2, 5) via highlight_end.
        "surface_start": 2,
        "surface_end": 4,
        "highlight_end": 5,
    }
    defaults.update(overrides)
    return TokenizedWord(**defaults)


def _payload(word: TokenizedWord, extra_fields=None) -> CardPayload:
    return CardPayload(
        word=word,
        media=MediaData(),
        definition="to return home",
        extra_fields=extra_fields,
    )


def _config(**field_overrides) -> AnkiMinerConfig:
    """Default config with the given anki_fields keys mapped to real names."""
    fields = dict(AnkiMinerConfig().anki_fields)
    fields.update(field_overrides)
    return AnkiMinerConfig(anki_fields=fields)


def test_configured_target_field_names_uses_nonempty_mappings_and_active_marker():
    fields = dict.fromkeys(AnkiMinerConfig().anki_fields, "")
    fields.update(word="Expression", source="MiningSource")
    config = AnkiMinerConfig(anki_fields=fields, card_type="click")

    assert configured_target_field_names(config) == {"Expression", "MiningSource", "IsClickCard"}


class TestPitchGraphTextFields:
    """Raw-HTML insertion of the 6.3 pitch graph / overline fields."""

    _GRAPH = '<svg class="pronunciation-graph"><path d="M25 75"/></svg>'
    _TEXT = '<span class="pronunciation-text"><span>は</span></span>'

    def test_unmapped_omits_both_fields(self):
        note = build_note(
            _payload(_word(), extra_fields={"pitch_graph": self._GRAPH, "pitch_text": self._TEXT}),
            AnkiMinerConfig(),
            set(),
        ).note
        assert "PitchGraph" not in note["fields"]
        assert "PitchText" not in note["fields"]

    def test_mapped_inserts_raw_html_not_escaped(self):
        config = _config(pitch_graph="PitchGraph", pitch_text="PitchText")
        fields = build_note(
            _payload(_word(), extra_fields={"pitch_graph": self._GRAPH, "pitch_text": self._TEXT}),
            config,
            set(),
        ).note["fields"]
        # Verbatim: the <svg>/<span> markup is NOT html.escape()d.
        assert fields["PitchGraph"] == self._GRAPH
        assert fields["PitchText"] == self._TEXT
        assert "&lt;" not in fields["PitchGraph"]

    def test_mapped_but_no_data_omits_field(self):
        # extra_fields carries no pitch keys (episode_processor gates on the
        # render output being non-empty) → mapped field left untouched, not blanked.
        config = _config(pitch_graph="PitchGraph", pitch_text="PitchText")
        fields = build_note(_payload(_word()), config, set()).note["fields"]
        assert "PitchGraph" not in fields
        assert "PitchText" not in fields

    def test_default_config_wire_unchanged(self):
        # A legacy config whose anki_fields never contained the pitch_graph/
        # pitch_text keys produces the identical note dict as the current default.
        word = _word()
        default_note = build_note(_payload(word), AnkiMinerConfig(), set()).note
        legacy_fields = {
            k: v for k, v in AnkiMinerConfig().anki_fields.items() if k not in ("pitch_graph", "pitch_text")
        }
        legacy_note = build_note(
            _payload(word),
            AnkiMinerConfig(anki_fields=legacy_fields),
            set(),
        ).note
        assert default_note == legacy_note


class TestDuplicateOptions:
    def test_default_config_omits_options_key(self):
        # WIRE-FORMAT REGRESSION (omit-at-default): default config emits NO
        # options key on the note dict, so AnkiConnect applies its implicit
        # default (whole collection, same note type) — byte-identical to pre-7.3.
        note = build_note(_payload(_word()), AnkiMinerConfig(), set()).note
        assert "options" not in note

    def test_deck_builder_object_unchanged(self):
        # allow_duplicate_cards takes precedence and keeps the pre-7.3 hardcoded
        # object byte-for-byte.
        config = AnkiMinerConfig(allow_duplicate_cards=True)
        note = build_note(_payload(_word()), config, set()).note
        assert note["options"] == {"allowDuplicate": True, "duplicateScope": "deck"}


class TestProfileDeclaredExtraKeys:
    """Language N+1's card fields, passed per call instead of added centrally.

    ``OPTIONAL_FIELD_KEYS``/``_RAW_HTML_FIELD_KEYS`` are frozen; a new
    language's keys arrive through these two keyword arguments, which
    ``AnkiService`` fills from the active profile's ``extra_card_fields``.
    """

    _STUB = "stub_extra"
    _STUB_HTML = "stub_extra_html"

    def test_legacy_positional_call_matches_explicit_empty_extras(self):
        # scripts/engine_golden_contract_v2.py calls build_note(payload, config,
        # stored_files) positionally and may not be edited: both arguments
        # default, and defaulting them changes nothing.
        word = _word()
        positional = build_note(_payload(word), AnkiMinerConfig(), set()).note
        explicit = build_note(
            _payload(word),
            AnkiMinerConfig(),
            set(),
            extra_optional_keys=frozenset(),
            extra_raw_html_keys=frozenset(),
        ).note
        assert positional == explicit
        assert list(positional["fields"]) == list(explicit["fields"])

    def test_extra_optional_key_is_gated_like_an_optional_field(self):
        config = _config(**{self._STUB: "Stub"})
        payload = _payload(_word(), extra_fields={self._STUB: "<b>x</b>"})

        without = build_note(payload, config, set()).note["fields"]
        assert "Stub" not in without

        with_extra = build_note(
            payload,
            config,
            set(),
            extra_optional_keys=frozenset({self._STUB}),
        ).note["fields"]
        # Optional pass semantics: escaped, and only when mapped.
        assert with_extra["Stub"] == "&lt;b&gt;x&lt;/b&gt;"
        unmapped = build_note(
            payload,
            _config(**{self._STUB: ""}),
            set(),
            extra_optional_keys=frozenset({self._STUB}),
        ).note["fields"]
        assert "Stub" not in unmapped

    def test_extra_optional_key_skips_when_empty(self):
        config = _config(**{self._STUB: "Stub"})
        fields = build_note(
            _payload(_word(), extra_fields={self._STUB: ""}),
            config,
            set(),
            extra_optional_keys=frozenset({self._STUB}),
        ).note["fields"]
        assert "Stub" not in fields

    def test_extra_raw_html_key_is_emitted_verbatim(self):
        config = _config(**{self._STUB_HTML: "StubHtml"})
        fields = build_note(
            _payload(_word(), extra_fields={self._STUB_HTML: '<span class="tone">x</span>'}),
            config,
            set(),
            extra_optional_keys=frozenset({self._STUB_HTML}),
            extra_raw_html_keys=frozenset({self._STUB_HTML}),
        ).note["fields"]
        assert fields["StubHtml"] == '<span class="tone">x</span>'
        assert "&lt;" not in fields["StubHtml"]

    def test_extra_raw_html_key_skips_when_empty_or_absent(self):
        config = _config(**{self._STUB_HTML: "StubHtml"})
        kwargs = {
            "extra_optional_keys": frozenset({self._STUB_HTML}),
            "extra_raw_html_keys": frozenset({self._STUB_HTML}),
        }
        empty = build_note(
            _payload(_word(), extra_fields={self._STUB_HTML: ""}),
            config,
            set(),
            **kwargs,
        ).note["fields"]
        assert "StubHtml" not in empty
        absent = build_note(_payload(_word()), config, set(), **kwargs).note["fields"]
        assert "StubHtml" not in absent

    def test_extra_raw_html_key_unmapped_writes_nothing(self):
        fields = build_note(
            _payload(_word(), extra_fields={self._STUB_HTML: "<b>x</b>"}),
            AnkiMinerConfig(),
            set(),
            extra_optional_keys=frozenset({self._STUB_HTML}),
            extra_raw_html_keys=frozenset({self._STUB_HTML}),
        ).note["fields"]
        assert all(value != "<b>x</b>" for value in fields.values())
