"""Registry integrity + search behaviour for the Usage Guide catalogue."""

from __future__ import annotations

import pytest

from anki_miner.gui.capabilities import (
    CAPABILITIES,
    MAIN_TABS,
    SUBTAB_KEYS,
    UTILITY_SUBTABS,
    Capability,
    CapabilityTarget,
    effective_hidden_utilities,
    search,
    utility_labels,
)
from anki_miner.languages.registry import get_profile
from tests.unit.languages.test_language_contract import CAPABILITY_VOCABULARY

#: The entries a Japanese session never lists: the Chinese gates (``measure-word``
#: and ``tone-colour`` reach Cantonese too), Portuguese's variety and Korean's
#: hangul filters. Everything else is what a Japanese session listed before the
#: catalogue was gated, which the ja pin below fixes.
_NON_JAPANESE_IDS = (
    "script-variant",
    "pinyin",
    "measure-word",
    "tone-colour",
    "regional-variety",
    "hangul-filters",
)


def test_ids_are_unique() -> None:
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids)), "duplicate capability id(s)"


def test_dead_cross_episode_filter_is_not_advertised() -> None:
    assert all(cap.id != "cross-episode-count" for cap in CAPABILITIES)
    assert search("recurring") == []


def test_media_downloader_is_findable_by_subtitles_only() -> None:
    assert any(cap.id == "media-downloader" for cap in search("subtitles only"))


def test_registry_is_non_trivial() -> None:
    # Guards against an accidental truncation of the catalogue.
    assert len(CAPABILITIES) >= 75


def test_dialog_only_entries_live_in_tools_category() -> None:
    # A target-less row shows no Open button, so its description must say where
    # the feature lives; the Tools & maintenance block groups them.
    target_less = [c for c in CAPABILITIES if c.target is None]
    assert len(target_less) >= 10
    assert {c.category for c in target_less} == {"Tools & maintenance"}


def test_system_health_is_findable() -> None:
    hits = search("health")
    assert any(c.id == "system-health" for c in hits)


def test_merging_a_season_is_findable() -> None:
    """The merge option lives inside Condense, so it needs its own keywords."""
    assert any(c.id == "condense-options" for c in search("season"))
    assert any(c.id == "condense-options" for c in search("merge"))


def test_mining_language_is_findable() -> None:
    """Its own destination since v2.13; without an entry nothing points at it."""
    assert any(c.id == "mining-language" for c in search("korean"))

    entry = next(c for c in CAPABILITIES if c.id == "mining-language")
    assert entry.target is not None
    assert (entry.target.main_tab, entry.target.subtab) == ("settings", "mining_language")


def test_target_is_optional() -> None:
    cap = Capability(
        id="x-dialog-only",
        title="t",
        description="d",
        category="c",
        keywords=("k",),
    )
    assert cap.target is None


def test_categories_are_contiguous() -> None:
    # Each category must form one block so the browser prints each header once.
    seen: list[str] = []
    for cap in CAPABILITIES:
        if not seen or seen[-1] != cap.category:
            assert cap.category not in seen, f"category {cap.category!r} appears in two blocks"
            seen.append(cap.category)


@pytest.mark.parametrize("cap", CAPABILITIES, ids=lambda c: c.id)
def test_every_target_resolves(cap: Capability) -> None:
    target = cap.target
    if target is None:
        return  # dialog/menu-only entry; nothing to navigate to
    assert target.main_tab in MAIN_TABS, f"{cap.id}: unknown main_tab {target.main_tab!r}"
    subtabs = SUBTAB_KEYS.get(target.main_tab, frozenset())
    assert (
        target.subtab is None or target.subtab in subtabs
    ), f"{cap.id}: unknown subtab {target.subtab!r} for {target.main_tab!r}"


@pytest.mark.parametrize("cap", CAPABILITIES, ids=lambda c: c.id)
def test_text_fields_present(cap: Capability) -> None:
    assert cap.title.strip()
    assert cap.description.strip()
    assert cap.category.strip()
    assert cap.keywords, f"{cap.id}: no search keywords"


def test_empty_query_returns_everything_in_order() -> None:
    assert search("") == list(CAPABILITIES)
    assert search("   ") == list(CAPABILITIES)


def test_search_matches_keyword_case_insensitively() -> None:
    hits = search("I+1")
    assert any(c.id == "i-plus-one" for c in hits)


def test_search_matches_title() -> None:
    hits = search("audiobook")
    assert any(c.id == "audiobook-mining" for c in hits)


def test_search_matches_description() -> None:
    hits = search("without kanji")
    assert any(c.id == "kana-only-exclude" for c in hits)


def test_pos_filter_is_not_advertised() -> None:
    # allowed_pos/excluded_subtypes are config-file-only; the guide covers the GUI.
    assert all(cap.id != "pos-filter" for cap in CAPABILITIES)


def test_restyle_mined_cards_is_dialog_only() -> None:
    # It is a Tools-menu action; there is no tab that hosts a Restyle control.
    hits = search("restyle mined cards")
    capability = next(c for c in hits if c.id == "restyle-mined-cards")
    assert capability.target is None


def test_subtitle_regex_targets_sentences() -> None:
    # The regex presets live on the Sentences panel, not Transcription & Alignment.
    capability = next(c for c in CAPABILITIES if c.id == "subtitle-regex")
    assert capability.target == CapabilityTarget("settings", "sentences")


def test_bold_target_word_targets_sentences() -> None:
    # Moved off Filtering with the rest of the sentence-content settings (T9).
    capability = next(c for c in CAPABILITIES if c.id == "bold-target-word")
    assert capability.target == CapabilityTarget("settings", "sentences")


def test_subtitle_file_mining_is_findable() -> None:
    hits = search("srt")
    capability = next(c for c in hits if c.id == "subtitle-file-mining")
    assert capability.target == CapabilityTarget("reading", "subtitles")


def test_word_curator_is_findable() -> None:
    hits = search("curator")
    assert any(c.id == "word-curator" for c in hits)


def test_review_words_entry_targets_a_screen_that_has_the_checkbox():
    entry = next(c for c in CAPABILITIES if c.id == "word-curator")
    assert entry.target == CapabilityTarget("video", "batch")


def test_secondary_subtitles_is_findable() -> None:
    hits = search("bilingual")
    assert hits and hits[0].id == "secondary-subtitles"
    assert hits[0].target == CapabilityTarget("video", "single")


def test_name_wordsets_is_findable() -> None:  # audit AP3-010
    hits = search("surname")
    capability = next(c for c in hits if c.id == "name-wordsets")
    assert capability.target == CapabilityTarget("settings", "filtering")


def test_search_preserves_registry_order() -> None:
    hits = search("mine")
    order = [c.id for c in CAPABILITIES]
    assert [c.id for c in hits] == [i for i in order if i in {h.id for h in hits}]


def test_search_no_match_returns_empty() -> None:
    assert search("zzzz-no-such-feature-xyzzy") == []


def test_every_requires_names_a_real_profile_capability() -> None:
    # A typo'd flag is an entry no language can ever see.
    assert {cap.requires for cap in CAPABILITIES if cap.requires} <= CAPABILITY_VOCABULARY


def test_no_capability_set_lists_the_whole_catalogue() -> None:
    # Callers with no mining language in scope (and the registry's own tests)
    # must keep seeing every entry.
    assert search("", None) == list(CAPABILITIES)


def test_japanese_lists_exactly_the_entries_it_always_did() -> None:
    japanese = get_profile("ja").capabilities
    expected = [cap.id for cap in CAPABILITIES if cap.id not in _NON_JAPANESE_IDS]

    assert [cap.id for cap in search("", japanese)] == expected


def test_chinese_hides_the_japanese_only_entries() -> None:
    chinese = get_profile("zh").capabilities
    shown = {cap.id for cap in search("", chinese)}

    assert not shown & {
        "kana-only-exclude",
        "kana-variant-known",
        "name-wordsets",
        "furigana",
        "pitch-accent",
        # Settings gates the whole Preset row on note_presets, so the guide's
        # Open button would land a zh session on a page without it.
        "note-type-preset",
    }


def test_chinese_lists_its_own_entries() -> None:
    chinese = get_profile("zh").capabilities
    shown = {cap.id for cap in search("", chinese)}

    assert {"script-variant", "pinyin", "measure-word", "tone-colour"} <= shown


def test_chinese_entries_are_findable_by_search() -> None:
    chinese = get_profile("zh").capabilities

    assert any(cap.id == "pinyin" for cap in search("pinyin", chinese))
    assert any(cap.id == "script-variant" for cap in search("traditional", chinese))
    assert any(cap.id == "measure-word" for cap in search("classifier", chinese))


def test_the_traditional_field_is_named_only_under_its_own_gate() -> None:
    # The Traditional Field row is gated on script_variants, so only an entry
    # carrying that same gate may tell a user to map it.
    naming = [cap for cap in CAPABILITIES if "Traditional Field" in cap.description]

    assert [cap.requires for cap in naming] == ["script_variants"]


def test_cantonese_is_not_sent_to_a_row_it_cannot_see() -> None:
    # yue declares measure_word but not script_variants: it shows the Measure
    # Word row and no Traditional one.
    cantonese = get_profile("yue").capabilities
    shown = search("", cantonese)

    assert any(cap.id == "measure-word" for cap in shown)
    assert not any("Traditional Field" in cap.description for cap in shown)


def test_search_within_a_capability_set_drops_gated_hits() -> None:
    japanese = get_profile("ja").capabilities
    chinese = get_profile("zh").capabilities

    assert any(cap.id == "pitch-accent" for cap in search("pitch", japanese))
    assert not any(cap.id == "pitch-accent" for cap in search("pitch", chinese))
    assert not any(cap.id == "pinyin" for cap in search("pinyin", japanese))


def _entry(cap_id: str) -> Capability:
    return next(cap for cap in CAPABILITIES if cap.id == cap_id)


@pytest.mark.parametrize(
    "cap_id",
    ["jisho-fallback", "manga-mining", "manga-ocr", "download-resources", "card-backfill", "deck-filter"],
)
def test_ungated_entries_name_their_japanese_only_part(cap_id: str) -> None:
    # No profile capability gates these, so every language lists them; the
    # text has to say which part only Japanese gets.
    cap = _entry(cap_id)

    assert "Japanese" in f"{cap.title} {cap.description}"


def test_audiobook_sync_names_the_reading_subtab_by_its_label() -> None:
    assert "Reading -> Subtitle Files" in _entry("audiobook-sync").description


def test_word_audio_entry_scopes_edge_tts_to_the_languages_that_offer_it() -> None:
    # Settings -> Audio offers Edge only where the profile names an Edge voice;
    # a fifth language gaining one has to be added to the entry's text too.
    from anki_miner.languages import AVAILABLE_LANGUAGES

    with_edge = {code for code in AVAILABLE_LANGUAGES if get_profile(code).audio.edge_voice}
    cap = _entry("expression-audio")

    assert with_edge == {"yue", "he", "fa", "sl"}
    assert "Microsoft Edge text-to-speech (Cantonese, Hebrew, Persian, Slovenian)" in cap.description
    assert "edge tts" in cap.keywords


def test_deck_filter_names_every_language_with_a_script_filter() -> None:
    from anki_miner.languages import AVAILABLE_LANGUAGES

    with_options = {code for code in AVAILABLE_LANGUAGES if get_profile(code).script.filter_options()}

    assert with_options == {"ja", "ko"}
    assert "script type (Japanese, Korean)" in _entry("deck-filter").description


def test_portuguese_variety_entry_states_what_the_variety_changes() -> None:
    # The variety picks the Google voice and the suggested frequency list; it
    # does not touch the card front or the dictionary lookup.
    description = _entry("regional-variety").description

    assert "frequency list" in description
    assert "card front" not in description


def test_sentence_tts_names_the_languages_without_a_voice() -> None:
    assert "Persian or Slovenian" in _entry("sentence-tts").description


@pytest.mark.parametrize(
    ("cap_id", "code"),
    [("tone-colour", "zh"), ("tone-colour", "yue"), ("regional-variety", "pt"), ("hangul-filters", "ko")],
)
def test_gated_setting_entry_is_listed_for_its_language(cap_id: str, code: str) -> None:
    shown = {cap.id for cap in search("", get_profile(code).capabilities)}

    assert cap_id in shown


@pytest.mark.parametrize("cap_id", ["tone-colour", "regional-variety", "hangul-filters"])
def test_gated_setting_entry_is_hidden_from_japanese(cap_id: str) -> None:
    shown = {cap.id for cap in search("", get_profile("ja").capabilities)}

    assert cap_id not in shown


# ---------------------------------------------------------------------------
# The Utilities tools (Settings -> Appearance & Language hides them)
# ---------------------------------------------------------------------------


def test_utility_subtabs_are_the_subtitles_container_keys() -> None:
    assert frozenset(UTILITY_SUBTABS) == SUBTAB_KEYS["subtitles"]
    assert len(UTILITY_SUBTABS) == len(set(UTILITY_SUBTABS))


def test_utility_labels_follow_the_tab_order() -> None:
    labels = utility_labels()

    assert tuple(labels) == UTILITY_SUBTABS
    assert labels["mokuro"] == "Manga OCR"
    assert labels["booksync"] == "Audiobook Sync"


def test_unknown_hidden_keys_are_dropped() -> None:
    assert effective_hidden_utilities(("retime", "no-such-tool")) == frozenset({"retime"})


def test_hiding_every_tool_hides_none() -> None:
    assert effective_hidden_utilities(UTILITY_SUBTABS) == frozenset()
    assert effective_hidden_utilities((*UTILITY_SUBTABS, "no-such-tool")) == frozenset()


def test_hiding_all_but_one_is_honoured() -> None:
    assert effective_hidden_utilities(UTILITY_SUBTABS[1:]) == frozenset(UTILITY_SUBTABS[1:])


def test_the_visibility_setting_has_a_guide_entry() -> None:
    entry = _entry("utilities-visibility")

    assert entry.target == CapabilityTarget("settings", "ui")
    assert "tools" in " ".join(entry.keywords)
