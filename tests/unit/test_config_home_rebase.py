"""A copied or relocated data folder keeps working (BA-009).

gui_config.json stores the ANKI_MINER_HOME-derived default paths as absolute
paths. Loading a file written under another home rebases every default-shaped
path onto the current home when at least two of them agree on that old home.
Deliberate overrides stay. The persisted format does not change.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import anki_miner.config.config as config_module
from anki_miner.cli import entry
from anki_miner.config import AnkiMinerConfig, create_default_config
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore


@pytest.fixture
def home_b() -> Path:
    """The current (isolated) home: the one the default factories resolve."""
    return config_module.ANKI_MINER_HOME


@pytest.fixture
def home_a(tmp_path: Path) -> Path:
    return tmp_path / "olduser" / ".anki_miner"


def _config_under(home: Path, monkeypatch: pytest.MonkeyPatch, **overrides) -> AnkiMinerConfig:
    """A default config built as if ANKI_MINER_HOME were ``home``."""
    with monkeypatch.context() as m:
        m.setattr(config_module, "ANKI_MINER_HOME", home)
        return create_default_config(**overrides)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _serialized(config: AnkiMinerConfig) -> dict:
    data = GUIConfigManager._paths_to_strings(GUIConfigManager._config_to_serializable_dict(config))
    data["config_schema_version"] = GUIConfigManager.CONFIG_SCHEMA_VERSION
    return data


def _home_fields(home: Path) -> list[str]:
    defaults = create_default_config()
    return [
        name
        for name in GUIConfigManager._path_field_names()
        if isinstance(getattr(defaults, name), Path) and getattr(defaults, name).is_relative_to(home)
    ]


def test_config_from_another_home_gets_every_default_path_on_this_home(home_a, home_b, monkeypatch, caplog):
    _write(GUIConfigManager.CONFIG_FILE, _serialized(_config_under(home_a, monkeypatch)))

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.utils.config_manager"):
        loaded = GUIConfigManager.load_config()

    defaults = create_default_config()
    assert len(_home_fields(home_b)) >= 10
    for name in GUIConfigManager._path_field_names():
        assert getattr(loaded, name) == getattr(defaults, name), name
    rebase_lines = [r.getMessage() for r in caplog.records if str(home_a) in r.getMessage()]
    assert len(rebase_lines) == 1 and str(home_b) in rebase_lines[0]


def test_deliberate_override_stays(home_a, home_b, monkeypatch):
    override = Path("/mnt/ext/dicts")
    beside = home_a / "my_freqs"
    config = _config_under(home_a, monkeypatch, dicts_root=override, freqs_root=beside)
    _write(GUIConfigManager.CONFIG_FILE, _serialized(config))

    loaded = GUIConfigManager.load_config()

    assert loaded.dicts_root == override
    assert loaded.freqs_root == beside
    assert loaded.stats_db_path == home_b / "stats.db"
    assert loaded.log_path == home_b / "anki_miner.log"


def test_lone_matching_field_does_not_rebase(home_a):
    _write(
        GUIConfigManager.CONFIG_FILE,
        {
            "config_schema_version": GUIConfigManager.CONFIG_SCHEMA_VERSION,
            "stats_db_path": str(home_a / "stats.db"),
        },
    )

    loaded = GUIConfigManager.load_config()

    assert loaded.stats_db_path == home_a / "stats.db"


def test_config_under_its_own_home_is_unchanged(home_b, monkeypatch, caplog):
    config = _config_under(home_b, monkeypatch, dicts_root=Path("/mnt/ext/dicts"))
    _write(GUIConfigManager.CONFIG_FILE, _serialized(config))

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.utils.config_manager"):
        loaded = GUIConfigManager.load_config()

    assert loaded == config
    assert not [r for r in caplog.records if "rebase" in r.getMessage().lower()]


def test_settings_profile_from_another_home_rebases(home_a, home_b, monkeypatch):
    ProfileStore.write_profile("anime", _config_under(home_a, monkeypatch), name="Anime")

    loaded = ProfileStore.read_profile("anime")

    assert loaded.known_words_db_path == home_b / "known_words.db"
    assert loaded.dicts_root == home_b / "dicts"


def test_cli_load_path_rebases(home_a, home_b, monkeypatch, tmp_path):
    _write(GUIConfigManager.CONFIG_FILE, _serialized(_config_under(home_a, monkeypatch)))
    seen: dict[str, object] = {}

    class _Run:
        def __init__(self, config, sink, cancel) -> None:
            seen["config"] = config

        def run(self, jobs):
            return []

    monkeypatch.setattr(entry, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_start_log", lambda log_path: seen.setdefault("log_path", log_path))
    monkeypatch.setattr(entry, "MiningRun", _Run)
    (tmp_path / "e1.mkv").touch()
    (tmp_path / "e1.srt").touch()

    entry.main(["mine", "pairs", "--pair", str(tmp_path / "e1.mkv"), str(tmp_path / "e1.srt")])

    assert seen["log_path"] == home_b / "anki_miner.log"
    config = seen["config"]
    assert isinstance(config, AnkiMinerConfig)
    assert config.dicts_root == home_b / "dicts"
    assert config.stats_db_path == home_b / "stats.db"
