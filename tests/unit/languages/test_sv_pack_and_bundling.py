"""The sv model pack, the recommended resources, the release smoke leg and the CC BY-SA 4.0 model notice."""

from __future__ import annotations

import json
from pathlib import Path

from anki_miner.languages.sv.catalog import SV_CATALOG
from anki_miner.services.language_pack_installer import load_pack

ROOT = Path(__file__).resolve().parents[3]
SV_WHEEL_SHA256 = "be4929fb30523dca0b6672f999cdbf4d64f165419f1eed0014ca3a36599b8b4d"


def test_the_model_pack_requires_the_engine():
    pack = load_pack("sv")
    assert pack is not None and pack.requires == ("_spacy",)
    (component,) = pack.components
    assert component.import_name == "sv_core_news_sm" and component.universal is not None
    assert component.universal.sha256 == SV_WHEEL_SHA256
    assert component.universal.url == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "sv_core_news_sm-3.8.0/sv_core_news_sm-3.8.0-py3-none-any.whl"
    )
    assert component.sentinels == (
        "__init__.py",
        "sv_core_news_sm-3.8.0/config.cfg",
        "sv_core_news_sm-3.8.0/meta.json",
    )


def test_the_resolved_fixture_matches_the_manifest():
    resolved = json.loads((ROOT / "tests" / "fixtures" / "language_packs" / "sv.resolved.json").read_text("utf-8"))
    assert resolved["code"] == "sv" and resolved["requires"] == ["_spacy"]
    (component,) = resolved["components"]
    assert component["universal"]["sha256"] == SV_WHEEL_SHA256
    assert component["universal"]["size"] == 12741934


def test_the_catalogue_offers_one_dictionary_and_one_lemmatised_frequency_list():
    assert [spec.id for spec in SV_CATALOG] == ["wty-sv-en", "opensubtitles-sv"]
    dictionary, frequency = SV_CATALOG
    assert dictionary.kind == "dict" and dictionary.url.endswith("/dict/sv/en/wty-sv-en.zip")
    assert frequency.kind == "freq" and frequency.lemmatise is True
    assert frequency.url.endswith("/content/2018/sv/sv_50k.txt")
    assert all("CC BY-SA 4.0" in spec.license_note for spec in SV_CATALOG)


def test_the_model_licence_notice_ships():
    notice = ROOT / "licenses" / "sv_core_news_sm"
    assert (notice / "LICENSE").is_file()
    assert "CC BY-SA 4.0" in (notice / "README.md").read_text(encoding="utf-8")
