"""Value pins for the ja profile — every field is the existing JA behaviour."""

from __future__ import annotations

import unicodedata
from types import SimpleNamespace

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.gui.utils.phrase_wrap import phrase_wrap_ja
from anki_miner.languages import ja as ja_pkg
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import LANGUAGE_SCOPED_FIELDS
from anki_miner.models.word import select_mined_form
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary import storage
from anki_miner.services.reading import sentence_splitter
from anki_miner.services.resource_catalog import RECOMMENDED_DEFAULT_SET
from anki_miner.services.sentence_tts_fetcher import PAPAGO_SPEAKER_JA
from anki_miner.utils.audio_track_detector import JAPANESE_LANGUAGE_CODES
from anki_miner.utils.ja_normalize import normalize_for_tokenization
from anki_miner.utils.text_utils import (
    generate_furigana_from_tokens,
    generate_reading_from_tokens,
    is_hiragana_only,
    is_katakana_only,
    is_mixed_kana_only,
)


@pytest.fixture
def profile():
    return get_profile("ja")


def _token(surface: str, kana: str = "", **feature):
    return SimpleNamespace(surface=surface, feature=SimpleNamespace(kana=kana, **feature))


def test_identity(profile):
    assert profile.code == "ja"
    assert profile.display_name == "日本語"
    assert profile.asr_language == "ja"
    assert profile.capabilities == frozenset({"pitch", "furigana", "kana_filters", "name_wordsets", "deinflection"})


def test_media_and_import_values(profile):
    assert profile.audio_track_codes == JAPANESE_LANGUAGE_CODES
    assert profile.import_encodings == ("utf-8-sig", "cp932", "euc_jp")
    assert profile.captions.primary == "ja"


def test_audio_defaults_keep_todays_cache_stems(profile):
    assert profile.audio.gtts_lang == "ja"
    assert profile.audio.cache_stem_prefix == "googletts"
    assert profile.audio.sentence_cache_stem_prefix == "sentencetts"
    assert profile.audio.custom_fetcher_language == "ja"
    # Explicit, not None: the factory's `or PAPAGO_SPEAKER_JA` coercion was
    # dropped when zh registered (a missing speaker would have read Chinese
    # sentences in the Japanese voice), so ja now names its own.
    assert profile.audio.papago_speaker == PAPAGO_SPEAKER_JA
    assert profile.audio.candidates is None
    assert profile.audio.default_chain == AnkiMinerConfig().expression_audio_chain


def test_card_fields_and_hooks(profile):
    assert profile.card_field_defaults == dict(AnkiMinerConfig().anki_fields)
    assert profile.render_hooks == ()


def test_content_style_routes_the_japanese_font_and_wrapper(profile):
    assert profile.content_style.font_role == "japanese"
    assert profile.content_style.families == ()
    assert profile.content_style.wrap("行きましょう") == phrase_wrap_ja("行きましょう")


def test_sentence_rules_mirror_the_splitter_constants(profile):
    assert profile.sentence_rules.terminators == sentence_splitter._HARD_TERMINATORS
    assert profile.sentence_rules.ellipses == sentence_splitter._ELLIPSIS
    assert profile.sentence_rules.openers == sentence_splitter._OPENERS
    assert profile.sentence_rules.closers == sentence_splitter._CLOSERS
    assert profile.sentence_rules.space_aware is False


def test_normalize_and_catalog_are_the_existing_objects(profile):
    assert profile.normalize is normalize_for_tokenization
    assert profile.catalog == RECOMMENDED_DEFAULT_SET


def test_pos_defaults_come_from_the_config(profile):
    base = AnkiMinerConfig()
    assert profile.pos_defaults.allowed_pos == tuple(base.allowed_pos)
    assert profile.pos_defaults.excluded_subtypes == tuple(base.excluded_subtypes)


def test_scoped_defaults_are_the_config_defaults(profile):
    base = AnkiMinerConfig()
    assert set(profile.scoped_defaults) == set(LANGUAGE_SCOPED_FIELDS)
    for name in LANGUAGE_SCOPED_FIELDS:
        assert profile.scoped_defaults[name] == getattr(base, name), name


def test_create_parser_delegates_to_the_subtitle_parser_service(monkeypatch):
    seen = {}

    class _Fake:
        def __init__(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs

    monkeypatch.setattr(ja_pkg, "SubtitleParserService", _Fake)
    config = AnkiMinerConfig()
    parser = get_profile("ja").create_parser(config, term_lookup=None)
    assert isinstance(parser, _Fake)
    assert seen == {"args": (config,), "kwargs": {"term_lookup": None}}


def test_mined_form_delegates_to_select_mined_form(profile):
    cases = [
        ("動詞", "食べる", "食べる", "食べた", ""),
        ("名詞", "手", "手", "手ぇ", "テー"),
        ("代名詞", "ワタシ", "私", "ワタシ", "ワタシ"),
    ]
    for pos, orth_base, lemma, surface, pron in cases:
        assert profile.mined_form.mined_form(pos, orth_base, lemma, surface, pron) == select_mined_form(
            pos, orth_base, lemma, surface, pron
        )


def test_lookup_delegates_to_the_definition_service_fallbacks(profile):
    assert profile.lookup.candidates("食べた", "", None) == DefinitionService._fallback_candidates("食べた", "", None)
    assert profile.lookup.candidates("殺る", "遣る", "五段-ラ行") == DefinitionService._fallback_candidates(
        "殺る", "遣る", "五段-ラ行"
    )


def test_script_filter_options_name_the_kana_config_fields(profile):
    options = {opt.option_id: opt for opt in profile.script.filter_options()}
    assert set(options) == {"hiragana_only", "katakana_only", "mixed_kana_only"}
    assert options["hiragana_only"].config_field == "exclude_hiragana_only_words"
    assert options["katakana_only"].config_field == "exclude_katakana_only_words"
    # Mixed kana has no field of its own: it drops only when BOTH boxes are on.
    assert options["mixed_kana_only"].config_field == ""


@pytest.mark.parametrize("form", ["する", "コーヒー", "サボる", "食べる", "すごーい", ""])
def test_script_matches_delegate_to_the_kana_predicates(profile, form):
    assert profile.script.matches("hiragana_only", form) is is_hiragana_only(form)
    assert profile.script.matches("katakana_only", form) is is_katakana_only(form)
    assert profile.script.matches("mixed_kana_only", form) is is_mixed_kana_only(form)
    assert profile.script.matches("no_such_option", form) is False


def test_contains_target_script_is_the_anki_service_regex(profile):
    from anki_miner.services.anki_service import _JAPANESE_RE

    for text in ["食べる", "ひらがな", "カタカナ", "㐰", "hello", "", "123", "Hello 世界"]:
        assert profile.script.contains_target_script(text) is (_JAPANESE_RE.search(text) is not None)
    # Every block boundary of the copied character class, ±1 on each edge, so a
    # drifted range in either copy fails here rather than in a card diff.
    edges = (0x3040, 0x309F, 0x30A0, 0x30FF, 0x4E00, 0x9FFF, 0x3400, 0x4DBF)
    for cp in {c for edge in edges for c in (edge - 1, edge, edge + 1)}:
        char = chr(cp)
        assert profile.script.contains_target_script(char) is (_JAPANESE_RE.search(char) is not None), hex(cp)


def test_dict_keys_delegate_to_storage(profile):
    assert profile.dict_keys.fold_term("ｶﾞ") == unicodedata.normalize("NFC", "ｶﾞ")
    assert profile.dict_keys.fold_reading("タベル") == storage._fold_reading("タベル")
    assert profile.dict_keys.fold_reading(None) is None
    rows = [("レイド", "raid"), ("零度", "zero degrees")]
    assert profile.dict_keys.homograph_keep_mask("レイド", rows) == storage._homograph_keep_mask("レイド", rows)
    assert profile.dict_keys.homograph_keep_mask("ゆう", [("言う", "to say")], "言う") == storage._homograph_keep_mask(
        "ゆう", [("言う", "to say")], "言う"
    )


def test_reading_support_extracts_the_token_kana(profile):
    assert profile.reading.word_reading(_token("王国", "オウコク")) == "オウコク"
    assert profile.reading.word_reading(_token("？")) == "？"


def test_sentence_annotator_pairs_furigana_with_the_plain_reading(profile):
    tokens = [_token("王国", "オウコク"), _token("です", "デス"), _token("。")]
    text = "王国です。"
    assert profile.sentence_annotator.annotate_sentence(text, tokens) == (
        generate_furigana_from_tokens(tokens, text=text),
        generate_reading_from_tokens(tokens),
    )
