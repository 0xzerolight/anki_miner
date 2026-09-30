"""C18 + D12 (UI/UX audit 2026-09-29): one list of mining languages for Settings and the wizard."""

from __future__ import annotations

import dataclasses

from anki_miner.gui.utils import language_choices
from anki_miner.languages.registry import get_profile


def _only(monkeypatch, codes, *, unavailable=(), downloadable=None):
    monkeypatch.setattr(language_choices, "AVAILABLE_LANGUAGES", codes)

    def fake_profile(code):
        profile = get_profile(code)
        if code in unavailable:
            return dataclasses.replace(profile, unavailable_reason=lambda: "missing engine")
        return dataclasses.replace(profile, unavailable_reason=None)

    monkeypatch.setattr(language_choices, "get_profile", fake_profile)
    monkeypatch.setattr(language_choices, "_pack_download_mb", lambda code: (downloadable or {}).get(code))


def test_labels_show_native_then_english(monkeypatch):
    _only(monkeypatch, ("ja", "th", "en"))
    labels = dict(language_choices.available_mining_languages())
    assert labels["th"] == "ไทย — Thai"
    assert labels["ja"] == "日本語 — Japanese"
    assert labels["en"] == "English"


def test_japanese_first_then_sorted_by_english_name(monkeypatch):
    _only(monkeypatch, ("zh", "de", "ja", "ar", "en"))
    codes = [code for code, _label in language_choices.available_mining_languages()]
    assert codes == ["ja", "ar", "zh", "en", "de"]


def test_a_language_needing_its_pack_is_offered_for_download(monkeypatch):
    _only(monkeypatch, ("ja", "de", "ko"), unavailable=("de", "ko"), downloadable={"de": 70})
    choices = {choice.code: choice for choice in language_choices.mining_language_choices()}
    assert choices["ja"].needs_download is False
    assert choices["de"].needs_download is True
    assert choices["de"].download_mb == 70
    assert choices["de"].native_name == "Deutsch"
    assert "ko" not in choices  # unavailable and no pack to fetch here


def test_download_able_languages_stay_out_of_the_mine_now_list(monkeypatch):
    _only(monkeypatch, ("ja", "de"), unavailable=("de",), downloadable={"de": 70})
    assert [code for code, _label in language_choices.available_mining_languages()] == ["ja"]
