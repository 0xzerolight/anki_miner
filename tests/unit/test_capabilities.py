"""Registry integrity + search behaviour for the Find a Feature catalogue."""

from __future__ import annotations

import pytest

from anki_miner.gui.capabilities import (
    CAPABILITIES,
    MAIN_TABS,
    SETTINGS_SUBTABS,
    Capability,
    search,
)


def test_ids_are_unique() -> None:
    ids = [c.id for c in CAPABILITIES]
    assert len(ids) == len(set(ids)), "duplicate capability id(s)"


def test_registry_is_non_trivial() -> None:
    # Guards against an accidental truncation of the catalogue.
    assert len(CAPABILITIES) >= 30


@pytest.mark.parametrize("cap", CAPABILITIES, ids=lambda c: c.id)
def test_every_target_resolves(cap: Capability) -> None:
    target = cap.target
    assert target.main_tab in MAIN_TABS, f"{cap.id}: unknown main_tab {target.main_tab!r}"
    if target.main_tab == "settings":
        assert target.settings_subtab in SETTINGS_SUBTABS, f"{cap.id}: unknown subtab {target.settings_subtab!r}"
    else:
        assert target.settings_subtab is None, f"{cap.id}: settings_subtab set on non-settings target"


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
    hits = search("hiragana or katakana")
    assert any(c.id == "kana-only-exclude" for c in hits)


def test_search_preserves_registry_order() -> None:
    hits = search("mine")
    order = [c.id for c in CAPABILITIES]
    assert [c.id for c in hits] == [i for i in order if i in {h.id for h in hits}]


def test_search_no_match_returns_empty() -> None:
    assert search("zzzz-no-such-feature-xyzzy") == []
