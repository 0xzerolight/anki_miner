"""The Persian profile: registration, wiring and scoped defaults.

The shared contract tests (``test_language_contract.py``,
``test_hook_field_mapping_rows.py``) prove the SHAPE for every registered language;
this file proves Persian's CONTENT.
"""

from __future__ import annotations

from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages._spaced import create_spaced_parser
from anki_miner.languages.fa import FA_CARD_FIELDS, FA_EXTRA_CARD_FIELDS, FA_SMOKE_SENTENCE
from anki_miner.languages.fa.audio import FA_AUDIO
from anki_miner.languages.fa.availability import fa_missing_reason
from anki_miner.languages.fa.catalog import FA_CATALOG
from anki_miner.languages.fa.lookup import PersianLookupStrategy
from anki_miner.languages.fa.morphology import (
    FA_ALLOWED_POS,
    FA_EXCLUDED_SUBTYPES,
    FA_POS_LABELS,
    PersianMinedForm,
)
from anki_miner.languages.fa.render import FA_RENDER_HOOKS
from anki_miner.languages.fa.script import ZWNJ, PersianDictKeys, PersianScript, fa_fold, fa_normalize
from anki_miner.languages.fa.style import FA_CONTENT_STYLE
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS


def test_persian_is_registered_in_both_code_tuples():
    assert "fa" in AVAILABLE_LANGUAGES
    # The two tuples are pinned identical by test_config_language.py; a row added
    # to one and not the other is the standard miss.
    assert AVAILABLE_LANGUAGES == _LANGUAGE_CODES


def test_the_profile_names_persian_in_both_scripts():
    profile = get_profile("fa")
    assert profile.code == "fa"
    assert profile.english_name == "Persian"
    # U+0641 U+0627 U+0631 U+0633 U+06CC, the Farsi yeh rather than the Arabic one.
    assert [ord(c) for c in profile.display_name] == [0x0641, 0x0627, 0x0631, 0x0633, 0x06CC]


def test_the_profile_is_wired_to_the_persian_engine():
    profile = get_profile("fa")
    # The shared spaced factory, not a private copy: it fills exactly the five
    # seams fa needs and branches on no language code.
    assert profile.create_parser is create_spaced_parser
    assert isinstance(profile.mined_form, PersianMinedForm)
    assert isinstance(profile.lookup, PersianLookupStrategy)
    assert isinstance(profile.script, PersianScript)
    assert isinstance(profile.dict_keys, PersianDictKeys)
    assert profile.normalize is fa_normalize
    assert profile.dedup_fold is fa_fold
    assert profile.render_hooks is FA_RENDER_HOOKS
    assert profile.content_style is FA_CONTENT_STYLE
    assert profile.catalog is FA_CATALOG
    assert profile.audio is FA_AUDIO
    assert profile.unavailable_reason is fa_missing_reason
    # No respelling and no sentence annotator: the romanisation is a card field,
    # not a reading, and a Persian sentence gets no ruby.
    assert profile.reading is None
    assert profile.sentence_annotator is None


def test_the_pos_defaults_come_from_the_words_dat_tag_column():
    pos = get_profile("fa").pos_defaults
    assert pos.allowed_pos == FA_ALLOWED_POS
    assert pos.excluded_subtypes == FA_EXCLUDED_SUBTYPES
    assert pos.labels is FA_POS_LABELS


def test_media_and_text_entry_points_are_persian():
    profile = get_profile("fa")
    assert profile.asr_language == "fa"
    # 639-2/B "per" is what Matroska and ffmpeg write; "fas" is 639-2/T, "pes"
    # and "prs" are the Iranian and Dari variants a dual-audio rip may carry.
    assert {"per", "fas", "fa", "pes", "prs"} <= profile.audio_track_codes
    # Legacy Persian subtitles are cp1256. Measured: it carries the keheh (0x98),
    # the ZWNJ (0x9d) and pe/che/zhe/gaf, but NOT the Farsi yeh, so every such
    # file spells that with the Arabic yeh fa_normalize unifies.
    assert profile.import_encodings == ("utf-8-sig", "cp1256")
    assert profile.captions.primary == "fa"
    assert profile.captions.codes == ("fa",)
    assert profile.captions.orig_codes == ("fa-orig",)
    assert profile.captions.bare_fallback is True
    # "" means the code itself: the wty edition and the en.wiktionary section
    # are both "fa".
    assert profile.wiktionary_code == ""


def test_the_smoke_sentence_carries_its_zwnj_as_the_named_constant():
    """The bundle smoke line, "I go to school every day"."""
    assert get_profile("fa").smoke_sentence == FA_SMOKE_SENTENCE
    assert ZWNJ in FA_SMOKE_SENTENCE
    # Written in the source as an f-string over the ZWNJ constant, never as an
    # invisible character; the codepoints are the check.
    assert [ord(c) for c in FA_SMOKE_SENTENCE] == [
        0x0645, 0x0646, 0x0020,                          # man
        0x0647, 0x0631, 0x0020,                          # har
        0x0631, 0x0648, 0x0632, 0x0020,                  # ruz
        0x0628, 0x0647, 0x0020,                          # be
        0x0645, 0x062F, 0x0631, 0x0633, 0x0647, 0x0020,  # madrese
        0x0645, 0x06CC, 0x200C, 0x0631, 0x0648, 0x0645,  # mi-ravam
        0x002E,
    ]  # fmt: skip
    # The stored sentence is the normalised one (S3): normalising the smoke line
    # must not move it, or the card and the smoke leg disagree.
    assert fa_normalize(FA_SMOKE_SENTENCE) == FA_SMOKE_SENTENCE


def test_the_capabilities_gate_the_three_card_fields_and_the_rtl_layout():
    profile = get_profile("fa")
    assert profile.capabilities == frozenset(
        {"persian_romanization", "persian_register", "persian_stems", "rtl", "lemmatised_frequency"}
    )
    # Every extra field's gate is a capability this profile actually declares,
    # or the row can never be shown.
    assert {spec.capability for spec in profile.extra_card_fields} <= profile.capabilities
    assert profile.extra_card_fields == FA_EXTRA_CARD_FIELDS
    assert [spec.key for spec in FA_EXTRA_CARD_FIELDS] == [
        "reading_romanized",
        "colloquial_form",
        "present_stem",
    ]
    # Each hook key has its spec, and each spec its hook.
    hook_keys = {key for hook in profile.render_hooks for key in hook.field_names()}
    assert hook_keys == {spec.key for spec in FA_EXTRA_CARD_FIELDS}


def test_the_card_fields_leave_every_japanese_slot_unmapped():
    profile = get_profile("fa")
    assert profile.card_field_defaults == FA_CARD_FIELDS
    assert FA_CARD_FIELDS["word"] == "Expression"
    assert FA_CARD_FIELDS["sentence"] == "Sentence"
    assert FA_CARD_FIELDS["definition"] == "MainDefinition"
    # The romanisation hook owns the reading slot, so the ja reading fields stay
    # unmapped ("" = feature off, the empty-name skip).
    for key in ("expression_reading", "expression_furigana", "sentence_reading", "pitch_position"):
        assert FA_CARD_FIELDS[key] == ""
    for key in ("reading_romanized", "colloquial_form", "present_stem"):
        assert FA_CARD_FIELDS[key] == ""


def test_scoped_defaults_cover_every_scoped_field_with_persian_values():
    scoped = get_profile("fa").scoped_defaults
    # Built from blank_scoped_defaults(), never hand-written: a new scoped field
    # cannot silently miss a Persian default.
    assert set(scoped) == set(LANGUAGE_SCOPED_FIELDS)
    assert scoped["downloader_subtitle_langs"] == "fa"
    assert scoped["expression_audio_chain"] == FA_AUDIO.default_chain
    assert scoped["allowed_pos"] == FA_ALLOWED_POS
    assert scoped["excluded_subtypes"] == FA_EXCLUDED_SUBTYPES
    assert scoped["anki_fields"] == FA_CARD_FIELDS
    assert scoped["anki_fields"] is not FA_CARD_FIELDS  # a copy; the module table stays clean
    # "" is not a deck AnkiConnect accepts, and inheriting ja's would file
    # Persian cards into the Japanese deck.
    assert scoped["anki_deck_name"] == "Anki Miner"
    assert scoped["script_variant"] == ""
    assert scoped["reading_tone_color"] is False


def test_the_default_audio_chain_is_the_edge_voice():
    """gTTS has no Persian (tts_langs() lacks fa), so the seam's edgetts leg is the default."""
    chain = get_profile("fa").scoped_defaults["expression_audio_chain"]
    assert [entry.kind for entry in chain] == ["edgetts"]
    assert get_profile("fa").audio.gtts_lang == ""
    assert get_profile("fa").audio.edge_voice == "fa-IR-DilaraNeural"
