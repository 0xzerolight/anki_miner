"""The mining-language config field and its duplicated-literal sync guard."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.config.config import _LANGUAGE_CODES
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.languages import AVAILABLE_LANGUAGES
from tests.unit.languages.stub_registry import unregister_profile


@pytest.fixture
def isolated_config_file(tmp_path: Path, monkeypatch) -> Path:
    fake = tmp_path / "gui_config.json"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", fake)
    return fake


@pytest.fixture
def ko_unregistered(monkeypatch) -> None:
    """``ko`` is the code the degrade cases below carry, taken back out.

    Stage 3 registered it, and ``__post_init__`` folds anything outside
    ``_LANGUAGE_CODES`` to ``ja``, so hiding a real code is what keeps these
    cases pointed at ``config_language``'s still-live degrade branch.
    """
    unregister_profile(monkeypatch, "ko")


def test_language_defaults_to_ja():
    assert AnkiMinerConfig().language == "ja"


def test_config_literal_matches_available_languages():
    """config must not import the languages package, so the tuple is a
    hand-duplicated literal; this assertion keeps the two in sync."""
    assert _LANGUAGE_CODES == AVAILABLE_LANGUAGES


def test_unknown_language_resets_to_ja():
    assert AnkiMinerConfig(language="tlh").language == "ja"


def test_language_is_normalized():
    assert AnkiMinerConfig(language="  ZH ").language == "zh"


def test_language_survives_replace():
    assert dataclasses.replace(AnkiMinerConfig(), language="ko").language == "ko"


def test_language_round_trips_through_json(isolated_config_file):
    GUIConfigManager.save_config(dataclasses.replace(create_default_config(), language="zh"))
    assert GUIConfigManager.load_config().language == "zh"


def test_reset_to_defaults_keeps_the_mining_language(test_config, qtbot, monkeypatch):
    """`language_stash` is machine-specific, so Reset to Defaults preserves it.
    Resetting `language` alongside it would leave the stash holding a parked
    snapshot for the language now active, which the field's invariant forbids
    ("every language that is NOT active")."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui.widgets.settings_tab import SettingsTab

    tab = SettingsTab(
        dataclasses.replace(test_config, language="zh", language_stash={"ja": {"anki_deck_name": "JA"}}),
    )
    qtbot.addWidget(tab)
    monkeypatch.setattr(
        "anki_miner.gui.widgets.settings_tab.QMessageBox.question",
        lambda *a, **kw: QMessageBox.StandardButton.Yes,
    )
    emitted: list[AnkiMinerConfig] = []
    tab.config_changed.connect(emitted.append)

    tab._on_reset_to_defaults_clicked()

    assert emitted[-1].language == "zh"
    assert emitted[-1].language not in emitted[-1].language_stash


def test_an_unregistered_language_degrades_to_ja(ko_unregistered):
    """`_LANGUAGE_CODES` whitelists a code whose profile a build may not carry,
    so a config can name one the registry cannot build. Pre-1B the field was
    inert; degrading here keeps it inert instead of raising out of every
    `get_profile(config_language(config))` site.

    The rule stops applying to a code the moment its profile lands, which is why
    the fixture hides one: ja, zh and ko all register as of Stage 3."""
    from anki_miner.languages.registry import available_languages, config_language

    assert "ko" not in available_languages()
    assert config_language(AnkiMinerConfig(language="ko")) == "ja"


def test_a_registered_language_is_returned_verbatim():
    """Self-healed the moment Stage 2A registered the real zh profile."""
    from anki_miner.languages.registry import config_language

    assert config_language(AnkiMinerConfig(language="zh")) == "zh"


def test_the_degrade_is_logged_once_per_code(caplog, monkeypatch, ko_unregistered):
    from anki_miner.languages import registry

    monkeypatch.setattr(registry, "_DEGRADE_WARNED", set())
    with caplog.at_level("WARNING", logger="anki_miner.languages.registry"):
        for _ in range(3):
            registry.config_language(AnkiMinerConfig(language="ko"))
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "ko" in warnings[0].getMessage()


def test_settings_panels_load_an_unregistered_language(test_config, qtbot, ko_unregistered):
    """The finding's crash site: `load_from_config` resolves the profile's
    capabilities, and an unbuilt code used to raise ValueError with no in-app
    recovery."""
    from anki_miner.gui.widgets.panels.anki_settings_panel import AnkiSettingsPanel
    from anki_miner.gui.widgets.panels.filtering_settings_panel import FilteringSettingsPanel

    cfg = dataclasses.replace(test_config, language="ko")
    # Held in a list: qtbot.addWidget keeps only a weakref.
    panels = [FilteringSettingsPanel(), AnkiSettingsPanel()]
    for panel in panels:
        qtbot.addWidget(panel)
        panel.load_from_config(cfg)


def test_anki_service_accepts_an_unregistered_language(test_config, ko_unregistered):
    from anki_miner.services.anki_service import AnkiService

    assert AnkiService(dataclasses.replace(test_config, language="ko")) is not None


def test_deck_filter_scan_runs_under_an_unregistered_language(test_config, ko_unregistered):
    """Utilities -> Deck Filter, scan half: the script-type gate read the raw
    field, so a hand-edited config carrying an unbuilt code raised ValueError
    mid-scan. Degraded, the scan applies the JA hiragana-only rule."""
    from types import SimpleNamespace

    from anki_miner.services.deck_filter import DeckFilterOptions, scan_deck_filter
    from anki_miner.services.word_filter import WordFilterService

    class _Anki:
        def find_notes(self, query):
            return [1]

        def notes_info(self, note_ids):
            return [
                {
                    "noteId": 1,
                    "modelName": "Basic",
                    "tags": [],
                    "fields": {"Expression": {"value": "する", "order": 0}},
                }
            ]

        def get_vocabulary_excluding_deck(self, deck):
            return set()

    cfg = dataclasses.replace(
        test_config,
        language="ko",
        exclude_hiragana_only_words=True,
        exclude_katakana_only_words=True,
        use_known_words_db=False,
    )
    plan = scan_deck_filter(
        _Anki(),
        cfg,
        SimpleNamespace(word_filter=WordFilterService(cfg)),
        DeckFilterOptions(source_deck="Src", target_deck="Dst"),
    )

    assert plan.scanned == 1
    assert dict(plan.drops)["script_type"] == 1
    assert plan.kept == ()


def test_the_deck_filter_bundle_builds_under_an_unregistered_language(test_config, ko_unregistered):
    """Utilities -> Deck Filter, service bundle: both profile reads used the
    raw field, so the scan crashed before it started."""
    from anki_miner.gui.workers.deck_filter_worker import _build_filter_bundle
    from anki_miner.languages.registry import get_profile

    bundle = _build_filter_bundle(dataclasses.replace(test_config, language="ko"), None)

    ja = get_profile("ja")
    # Private reads: the bundle exists to hand these two to the filter, and
    # nothing public re-exposes which policy objects it picked.
    assert bundle.word_filter._mined_form is ja.mined_form
    assert bundle.word_filter._script is ja.script


def test_subtitle_generation_runs_under_an_unregistered_language(
    test_config, tmp_path, monkeypatch, qtbot, ko_unregistered
):
    """Utilities -> Generate: the ASR language read the raw field, so the
    worker raised on its first file."""
    from anki_miner.gui.workers import subtitle_gen_worker as worker_mod
    from anki_miner.languages.registry import get_profile
    from anki_miner.services.asr.subtitle_generation import SubtitleGenResult, SubtitleGenStatus

    captured: dict[str, object] = {}

    def _fake_generate(config, extractor, video_path, out_srt, **kwargs):
        captured.update(kwargs)
        return SubtitleGenResult(status=SubtitleGenStatus.NO_SPEECH)

    monkeypatch.setattr(worker_mod, "generate_subtitle_one", _fake_generate)

    video = tmp_path / "ep01.mkv"
    worker = worker_mod.SubtitleGenWorker(
        dataclasses.replace(test_config, language="ko"),
        [video],
        extractor=object(),
    )
    try:
        worker._process_file(0, video, tmp_path / "ep01.srt")
    finally:
        worker.deleteLater()

    assert captured["language"] == get_profile("ja").asr_language


def test_manage_known_words_opens_under_an_unregistered_language(test_config, qtbot, monkeypatch, ko_unregistered):
    """Settings -> Filtering -> Manage Known Words: the content style read the
    degraded code already; this pins it (the site swallows exceptions into a
    screen issue, so a regression would surface as an error banner, not a
    raise)."""
    from anki_miner.gui.widgets.dialogs import known_words_dialog
    from anki_miner.gui.widgets.settings_tab import SettingsTab
    from anki_miner.languages.registry import get_profile

    captured: dict[str, object] = {}

    class _FakeDialog:
        def __init__(self, db, parent, **kwargs):
            captured.update(kwargs)

        def exec(self):
            return 0

    monkeypatch.setattr(known_words_dialog, "KnownWordsManagerDialog", _FakeDialog)

    tab = SettingsTab(dataclasses.replace(test_config, language="ko"))
    qtbot.addWidget(tab)
    issues: list[object] = []
    monkeypatch.setattr(tab, "show_screen_issue", issues.append)

    tab._on_manage_known_words()

    assert issues == []
    assert captured["language"] == "ja"
    assert captured["content_style"] is get_profile("ja").content_style


def test_old_build_drops_the_key_without_raising(isolated_config_file):
    """Downgrade simulation: an unknown key is dropped by the valid-keys filter
    in _migrate_dict, exactly as `language` would be on a pre-Stage-0 build."""
    payload = {"language": "zh", "language_of_the_future": "xx"}
    migrated = GUIConfigManager._migrate_dict(payload)
    assert "language_of_the_future" not in migrated
    assert AnkiMinerConfig(**migrated).language == "zh"
    isolated_config_file.write_text(json.dumps({"language_of_the_future": "xx"}), encoding="utf-8")
    assert GUIConfigManager.load_config().language == "ja"
