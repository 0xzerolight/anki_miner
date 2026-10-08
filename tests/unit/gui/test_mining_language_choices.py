"""C18 + D12 (UI/UX audit 2026-09-29): one list of mining languages for Settings and the wizard."""

from __future__ import annotations

import dataclasses

from anki_miner.gui.utils import language_choices
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.registry import get_profile


def _only(monkeypatch, codes, *, unavailable=(), downloadable=None, names=None):
    monkeypatch.setattr(language_choices, "AVAILABLE_LANGUAGES", codes)

    def fake_profile(code):
        profile = get_profile(code)
        if names and code in names:
            profile = dataclasses.replace(profile, display_name=names[code])
        if code in unavailable:
            return dataclasses.replace(profile, unavailable_reason=lambda: "missing engine")
        return dataclasses.replace(profile, unavailable_reason=None)

    monkeypatch.setattr(language_choices, "get_profile", fake_profile)
    monkeypatch.setattr(language_choices, "_pack_download_mb", lambda code: (downloadable or {}).get(code))


def test_labels_show_only_the_native_name(monkeypatch):
    _only(monkeypatch, ("ja", "th", "en"))
    labels = dict(language_choices.available_mining_languages())
    assert labels["th"] == "ไทย"
    assert labels["ja"] == "日本語"
    assert labels["en"] == "English"


#: D2 (2026-10-08): the list in the order it shows. Latin first, A-Z ignoring
#: accents and case; then Greek, Cyrillic, Hebrew, Arabic, Thai, Han, Hangul.
_EVERY_LANGUAGE_IN_ORDER = [
    "Bahasa Indonesia",
    "Català",
    "Dansk",
    "Deutsch",
    "English",
    "Español",
    "Français",
    "Hrvatski",
    "Italiano",
    "Lietuvių",
    "Magyar",
    "Nederlands",
    "Norsk bokmål",
    "Polski",
    "Português",
    "Română",
    "Slovenščina",
    "Suomi",
    "Svenska",
    "Tiếng Việt",
    "Türkçe",
    "Ελληνικά",
    "Русский",
    "Українська",
    "עברית",
    "العربية",
    "فارسی",
    "ไทย",
    "中文",
    "廣東話",
    "日本語",
    "한국어",
]


def test_every_language_sorts_by_its_native_name_latin_scripts_first(monkeypatch):
    _only(monkeypatch, AVAILABLE_LANGUAGES)
    names = [name for _code, name in language_choices.available_mining_languages()]
    assert names == _EVERY_LANGUAGE_IN_ORDER


def test_accents_and_case_do_not_move_a_latin_name(monkeypatch):
    # Names a later language could carry. By code point both would land after
    # every unaccented capital, "Tiếng Việt" included.
    _only(monkeypatch, ("it", "en", "tr", "vi"), names={"en": "isiZulu", "tr": "Íslenska"})
    codes = [code for code, _name in language_choices.available_mining_languages()]
    assert codes == ["en", "tr", "it", "vi"]


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
