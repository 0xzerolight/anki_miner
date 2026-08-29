"""Every render-hook card field is mappable from Settings -> Anki.

The mapped field NAME is the on/off switch for these keys (``anki_note_builder``
:data:`OPTIONAL_FIELD_KEYS`), and each profile ships them empty. A key whose row
does not exist is therefore a feature nobody can ever turn on -- ``measure_word``
had the row, ``hanja``, ``expression_pinyin`` and ``expression_traditional`` did
not. Each row is capability-gated and contributes its key only while visible, so
a ja ``anki_fields`` never grows an empty non-ja key.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
from anki_miner.languages.registry import get_profile

#: (config language, hook key, panel attribute, gating capability).
HOOK_ROWS = [
    ("ko", "hanja", "hanja_field_input", "hanja"),
    ("zh", "expression_pinyin", "expression_pinyin_field_input", "pinyin"),
    ("zh", "expression_traditional", "expression_traditional_field_input", "script_variants"),
]


def _anki(qtbot, config) -> AnkiSettingsPanel:
    panel = AnkiSettingsPanel()
    qtbot.addWidget(panel)
    panel.load_from_config(config)
    return panel


def _lang(config, code, **overrides):
    return replace(config, language=code, **overrides)


@pytest.mark.parametrize("code,key,attr,capability", HOOK_ROWS)
def test_the_gating_capability_belongs_to_that_language(code, key, attr, capability):
    assert capability in get_profile(code).capabilities
    assert key in get_profile(code).card_field_defaults


@pytest.mark.parametrize("code,key,attr,capability", HOOK_ROWS)
def test_the_row_is_a_ja_no_op(qtbot, test_config, code, key, attr, capability):
    panel = _anki(qtbot, test_config)
    assert not getattr(panel, attr).isVisibleTo(panel)
    assert key not in panel.get_card_fields()


@pytest.mark.parametrize("code,key,attr,capability", HOOK_ROWS)
def test_the_row_shows_and_round_trips_for_its_own_language(qtbot, test_config, code, key, attr, capability):
    config = _lang(config=test_config, code=code, anki_fields={**dict(test_config.anki_fields), key: "Mapped"})
    panel = _anki(qtbot, config)
    assert getattr(panel, attr).isVisibleTo(panel)
    assert getattr(panel, attr).text() == "Mapped"
    assert panel.get_card_fields()[key] == "Mapped"

    getattr(panel, attr).setText("Renamed")
    assert panel.get_card_fields()[key] == "Renamed"


@pytest.mark.parametrize("code,key,attr,capability", HOOK_ROWS)
def test_an_unmapped_row_stays_empty_rather_than_inventing_a_field(qtbot, test_config, code, key, attr, capability):
    """The profile ships the key empty; the row must not seed a name."""
    config = _lang(config=test_config, code=code)
    panel = _anki(qtbot, config)
    assert getattr(panel, attr).text() == ""
    assert panel.get_card_fields()[key] == ""


@pytest.mark.parametrize("code,key,attr,capability", HOOK_ROWS)
def test_switching_back_to_ja_drops_the_key_again(qtbot, test_config, code, key, attr, capability):
    config = _lang(config=test_config, code=code, anki_fields={**dict(test_config.anki_fields), key: "Mapped"})
    panel = _anki(qtbot, config)
    panel.load_from_config(test_config)
    assert not getattr(panel, attr).isVisibleTo(panel)
    assert key not in panel.get_card_fields()


def test_every_hook_field_key_has_a_row(qtbot, test_config):
    """The regression that made this file: a hook key with no row is inert.

    Derived from the profiles, not a hardcoded copy: a future hook whose
    field_names() gain a key without a matching Settings row must fail here.
    measure_word is gloss-derived rather than hook-rendered; its row landed
    with the zh settings surfaces and it joins the derived set explicitly.
    """
    from anki_miner.languages.registry import available_languages, get_profile
    from anki_miner.services.anki_note_builder import OPTIONAL_FIELD_KEYS

    hook_keys = {
        name for code in available_languages() for hook in get_profile(code).render_hooks for name in hook.field_names()
    } | {"measure_word"}
    assert hook_keys <= OPTIONAL_FIELD_KEYS
    panel = _anki(qtbot, test_config)
    covered = {key for _, key, _, _ in HOOK_ROWS} | {"measure_word"}
    assert covered == hook_keys
    for _, _, attr, _ in HOOK_ROWS:
        assert hasattr(panel, attr)
