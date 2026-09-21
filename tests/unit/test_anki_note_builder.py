"""Unit tests for anki_note_builder.build_note optional-field wiring.

Covers the opt-in pitch graph/overline card fields (6.3) and the duplicate
options wire format. All optional fields default-off via unmapped anki_fields
keys, so the default wire stays byte-identical.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models import CardPayload, MediaData, TokenizedWord
from anki_miner.services.anki_note_builder import (
    _strip_for_dedup,
    build_note,
    configured_target_field_names,
    missing_note_type_message,
    no_note_type_message,
)


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


class TestMissingNoteTypeMessage:
    """A configured name that Anki does not have, and a name nobody set.

    zh ships ``anki_note_type=""`` on purpose, so the first run reported a note
    type called '' as absent from the collection -- a typo the user never made
    instead of the step they have not done yet.
    """

    def test_a_configured_name_reads_exactly_as_it_did(self):
        assert (
            missing_note_type_message("Lapis", ["Basic"])
            == "Note type 'Lapis' is not in Anki — pick one in Settings → Cards & Anki."
        )

    def test_an_unset_name_says_so_and_points_at_the_same_place(self):
        message = missing_note_type_message("", ["Basic"])

        assert message == no_note_type_message()
        assert "''" not in message
        assert "Settings → Cards & Anki" in message

    def test_an_unset_name_is_not_logged_as_a_missing_note_type(self, caplog):
        """The log is read as a diagnosis, and there is nothing missing here."""
        with caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_note_builder"):
            missing_note_type_message("", ["Basic"])

        assert "Anki note type missing" not in caplog.text

    def test_a_configured_name_still_logs_the_collection_s_note_types(self, caplog):
        with caplog.at_level(logging.WARNING, logger="anki_miner.services.anki_note_builder"):
            missing_note_type_message("Lapis", ["Basic"])

        assert "Anki note type missing: wanted=Lapis available=['Basic']" in caplog.text


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


def test_sentence_translation_is_written_escaped_when_mapped(test_config, make_tokenized_word):
    from dataclasses import replace

    config = replace(
        test_config, anki_fields={**test_config.anki_fields, "sentence_translation": "SentenceTranslation"}
    )
    word = make_tokenized_word()
    word.sentence_translation = "I <3 fish & chips"
    item = CardPayload(word=word, media=MediaData(), definition="def")

    built = build_note(item, config, stored_files=set())

    assert built.note["fields"]["SentenceTranslation"] == "I &lt;3 fish &amp; chips"


def test_sentence_translation_is_skipped_when_unmapped(test_config, make_tokenized_word):
    word = make_tokenized_word()
    word.sentence_translation = "Hello."
    item = CardPayload(word=word, media=MediaData(), definition="def")

    built = build_note(item, test_config, stored_files=set())

    assert "Hello." not in built.note["fields"].values()


class TestRtlContentWrapper:
    """S21 card side: an rtl language's word and sentence carry dir + lang; nothing else moves.

    The content is Japanese on purpose: the wrapper is text-agnostic, and RTL
    literals stay out of .py files.
    """

    @staticmethod
    def _fields(config, word, **kwargs) -> dict:
        item = CardPayload(word=word, media=MediaData(), definition="def")
        return build_note(item, config, stored_files=set(), **kwargs).note["fields"]

    def test_word_and_sentence_are_wrapped(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(), content_direction="rtl", content_lang="fa")
        assert fields["word"] == '<div dir="rtl" lang="fa">食べる</div>'
        assert fields["sentence"] == '<div dir="rtl" lang="fa">日本語を食べる。</div>'

    def test_the_bolded_sentence_is_wrapped_whole(self, test_config, make_tokenized_word):
        word = make_tokenized_word()
        word.sentence_bolded = "日本語を<b>食べる</b>。"
        config = replace(test_config, bold_target_in_sentence=True)
        fields = self._fields(config, word, content_direction="rtl", content_lang="ar")
        assert fields["sentence"] == '<div dir="rtl" lang="ar">日本語を<b>食べる</b>。</div>'

    def test_every_other_field_is_untouched(self, test_config, make_tokenized_word):
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_reading": "Reading", "sentence_translation": "Tr"},
        )
        word = make_tokenized_word(expression_reading="たべる")
        word.sentence_translation = "I eat."
        legacy = self._fields(config, word)
        rtl = self._fields(config, word, content_direction="rtl", content_lang="he")
        assert set(rtl) == set(legacy)
        assert {k: v for k, v in rtl.items() if k not in ("word", "sentence")} == {
            k: v for k, v in legacy.items() if k not in ("word", "sentence")
        }

    def test_an_empty_sentence_stays_empty(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(sentence=""), content_direction="rtl", content_lang="he")
        assert fields["sentence"] == ""

    def test_lang_is_optional_and_escaped(self, test_config, make_tokenized_word):
        word = make_tokenized_word()
        assert self._fields(test_config, word, content_direction="rtl")["word"] == '<div dir="rtl">食べる</div>'
        assert self._fields(test_config, word, content_direction="rtl", content_lang='x"y')["word"] == (
            '<div dir="rtl" lang="x&quot;y">食べる</div>'
        )

    def test_the_dedup_key_is_the_bare_word(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(), content_direction="rtl", content_lang="fa")
        assert _strip_for_dedup(fields["word"]) == "食べる"

    @pytest.mark.parametrize("lang", ["", "ja", "ko"])
    def test_ltr_builds_the_legacy_note(self, test_config, make_tokenized_word, lang):
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition="def")
        assert build_note(item, test_config, set(), content_direction="ltr", content_lang=lang) == build_note(
            item, test_config, set()
        )


class TestCardLangWrapper:
    """A Han language's sentence declares its language so the WebView picks its glyph forms.

    The resolver is a stand-in here; the zh and yue ones are pinned by their own
    suites. The content stays Japanese so these assertions read as wrapper
    mechanics rather than as Chinese examples.
    """

    @staticmethod
    def _fields(config, word, **kwargs) -> dict:
        item = CardPayload(word=word, media=MediaData(), definition="def")
        return build_note(item, config, stored_files=set(), **kwargs).note["fields"]

    def test_the_sentence_is_wrapped_and_the_word_is_not(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(), card_lang=lambda text, config: "zh-Hans")
        assert fields["sentence"] == '<span lang="zh-Hans">日本語を食べる。</span>'
        assert fields["word"] == "食べる"

    def test_the_resolver_sees_the_sentence_and_the_config(self, test_config, make_tokenized_word):
        """The tag describes the text that gets wrapped, so the sentence is the argument."""
        seen: list[tuple[str, object]] = []

        def resolver(text: str, config) -> str:
            seen.append((text, config))
            return "zh-Hant"

        fields = self._fields(test_config, make_tokenized_word(), card_lang=resolver)
        assert seen == [("日本語を食べる。", test_config)]
        assert fields["sentence"] == '<span lang="zh-Hant">日本語を食べる。</span>'

    def test_the_bold_highlight_survives_inside_the_span(self, test_config, make_tokenized_word):
        word = make_tokenized_word()
        word.sentence_bolded = "日本語を<b>食べる</b>。"
        config = replace(test_config, bold_target_in_sentence=True)
        fields = self._fields(config, word, card_lang=lambda t, c: "zh-Hans")
        assert fields["sentence"] == '<span lang="zh-Hans">日本語を<b>食べる</b>。</span>'

    def test_the_resolver_sees_the_sentence_before_escaping_and_highlighting(self, test_config, make_tokenized_word):
        """Markup would read as neither script; the resolver gets the plain source text."""
        seen: list[str] = []

        def resolver(text: str, _config) -> str:
            seen.append(text)
            return "zh-Hans"

        word = make_tokenized_word(sentence='"日本語"を食べる。')
        word.sentence_bolded = '"日本語"を<b>食べる</b>。'
        config = replace(test_config, bold_target_in_sentence=True)
        fields = self._fields(config, word, card_lang=resolver)
        assert seen == ['"日本語"を食べる。']
        assert fields["sentence"] == '<span lang="zh-Hans">"日本語"を<b>食べる</b>。</span>'

    def test_an_empty_tag_leaves_the_sentence_alone(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(), card_lang=lambda t, c: "")
        assert fields["sentence"] == "日本語を食べる。"

    def test_an_empty_sentence_stays_empty(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(sentence=""), card_lang=lambda t, c: "zh-Hans")
        assert fields["sentence"] == ""

    def test_the_tag_is_escaped(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(), card_lang=lambda t, c: 'x"y')
        assert fields["sentence"] == '<span lang="x&quot;y">日本語を食べる。</span>'

    def test_the_dedup_keys_survive_the_wrapper(self, test_config, make_tokenized_word):
        fields = self._fields(test_config, make_tokenized_word(), card_lang=lambda t, c: "zh-Hans")
        assert _strip_for_dedup(fields["word"]) == "食べる"
        assert _strip_for_dedup(fields["sentence"]) == "日本語を食べる。"

    def test_every_other_field_is_untouched(self, test_config, make_tokenized_word):
        config = replace(
            test_config,
            anki_fields={**test_config.anki_fields, "expression_reading": "Reading", "sentence_translation": "Tr"},
        )
        word = make_tokenized_word(expression_reading="たべる")
        word.sentence_translation = "I eat."
        legacy = self._fields(config, word)
        tagged = self._fields(config, word, card_lang=lambda t, c: "zh-Hans")
        assert set(tagged) == set(legacy)
        assert {k: v for k, v in tagged.items() if k != "sentence"} == {
            k: v for k, v in legacy.items() if k != "sentence"
        }

    def test_an_rtl_language_is_never_double_wrapped(self, test_config, make_tokenized_word):
        word = make_tokenized_word()
        rtl = self._fields(test_config, word, content_direction="rtl", content_lang="he")
        both = self._fields(
            test_config, word, content_direction="rtl", content_lang="he", card_lang=lambda t, c: "zh-Hans"
        )
        assert both == rtl

    @pytest.mark.parametrize("code", ["ja", "ko"])
    def test_a_profile_declaring_no_resolver_writes_no_lang(self, test_config, make_tokenized_word, code):
        from anki_miner.languages.registry import get_profile

        assert get_profile(code).content_style.card_lang is None
        item = CardPayload(word=make_tokenized_word(), media=MediaData(), definition="def")
        built = build_note(item, test_config, set(), card_lang=get_profile(code).content_style.card_lang)
        assert "lang=" not in "".join(built.note["fields"].values())
        assert built == build_note(item, test_config, set())
