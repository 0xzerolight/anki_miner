"""Shape pins for the LanguageProfile type module."""

from __future__ import annotations

import dataclasses

from anki_miner.languages import profile as profile_mod
from anki_miner.languages.profile import LanguageProfile, ResourceSpec, ScriptFilterOption
from anki_miner.languages.token import LanguageToken
from anki_miner.services.resource_catalog import ResourceSpec as CatalogResourceSpec

EXPECTED_FIELDS = (
    "code",
    "display_name",
    "create_parser",
    "mined_form",
    "lookup",
    "reading",
    "sentence_annotator",
    "script",
    "audio_track_codes",
    "import_encodings",
    "scoped_defaults",
    "sentence_rules",
    "normalize",
    "dict_keys",
    "audio",
    "asr_language",
    "captions",
    "pos_defaults",
    "catalog",
    "capabilities",
    "card_field_defaults",
    "render_hooks",
    "content_style",
    "unavailable_reason",
)


def test_profile_field_order_is_frozen():
    assert tuple(f.name for f in dataclasses.fields(LanguageProfile)) == EXPECTED_FIELDS


def test_profile_is_frozen():
    assert LanguageProfile.__dataclass_params__.frozen


def test_resource_spec_is_the_catalog_one_not_a_copy():
    assert ResourceSpec is CatalogResourceSpec


def test_script_filter_option_shape():
    opt = ScriptFilterOption(option_id="x", label="X", config_field="")
    assert (opt.option_id, opt.label, opt.config_field) == ("x", "X", "")


def test_language_token_duck_shape():
    t = LanguageToken("학생", "NNG", lemma="학생")
    assert t.surface == "학생"
    assert (t.feature.pos1, t.feature.pos2, t.feature.kana) == ("NNG", "", "")
    assert getattr(t.feature, "orthBase", None) is None
    t.feature.kana_locked = True  # SimpleNamespace stays mutable


def test_module_declares_every_public_type():
    for name in profile_mod.__all__:
        assert hasattr(profile_mod, name), name
