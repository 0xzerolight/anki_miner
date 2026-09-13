"""S18: the engine pack shows as part of the language's one download row."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.panels import mining_language_settings_panel as module  # noqa: E402
from anki_miner.gui.widgets.panels.mining_language_settings_panel import (  # noqa: E402
    MiningLanguageSettingsPanel,
    pack_already_importable,
)
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent  # noqa: E402
from anki_miner.services import language_pack_installer  # noqa: E402


def _pack(code: str, import_name: str, mb: int, requires: tuple[str, ...] = ()) -> LanguagePack:
    spec = ArtifactSpec(
        url=f"https://example.invalid/{import_name}.whl", sha256="0" * 64, kind="wheel", member_prefix=f"{import_name}/"
    )
    comp = PackComponent(import_name=import_name, required=True, sentinels=("__init__.py",), universal=spec)
    return LanguagePack(code=code, approx_download_mb=mb, components=(comp,), requires=requires)


ENGINE = _pack("_spacy", "xxengine", 30)
MODEL = _pack("xx", "xxmodel", 12, requires=("_spacy",))


@pytest.fixture
def panel(qtbot, monkeypatch, tmp_path):
    packs = {"xx": MODEL, "_spacy": ENGINE}
    monkeypatch.setattr(module, "AVAILABLE_LANGUAGES", ("ja", "xx"))
    monkeypatch.setattr(language_pack_installer, "load_pack", lambda code: packs.get(code))
    monkeypatch.setattr(language_pack_installer.paths, "ANKI_MINER_HOME", tmp_path)
    widget = MiningLanguageSettingsPanel()
    qtbot.addWidget(widget)
    return widget


def test_the_row_advertises_the_engine_it_will_fetch(panel):
    assert set(panel.language_pack_rows) == {"xx"}
    assert "42" in panel.language_pack_rows["xx"].status_label.text()


def test_the_size_drops_once_the_engine_is_on_disk(panel):
    engine = language_pack_installer.language_pack_root("_spacy") / "xxengine"
    engine.mkdir(parents=True)
    (engine / "__init__.py").write_text("", encoding="utf-8")

    panel._refresh_language_pack_row("xx")

    assert "12" in panel.language_pack_rows["xx"].status_label.text()


def test_importable_means_the_prerequisite_imports_too(monkeypatch):
    json_pack = _pack("_spacy", "json", 30)
    monkeypatch.setattr(language_pack_installer, "load_pack", lambda code: json_pack if code == "_spacy" else None)
    assert pack_already_importable(_pack("zz", "os", 1, requires=("_spacy",))) is True
    assert pack_already_importable(_pack("zz", "os", 1, requires=("_missing",))) is False
