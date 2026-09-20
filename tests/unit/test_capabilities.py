"""Registry integrity + search behaviour for the Usage Guide catalogue."""

from __future__ import annotations

import pytest

from anki_miner.gui.capabilities import (
    CAPABILITIES,
    MAIN_TABS,
    SUBTAB_KEYS,
    Capability,
    CapabilityTarget,
    search,
)
from anki_miner.languages.registry import get_profile
from tests.unit.languages.test_language_contract import CAPABILITY_VOCABULARY

#: The entries a Japanese session never lists (all three are Chinese gates;
#: ``measure-word`` reaches Cantonese too). Everything else is what a Japanese
#: session listed before the catalogue was gated, which the ja pin below fixes.
_NON_JAPANESE_IDS = ("script-variant", "pinyin", "measure-word")


def test_ids_are_unique() -> None:
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids)), "duplicate capability id(s)"


def test_dead_cross_episode_filter_is_not_advertised() -> None:
    assert all(cap.id != "cross-episode-count" for cap in CAPABILITIES)
    assert search("recurring") == []


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


def test_subtitle_regex_targets_filtering() -> None:
    # The regex presets live on the Filtering panel, not Transcription & Alignment.
    capability = next(c for c in CAPABILITIES if c.id == "subtitle-regex")
    assert capability.target == CapabilityTarget("settings", "filtering")


def test_subtitle_file_mining_is_findable() -> None:
    hits = search("srt")
    capability = next(c for c in hits if c.id == "subtitle-file-mining")
    assert capability.target == CapabilityTarget("reading", "subtitles")


def test_word_curator_is_findable() -> None:
    hits = search("curator")
    assert any(c.id == "word-curator" for c in hits)


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
    }


def test_chinese_lists_its_own_entries() -> None:
    chinese = get_profile("zh").capabilities
    shown = {cap.id for cap in search("", chinese)}

    assert set(_NON_JAPANESE_IDS) <= shown


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
