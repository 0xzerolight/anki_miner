"""Unit tests for anki_note_builder.build_note optional-field wiring.

Covers the opt-in pitch graph/overline card fields (6.3) and the duplicate
options wire format. All optional fields default-off via unmapped anki_fields
keys, so the default wire stays byte-identical.
"""

from __future__ import annotations

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import CardPayload, MediaData, TokenizedWord
from anki_miner.services.anki_note_builder import build_note


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
