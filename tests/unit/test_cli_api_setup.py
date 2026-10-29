"""--api setup over faked installs: which profile it writes, skips, the language pack, progress."""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest

from anki_miner.cli.api import setup as api_setup
from anki_miner.exceptions import SetupError
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.gui.utils.profile_store import ProfileStore
from anki_miner.gui.workers.resource_download_worker import ResourceDownloadResult
from anki_miner.languages.switching import switch_language
from anki_miner.services.resource_catalog import ResourceSpec

DICT = ResourceSpec(id="test-dict", kind="dict", display_name="Test dict", url="https://x/d.zip", license_note="")
FREQ = ResourceSpec(id="test-freq", kind="freq", display_name="Test freq", url="https://x/f.zip", license_note="")


@pytest.fixture(autouse=True)
def _no_seeded_marker(monkeypatch):
    monkeypatch.setattr(GUIConfigManager, "ACTIVE_PROFILE_ID", None)


@pytest.fixture
def catalog(monkeypatch):
    profile = SimpleNamespace(catalog=(DICT, FREQ), unavailable_reason=None, asr_language="ja")
    monkeypatch.setattr(api_setup, "get_profile", lambda code: profile)
    monkeypatch.setattr(api_setup.packs, "load_pack", lambda code: None)
    return profile


def _installs(monkeypatch, *, fail=(), calls=None):
    calls = [] if calls is None else calls

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
    # The profile has mined ko before: ko has its parked settings.
    GUIConfigManager.save_config(switch_language(switch_language(test_config, "ko"), "ja"))
    _installs(monkeypatch)
    _setup(tmp_path, "ko")
    config = GUIConfigManager.load_config()
    assert config.language == "ja"
    assert config.language_stash["ko"]["dictionary_chain"][0].dict_id == "test-dict"


def test_a_language_the_profile_never_used_is_refused(catalog, test_config, tmp_path, monkeypatch) -> None:
    from anki_miner.cli.api.contract import ApiError

    GUIConfigManager.save_config(test_config)
    saved = GUIConfigManager.CONFIG_FILE.read_bytes()
    calls = _installs(monkeypatch)
    with pytest.raises(ApiError) as err:
        _setup(tmp_path, "ko")
    assert err.value.code == "BAD_ARGUMENTS" and "never been used" in err.value.message
    assert "settings-import FILE --language ko --name NAME" in err.value.message
    assert calls == [] and not (tmp_path / "progress.json").exists()
    assert GUIConfigManager.CONFIG_FILE.read_bytes() == saved


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
    _installs(monkeypatch, calls=order)
    outcome = _setup(tmp_path)
    assert outcome.result["language_pack"] == {"status": "installed", "message": None}
    assert order == ["pack", "syspath", "test-dict", "test-freq"]


def test_a_cancel_stops_before_the_next_item(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    cancel = threading.Event()
    cancel.set()
    calls = _installs(monkeypatch)
    outcome = _setup(tmp_path, cancel=cancel)
    assert outcome.cancelled and calls == []
    assert {r["status"] for r in outcome.result["resources"]} == {"not_attempted"}


def test_a_cancel_after_the_first_resource_keeps_it_switched_on(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    cancel = threading.Event()
    calls = _installs(monkeypatch)
    fake = api_setup.install_resource

    def then_cancel(spec, **kwargs):
        result = fake(spec, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(api_setup, "install_resource", then_cancel)
    outcome = _setup(tmp_path, cancel=cancel)
    assert outcome.cancelled and calls == ["test-dict"]
    assert [r["status"] for r in outcome.result["resources"]] == ["installed", "not_attempted"]
    assert GUIConfigManager.load_config().dictionary_chain[0].dict_id == "test-dict"


@pytest.mark.parametrize(("index", "downloaded"), [(None, True), ("junk", True), ("current", False)])
def test_installed_reads_the_slots_index(catalog, test_config, tmp_path, monkeypatch, index, downloaded) -> None:
    from anki_miner.services.dictionary.storage import SCHEMA_VERSION, create_index, write_meta

    GUIConfigManager.save_config(test_config)
    # The root the saved settings load with, which is not test_config's temp one.
    db = api_setup.settings.load_profile_config(None).dicts_root / "test-dict" / "index.sqlite"
    if index is not None:
        db.parent.mkdir(parents=True)
        if index == "junk":
            db.write_text("not a database", encoding="utf-8")
        else:
            create_index(db)
            write_meta(db, {"schema_version": str(SCHEMA_VERSION), "source_name": "Test dict"})
    calls = _installs(monkeypatch)
    outcome = _setup(tmp_path)
    assert ("test-dict" in calls) is downloaded
    assert outcome.result["resources"][0]["status"] == ("installed" if downloaded else "already_installed")
    assert GUIConfigManager.load_config().dictionary_chain[0].dict_id == "test-dict"


def test_a_dictionary_install_reports_its_entries(tmp_path) -> None:
    bar = api_setup._Progress(tmp_path / "progress.json", stages=1)
    bar._min_interval = 0.0
    bar.stage(1, DICT.id)
    reporter = api_setup.phase_reporter(DICT, bar.resource)
    reporter.downloading(20, 20, "")
    reporter.installing()
    reporter.importing(3, 10, "Inserted 1,234 entries")
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert (progress["item"], progress["done"], progress["total"]) == ("test-dict", 1234, 0)


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


# --- --speech-model, --model and --ocr ---------------------------------------------------------------


@pytest.fixture
def bare(monkeypatch):
    """No language pack and no resources: only what the flags add runs, with the language's real OCR."""
    from anki_miner.languages import registry

    def profile(code):
        real = registry.get_profile(code)
        return SimpleNamespace(
            catalog=(),
            unavailable_reason=None,
            capabilities=real.capabilities,
            ocr_recognizer=real.ocr_recognizer,
            asr_language=real.asr_language,
        )

    monkeypatch.setattr(api_setup, "get_profile", profile)
    monkeypatch.setattr(api_setup.packs, "load_pack", lambda code: None)


@pytest.fixture
def machine(monkeypatch):
    """A source install with the CT2 engine, no GPU and nothing downloaded; every install is recorded in order."""
    state = SimpleNamespace(
        engine=True,
        pack_supported=True,
        cuda=0,
        cuda_supported=True,
        cuda_installed=False,
        whisper_cpp=False,
        vulkan=0,
        models=set(),
        ggml=False,
        vad=False,
        calls=[],
    )
    monkeypatch.delattr(sys, "frozen", raising=False)
    engine, packs_ = api_setup._engine, api_setup.asr_pack_installer
    monkeypatch.setattr(engine, "available", lambda: state.engine)
    monkeypatch.setattr(engine, "cuda_device_count", lambda: state.cuda)
    monkeypatch.setattr(engine, "whisper_cpp_available", lambda: state.whisper_cpp)
    monkeypatch.setattr(engine, "vulkan_device_count", lambda: state.vulkan)
    monkeypatch.setattr(packs_, "asr_pack_supported", lambda: state.pack_supported)

    def install_pack(root, *, progress, cancelled_check):
        state.calls.append("asr_pack")
        progress(10, 10, "ASR pack (1/1): downloading")

    def on_syspath():
        state.calls.append("syspath")
        state.engine = True

    monkeypatch.setattr(packs_, "install_asr_pack", install_pack)
    monkeypatch.setattr(packs_, "ensure_asr_pack_on_syspath", on_syspath)
    models = api_setup.model_manager
    monkeypatch.setattr(models, "is_downloaded", lambda name, root: name in state.models)

    def download(name, root, cancel_event=None):
        state.calls.append(f"model:{name}")
        state.models.add(name)

    monkeypatch.setattr(models, "download", download)
    cuda = api_setup.cuda_pack_installer
    monkeypatch.setattr(cuda, "cuda_pack_supported", lambda: state.cuda_supported)
    monkeypatch.setattr(cuda, "is_installed", lambda root: state.cuda_installed)
    monkeypatch.setattr(cuda, "install_cuda_pack", lambda root, **kw: state.calls.append("cuda"))
    ggml = api_setup.ggml_model_installer
    monkeypatch.setattr(ggml, "is_ggml_downloaded", lambda name, root: state.ggml)
    monkeypatch.setattr(ggml, "is_vad_downloaded", lambda root: state.vad)
    monkeypatch.setattr(ggml, "install_ggml_model", lambda name, root, **kw: state.calls.append(f"ggml:{name}"))
    monkeypatch.setattr(ggml, "install_vad_model", lambda root, **kw: state.calls.append("vad"))
    return state


def _with(tmp_path, *, language="ja", cancel=None, **extras):
    return api_setup.run_setup(
        language, None, tmp_path / "progress.json", cancel or threading.Event(), api_setup.Extras(**extras)
    )


def test_without_the_flags_the_result_and_progress_are_unchanged(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    _installs(monkeypatch)
    outcome = _setup(tmp_path)
    assert list(outcome.result) == ["language", "profile", "language_pack", "resources"]
    assert json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))["stages"] == 2


def test_the_speech_model_comes_after_the_resources(catalog, machine, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    calls = _installs(monkeypatch, calls=machine.calls)
    outcome = _with(tmp_path, speech_model=True)
    assert calls == ["test-dict", "test-freq", "model:large-v3"]
    assert outcome.result["speech_model"] == {"name": "large-v3", "status": "installed", "message": None}
    assert "ocr" not in outcome.result and not outcome.failed
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert (progress["item"], progress["stage"], progress["stages"]) == ("speech_model", 3, 3)
    assert (progress["done"], progress["total"]) == (0, 0)  # the model download reports no byte count


@pytest.mark.parametrize(
    ("device", "cuda", "expected"),
    [
        ("auto", 1, ["asr_pack", "syspath", "model:large-v3", "cuda"]),  # CUDA wins over Vulkan
        ("auto", 0, ["asr_pack", "syspath", "model:large-v3", "ggml:large-v3", "vad"]),
        ("vulkan", 1, ["asr_pack", "syspath", "model:large-v3", "cuda", "ggml:large-v3", "vad"]),
        ("cpu", 1, ["asr_pack", "syspath", "model:large-v3", "cuda"]),  # the panel offers the pack for any device
        ("cuda", 0, ["asr_pack", "syspath", "model:large-v3"]),
    ],
)
def test_a_bundle_installs_the_engine_then_its_gpu_part(
    bare, machine, test_config, tmp_path, monkeypatch, device, cuda, expected
) -> None:
    GUIConfigManager.save_config(replace(test_config, asr_device=device))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    machine.engine, machine.cuda = False, cuda
    machine.whisper_cpp, machine.vulkan = True, 1
    outcome = _with(tmp_path, speech_model=True)
    assert machine.calls == expected
    assert outcome.result["speech_model"]["status"] == "installed"


def test_no_vulkan_device_no_whisper_cpp_files(bare, machine, test_config, tmp_path) -> None:
    GUIConfigManager.save_config(replace(test_config, asr_device="vulkan"))
    machine.whisper_cpp, machine.vulkan = True, 0
    _with(tmp_path, speech_model=True)
    assert machine.calls == ["model:large-v3"]


def test_what_is_there_already_is_not_downloaded(bare, machine, test_config, tmp_path) -> None:
    GUIConfigManager.save_config(test_config)
    machine.models, machine.cuda, machine.cuda_installed = {"large-v3"}, 1, True
    outcome = _with(tmp_path, speech_model=True)
    assert machine.calls == []
    assert outcome.result["speech_model"] == {"name": "large-v3", "status": "already_installed", "message": None}


@pytest.mark.parametrize(
    ("frozen", "supported", "message"),
    [
        (False, True, 'pip install "anki-miner[asr]"'),
        (True, False, "not available for this build"),
    ],
)
def test_an_engine_this_build_cannot_download_fails(
    bare, machine, test_config, tmp_path, monkeypatch, frozen, supported, message
) -> None:
    GUIConfigManager.save_config(test_config)
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    machine.engine, machine.pack_supported = False, supported
    outcome = _with(tmp_path, speech_model=True)
    assert machine.calls == [] and outcome.failed
    assert outcome.result["speech_model"]["status"] == "failed"
    assert message in outcome.result["speech_model"]["message"]


def test_model_is_installed_and_saved_as_the_profiles(bare, machine, test_config, tmp_path) -> None:
    GUIConfigManager.save_config(test_config)
    outcome = _with(tmp_path, speech_model=True, model="small")
    assert machine.calls == ["model:small"]
    assert outcome.result["speech_model"] == {"name": "small", "status": "installed", "message": None}
    assert GUIConfigManager.load_config().asr_model == "small"


def test_model_already_there_is_still_saved(bare, machine, test_config, tmp_path) -> None:
    GUIConfigManager.save_config(test_config)
    machine.models = {"small"}
    assert _with(tmp_path, speech_model=True, model="small").result["speech_model"]["status"] == "already_installed"
    assert GUIConfigManager.load_config().asr_model == "small"


@pytest.mark.parametrize("error", [SetupError, OSError])  # OSError: huggingface_hub's own (offline, HTTP)
def test_a_model_that_did_not_download_is_not_saved(bare, machine, test_config, tmp_path, monkeypatch, error) -> None:
    GUIConfigManager.save_config(test_config)

    def broken(name, root, cancel_event=None):
        raise error("huggingface.co did not answer")

    monkeypatch.setattr(api_setup.model_manager, "download", broken)
    outcome = _with(tmp_path, speech_model=True, model="small")
    assert outcome.failed and outcome.result["speech_model"] == {
        "name": "small",
        "status": "failed",
        "message": "huggingface.co did not answer",
    }
    assert GUIConfigManager.load_config().asr_model == "large-v3"


def test_a_model_that_cannot_transcribe_the_language_is_refused_before_it_downloads(
    bare, machine, test_config, tmp_path
) -> None:
    """Cantonese on small: saved, it would stop every transcription in the profile."""
    GUIConfigManager.save_config(replace(test_config, language="yue"))
    outcome = _with(tmp_path, language="yue", speech_model=True, model="small")
    assert machine.calls == [] and outcome.failed
    assert outcome.result["speech_model"]["status"] == "failed"
    assert "cannot transcribe" in outcome.result["speech_model"]["message"]
    assert GUIConfigManager.load_config().asr_model == "large-v3"


def test_a_cancel_during_the_model_download_leaves_it_not_attempted(
    bare, machine, test_config, tmp_path, monkeypatch
) -> None:
    GUIConfigManager.save_config(test_config)
    cancel = threading.Event()

    def interrupted(name, root, cancel_event=None):
        cancel.set()
        cancel_event.wait(5)  # the watcher passes the signal on; the download then promotes nothing

    monkeypatch.setattr(api_setup.model_manager, "download", interrupted)
    outcome = _with(tmp_path, cancel=cancel, speech_model=True, model="small", ocr=True)
    assert outcome.cancelled and not outcome.failed
    assert [outcome.result[k]["status"] for k in ("speech_model", "ocr", "video_ocr")] == ["not_attempted"] * 3
    assert GUIConfigManager.load_config().asr_model == "large-v3"


@pytest.fixture
def ocr(monkeypatch):
    """Nothing of either OCR installed; every install is recorded in order."""
    calls: list[str] = []
    state = SimpleNamespace(managed=False, onnxruntime=False, importable=False, calls=calls)
    monkeypatch.setattr(api_setup, "manga_ocr_problem", lambda config: "mokuro is not installed.")
    monkeypatch.setattr(api_setup, "video_ocr_problem", lambda config: "Video OCR is not installed.")
    monkeypatch.setattr(api_setup, "managed_mokuro_installed", lambda uv_root: state.managed)
    mi = api_setup.mokuro_installer

    def install_mokuro(bin_root, uv_root, *, status, progress, cancel_event):
        calls.append("mokuro")
        progress(20, 20, "uv")
        status("Preparing Python 3.12.14…")

    monkeypatch.setattr(mi, "install_mokuro", install_mokuro)
    monkeypatch.setattr(mi, "onnxruntime_installed", lambda uv_root: state.onnxruntime)
    monkeypatch.setattr(mi, "add_onnxruntime", lambda bin_root, uv_root, **kw: calls.append("venv-onnxruntime"))
    monkeypatch.setattr(api_setup.onnx_pack_installer, "onnxruntime_importable", lambda root: state.importable)
    monkeypatch.setattr(
        api_setup.onnx_pack_installer, "install_onnx_pack", lambda root, **kw: calls.append("onnx-pack")
    )

    def install_models(root, specs, *, progress, cancel_event):
        calls.append("models:" + ",".join(spec.filename for spec in specs))
        progress(7, 7, "")

    monkeypatch.setattr(api_setup.model_installer, "install_models", install_models)
    return state


def test_ocr_for_japanese_is_mokuro_then_video_ocr(bare, ocr, test_config, tmp_path) -> None:
    from anki_miner.services.ocr.model_installer import MEIKI_SPECS

    GUIConfigManager.save_config(test_config)
    outcome = _with(tmp_path, ocr=True)
    assert ocr.calls == ["mokuro", "onnx-pack", "models:" + ",".join(s.filename for s in MEIKI_SPECS)]
    assert outcome.result["ocr"] == {"status": "installed", "message": None}
    assert outcome.result["video_ocr"] == {"status": "installed", "message": None}
    assert "speech_model" not in outcome.result
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert (progress["item"], progress["stage"], progress["stages"], progress["done"]) == ("video_ocr", 2, 2, 7)


def test_ocr_for_a_recogniser_language_adds_its_runtime_and_model(bare, ocr, test_config, tmp_path) -> None:
    from anki_miner.services.ocr.model_installer import MEIKI_SPECS
    from anki_miner.services.ocr.recognizers import RECOGNIZERS

    GUIConfigManager.save_config(switch_language(switch_language(test_config, "ko"), "ja"))
    ocr.managed, ocr.importable = True, True  # a Japanese mokuro already in; onnxruntime importable for video OCR
    outcome = _with(tmp_path, language="ko", ocr=True)
    korean = RECOGNIZERS["korean"].model.filename
    assert ocr.calls == ["venv-onnxruntime", f"models:{korean}", f"models:{MEIKI_SPECS[0].filename},{korean}"]
    assert (outcome.result["ocr"]["status"], outcome.result["video_ocr"]["status"]) == ("installed", "installed")


def test_ocr_already_installed_downloads_nothing(bare, ocr, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    monkeypatch.setattr(api_setup, "manga_ocr_problem", lambda config: None)
    monkeypatch.setattr(api_setup, "video_ocr_problem", lambda config: None)
    outcome = _with(tmp_path, ocr=True)
    assert ocr.calls == [] and not outcome.failed
    assert outcome.result["ocr"]["status"] == outcome.result["video_ocr"]["status"] == "already_installed"


def test_ocr_for_a_language_without_it_fails(bare, ocr, test_config, tmp_path, monkeypatch) -> None:
    from anki_miner.cli.api import readiness

    GUIConfigManager.save_config(switch_language(switch_language(test_config, "vi"), "ja"))
    monkeypatch.setattr(api_setup, "manga_ocr_problem", readiness.manga_ocr_problem)
    monkeypatch.setattr(api_setup, "video_ocr_problem", readiness.video_ocr_problem)
    outcome = _with(tmp_path, language="vi", ocr=True)
    assert ocr.calls == [] and outcome.failed
    assert outcome.result["ocr"] == {"status": "failed", "message": "Manga OCR is not available for this language."}
    assert outcome.result["video_ocr"] == {
        "status": "failed",
        "message": "Video OCR is not available for this language.",
    }


def test_a_failed_mokuro_install_still_tries_video_ocr(bare, ocr, test_config, tmp_path, monkeypatch) -> None:
    from anki_miner.exceptions import SetupError

    GUIConfigManager.save_config(test_config)

    def broken(*args, **kwargs):
        raise SetupError("uv pip install failed (exit code 1).")

    monkeypatch.setattr(api_setup.mokuro_installer, "install_mokuro", broken)
    outcome = _with(tmp_path, ocr=True)
    assert outcome.failed and outcome.result["ocr"]["message"] == "uv pip install failed (exit code 1)."
    assert outcome.result["video_ocr"]["status"] == "installed"


def test_a_cancel_file_beside_progress_stops_setup(bare, ocr, test_config, tmp_path, monkeypatch) -> None:
    from anki_miner.exceptions import OperationCancelled

    GUIConfigManager.save_config(test_config)

    def cancelled_by_file(bin_root, uv_root, *, status, progress, cancel_event):
        (tmp_path / "cancel").touch()
        assert cancel_event.wait(5)
        raise OperationCancelled("mokuro installation cancelled")

    monkeypatch.setattr(api_setup.mokuro_installer, "install_mokuro", cancelled_by_file)
    outcome = _with(tmp_path, ocr=True)
    assert outcome.cancelled and not outcome.failed
    assert outcome.result["ocr"]["status"] == outcome.result["video_ocr"]["status"] == "not_attempted"
    assert ocr.calls == [] and not (tmp_path / "cancel").exists()


def test_an_earlier_calls_cancel_and_progress_are_cleared(catalog, test_config, tmp_path, monkeypatch) -> None:
    GUIConfigManager.save_config(test_config)
    (tmp_path / "cancel").touch()
    (tmp_path / "progress.json").write_text('{"stale": true}', encoding="utf-8")
    calls = _installs(monkeypatch)
    outcome = _setup(tmp_path)
    assert not outcome.cancelled and calls == ["test-dict", "test-freq"]
    assert "stale" not in json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))


def test_setup_passes_the_flags(verdict, tmp_path, monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(api_setup, "run_setup", lambda *args: seen.append(args) or _outcome())
    v = verdict(
        "setup",
        "--language",
        "ja",
        "--progress",
        str(tmp_path / "p.json"),
        "--speech-model",
        "--model",
        "small",
        "--ocr",
    )
    assert v["ok"] and seen[0][4] == api_setup.Extras(speech_model=True, model="small", ocr=True)
    verdict("setup", "--language", "ja", "--progress", str(tmp_path / "p.json"))
    assert seen[1][4] == api_setup.Extras()


@pytest.mark.parametrize("extra", [("--model", "small"), ("--speech-model", "--model", "medium")])
def test_a_bad_model_is_bad_arguments(verdict, tmp_path, monkeypatch, extra) -> None:
    monkeypatch.setattr(api_setup, "run_setup", lambda *args: pytest.fail("ran"))
    v = verdict("setup", "--language", "ja", "--progress", str(tmp_path / "p.json"), *extra)
    assert v["error"] == "BAD_ARGUMENTS"


def test_the_model_choices_are_the_known_models() -> None:
    from anki_miner.cli import api
    from anki_miner.services.asr.model_manager import KNOWN_MODELS

    assert set(api.SPEECH_MODELS) == KNOWN_MODELS
