"""--api setup over faked installs: which profile it writes, skips, the language pack, progress."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.cli.api import setup as api_setup
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore
from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult
from anki_miner.services.resource_catalog import ResourceSpec

DICT = ResourceSpec(id="test-dict", kind="dict", display_name="Test dict", url="https://x/d.zip", license_note="")
FREQ = ResourceSpec(id="test-freq", kind="freq", display_name="Test freq", url="https://x/f.zip", license_note="")


@pytest.fixture(autouse=True)
def _no_seeded_marker(monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", None)


@pytest.fixture
def catalog(monkeypatch):
    profile = SimpleNamespace(catalog=(DICT, FREQ), unavailable_reason=None)
    monkeypatch.setattr(api_setup, "get_profile", lambda code: profile)
    monkeypatch.setattr(api_setup.packs, "load_pack", lambda code: None)
    return profile


def _installs(monkeypatch, *, fail=()):
    calls: list[str] = []

    def fake(spec, *, reporter, cancelled, **kwargs):
        calls.append(spec.id)
        reporter.downloading(10, 20, "")
        if spec.id in fail:
            return ResourceDownloadResult(spec.id, spec.kind, spec.display_name, spec.url, ok=False, detail="boom")
        return ResourceDownloadResult(
            spec.id,
            spec.kind,
            spec.display_name,
            spec.url,
            ok=True,
            detail="",
            dict_id=spec.id if spec.kind == "dict" else None,
            source_id=None if spec.kind == "dict" else "test-freq-title",
        )

    monkeypatch.setattr(api_setup, "install_resource", fake)
    return calls


def _setup(tmp_path, language="ja", profile_id=None, cancel=None):
    return api_setup.run_setup(language, profile_id, tmp_path / "progress.json", cancel or threading.Event())


def test_installs_and_switches_on_in_the_active_profile(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    _installs(monkeypatch)
    outcome = _setup(tmp_path)
    assert (outcome.cancelled, outcome.failed) == (False, False)
    assert outcome.result["language_pack"] == {"status": "not_needed", "message": None}
    assert [(r["id"], r["status"]) for r in outcome.result["resources"]] == [
        ("test-dict", "installed"),
        ("test-freq", "installed"),
    ]
    config = GUIConfigManager.load_config()
    assert config.dictionary_chain[0].dict_id == "test-dict"
    assert config.frequency_chain[0].source_id == "test-freq-title"
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert progress["stages"] == 2 and progress["item"] == "test-freq" and progress["total"] == 20


def test_the_active_marker_survives(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.ACTIVE_PROFILE_ID = "default"
    GUIConfigManager.save_config(test_config)
    GUIConfigManager.ACTIVE_PROFILE_ID = None
    ProfileStore.write_profile("default", test_config, name="Default")
    _installs(monkeypatch)
    assert _setup(tmp_path).result["profile"] == "default"
    assert GUIConfigManager.read_active_profile_id() == "default" and GUIConfigManager.ACTIVE_PROFILE_ID is None


def test_an_installed_resource_is_switched_on_not_downloaded(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    monkeypatch.setattr(api_setup, "_installed", lambda spec, slot, config: spec.kind == "dict")
    calls = _installs(monkeypatch)
    outcome = _setup(tmp_path)
    assert calls == ["test-freq"]
    assert outcome.result["resources"][0]["status"] == "already_installed"
    assert GUIConfigManager.load_config().dictionary_chain[0].dict_id == "test-dict"


def test_an_installed_but_disabled_resource_is_switched_back_on(catalog, test_config, tmp_path, monkeypatch) -> None:
    entry = replace(test_config.dictionary_chain[0], dict_id="test-dict", enabled=False)
    GUIConfigManager.save_config(replace(test_config, dictionary_chain=[entry]))
    monkeypatch.setattr(api_setup, "_installed", lambda spec, slot, config: spec.kind == "dict")
    _installs(monkeypatch)
    _setup(tmp_path)
    [on] = [e for e in GUIConfigManager.load_config().dictionary_chain if e.dict_id == "test-dict"]
    assert on.enabled is True


def test_another_language_waits_in_its_stash(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    _installs(monkeypatch)
    _setup(tmp_path, "ko")
    config = GUIConfigManager.load_config()
    assert config.language == "ja"
    assert config.language_stash["ko"]["dictionary_chain"][0].dict_id == "test-dict"


def test_a_failed_resource_is_listed_and_the_rest_still_apply(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    _installs(monkeypatch, fail={"test-dict"})
    outcome = _setup(tmp_path)
    assert outcome.failed and outcome.result["resources"][0] == {
        "id": "test-dict",
        "kind": "dict",
        "name": "Test dict",
        "status": "failed",
        "message": "boom",
    }
    assert GUIConfigManager.load_config().frequency_chain[0].source_id == "test-freq-title"


def test_a_non_active_profile_gets_them(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.ACTIVE_PROFILE_ID = "default"
    GUIConfigManager.save_config(test_config)
    GUIConfigManager.ACTIVE_PROFILE_ID = None
    ProfileStore.write_profile("default", test_config, name="Default")
    ProfileStore.write_profile("caller", test_config, name="Caller")
    _installs(monkeypatch)
    assert _setup(tmp_path, profile_id="caller").result["profile"] == "caller"
    assert ProfileStore.read_profile("caller").dictionary_chain[0].dict_id == "test-dict"
    assert GUIConfigManager.load_config().dictionary_chain == test_config.dictionary_chain


def test_the_language_pack_goes_first_and_onto_sys_path(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    order: list[str] = []
    catalog.unavailable_reason = lambda: "needs its pack"
    monkeypatch.setattr(api_setup.packs, "load_pack", lambda code: object())
    monkeypatch.setattr(api_setup.packs, "pack_supported", lambda code: True)
    monkeypatch.setattr(api_setup.packs, "is_installed", lambda code: False)
    monkeypatch.setattr(api_setup.packs, "install_language_pack", lambda code, root, **kw: order.append("pack"))
    monkeypatch.setattr(api_setup.packs, "ensure_language_packs_on_syspath", lambda: order.append("syspath"))
    calls = _installs(monkeypatch)
    outcome = _setup(tmp_path)
    assert outcome.result["language_pack"] == {"status": "installed", "message": None}
    assert order == ["pack", "syspath"] and calls == ["test-dict", "test-freq"]


def test_a_cancel_stops_before_the_next_item(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    cancel = threading.Event()
    cancel.set()
    calls = _installs(monkeypatch)
    outcome = _setup(tmp_path, cancel=cancel)
    assert outcome.cancelled and calls == []
    assert {r["status"] for r in outcome.result["resources"]} == {"not_attempted"}


def test_a_missing_progress_folder_is_bad_arguments(catalog, tmp_path) -> None:
    from anki_miner.cli.api.contract import ApiError

    with pytest.raises(ApiError) as err:
        api_setup.run_setup("ja", None, tmp_path / "no" / "p.json", threading.Event())
    assert err.value.code == "BAD_ARGUMENTS"


@pytest.fixture
def verdict(capfd, monkeypatch):
    from anki_miner.cli import api, entry

    monkeypatch.setattr(api, "_prepare_process", lambda: None)
    monkeypatch.setattr(entry, "_install_api_log", lambda: None)

    def run(*argv: str) -> dict:
        assert entry.main(["--api", *argv]) == 0
        [line] = capfd.readouterr().out.splitlines()
        return json.loads(line)

    return run


def _outcome(status="installed", *, cancelled=False):
    result = {
        "language": "ja",
        "profile": "default",
        "language_pack": {"status": "not_needed", "message": None},
        "resources": [{"id": "x", "kind": "dict", "name": "X", "status": status, "message": None}],
    }
    return api_setup.SetupOutcome(result=result, cancelled=cancelled, failed=status == "failed")


@pytest.mark.parametrize(
    ("outcome", "ok", "error"),
    [
        (_outcome(), True, None),
        (_outcome("failed"), False, None),
        (_outcome("not_attempted", cancelled=True), False, "CANCELLED"),
    ],
)
def test_setup_verdict(verdict, tmp_path, monkeypatch, outcome, ok, error) -> None:
    monkeypatch.setattr(api_setup, "run_setup", lambda *args: outcome)
    v = verdict("setup", "--language", "ja", "--progress", str(tmp_path / "p.json"))
    assert (v["ok"], v["error"], v["command"]) == (ok, error, "setup") and v["result"] == outcome.result


def test_setup_is_busy_while_any_window_is_open(verdict, tmp_path) -> None:
    from anki_miner.config import paths as config_paths
    from anki_miner.gui.app import _hold_window_marker

    config_paths.ANKI_MINER_HOME.mkdir(parents=True, exist_ok=True)
    marker = _hold_window_marker(config_paths.ANKI_MINER_HOME)
    try:
        v = verdict("setup", "--language", "ja", "--progress", str(tmp_path / "p.json"))
    finally:
        marker.unlock()
    assert v["error"] == "BUSY" and "window is open" in v["message"]
