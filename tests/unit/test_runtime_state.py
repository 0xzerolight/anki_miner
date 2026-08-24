"""Where recovery state lives, and the guarantee that it never travels.

D16-C keeps partial downloads and queue contents on disk. Neither is a setting:
a half-downloaded JMdict and a queue of local file paths describe *this machine
at this moment*, so carrying either into a settings export or a profile sidecar
would be wrong in every direction. The guarantee is structural rather than an
exclusion list someone has to remember to extend — ``export_config`` and
``ProfileStore`` only ever serialise :class:`AnkiMinerConfig`, and nothing in
``runtime_state/`` is part of it. These tests assert exactly that.
"""

from __future__ import annotations

import json

import pytest

from anki_miner.config import create_default_config
from anki_miner.gui.utils import queue_state_store, runtime_state
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore
from anki_miner.gui.utils.queue_state_store import QueueItemSnapshot, QueueSnapshot
from anki_miner.services import download_resume


@pytest.fixture()
def _home(tmp_path, monkeypatch):
    """Point every runtime-state root at a throwaway app home."""
    from anki_miner.config import paths

    home = tmp_path / "home"
    monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", home / "gui_config.json")
    monkeypatch.setattr(paths, "ANKI_MINER_HOME", home)
    return home


class TestLayout:
    def test_the_root_sits_beside_the_config_and_is_resolved_per_call(self, _home, tmp_path, monkeypatch):
        assert runtime_state.runtime_state_root() == _home / "runtime_state"
        # Retargeting CONFIG_FILE mid-session moves the root with it: the test
        # home fixtures rebind that attribute per test, and a snapshot taken at
        # import would keep writing into the user's real ~/.anki_miner.
        monkeypatch.setattr(GUIConfigManager, "CONFIG_FILE", tmp_path / "elsewhere" / "gui_config.json")
        assert runtime_state.runtime_state_root() == tmp_path / "elsewhere" / "runtime_state"

    def test_the_two_roots_are_separate_and_both_under_runtime_state(self, _home):
        assert runtime_state.download_resume_root() == _home / "runtime_state" / "downloads"
        assert runtime_state.queue_state_root() == _home / "runtime_state" / "queues"

    def test_the_service_side_resolver_agrees_with_the_gui_side_one(self, _home):
        """Two callers, one directory — the downloader may not import the GUI."""
        assert download_resume.default_resume_root() == runtime_state.download_resume_root()

    def test_it_is_not_inside_the_profiles_directory(self, _home):
        assert ProfileStore.profiles_dir() not in runtime_state.runtime_state_root().parents
        assert runtime_state.runtime_state_root() != ProfileStore.profiles_dir()

    def test_a_key_cannot_name_a_file_outside_the_root(self):
        with pytest.raises(ValueError, match="unsafe"):
            runtime_state.validate_key("../escape")
        assert runtime_state.validate_key("queue.audiobook") == "queue.audiobook"

    def test_is_within_answers_on_resolved_paths(self, _home):
        root = runtime_state.queue_state_root()
        root.mkdir(parents=True, exist_ok=True)
        assert runtime_state.is_within(root / "a.json", root) is True
        assert runtime_state.is_within(root, root) is True
        assert runtime_state.is_within(root.parent / "a.json", root) is False


def _seed_runtime_state(home):
    """Leave one partial download and one queue snapshot on disk."""
    resume = download_resume.ResumeState(runtime_state.download_resume_root(), "resource-dict-jmdict")
    resume.ensure_root()
    resume.part_path.write_bytes(b"partial-jmdict-bytes")
    resume.manifest_path.write_text("{}", encoding="utf-8")
    queue_state_store.save(
        QueueSnapshot(
            key="queue.youtube",
            items=(
                QueueItemSnapshot(
                    item_id="row-1",
                    source=queue_state_store.url_source("https://youtu.be/abc", title="Ep 1"),
                    title="Ep 1",
                ),
            ),
        )
    )


class TestNeverExported:
    def test_a_settings_export_carries_no_runtime_state(self, _home, tmp_path):
        _seed_runtime_state(_home)
        export = tmp_path / "settings.json"
        GUIConfigManager.export_config(create_default_config(), export)
        text = export.read_text(encoding="utf-8")
        assert "runtime_state" not in text
        assert "youtu.be" not in text
        assert "resource-dict-jmdict" not in text
        payload = json.loads(text)
        assert set(payload) == {"anki_miner_settings", "app_version", "config_schema_version", "settings"}
        # downloader_* are the Download tool's persisted run options — genuine
        # settings (like condenser_*), exempt from the runtime-state heuristic.
        assert not any(
            ("queue" in key or "download" in key) and not key.startswith("downloader_") for key in payload["settings"]
        )

    def test_a_profile_sidecar_carries_no_runtime_state(self, _home):
        _seed_runtime_state(_home)
        ProfileStore.write_profile("anime", create_default_config(), name="Anime")
        sidecar = ProfileStore.profiles_dir() / "anime.json"
        text = sidecar.read_text(encoding="utf-8")
        assert "runtime_state" not in text
        assert "youtu.be" not in text

    def test_the_live_config_file_carries_no_runtime_state(self, _home):
        _seed_runtime_state(_home)
        GUIConfigManager.save_config(create_default_config())
        text = GUIConfigManager.CONFIG_FILE.read_text(encoding="utf-8")
        assert "runtime_state" not in text
        assert "youtu.be" not in text

    def test_importing_settings_cannot_plant_a_queue_or_a_partial(self, _home, tmp_path):
        """Even a hand-written file naming these keys leaves the store empty."""
        hostile = tmp_path / "hostile.json"
        hostile.write_text(
            json.dumps(
                {
                    "anki_miner_settings": 1,
                    "settings": {
                        "runtime_state": {"queues": {"queue.youtube": ["https://evil"]}},
                        "queue_state": ["https://evil"],
                    },
                }
            ),
            encoding="utf-8",
        )
        GUIConfigManager.import_config(hostile, create_default_config())
        assert queue_state_store.stored_keys() == ()
        assert not runtime_state.runtime_state_root().exists() or not list(
            runtime_state.queue_state_root().glob("*.json")
        )
