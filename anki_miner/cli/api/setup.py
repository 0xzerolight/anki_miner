"""setup: the setup wizard's downloads for one mining language, without the window (API.md, "setup").

The language pack when the language needs one, then the language's
recommended resources (the wizard's Recommended Resources page), switched on
the way the wizard's activation switches them on (apply_download_summary).
``--speech-model`` then installs what Settings -> Transcription & Alignment
installs on this machine, and ``--ocr`` what Utilities -> Manga OCR and
-> Video OCR install for the language. The caller holds the run lock with no
window open: the downloads replace indexes a window may hold open, and a
window would undo the settings write.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from anki_miner.cli.api import settings, settings_write
from anki_miner.cli.api.contract import BAD_ARGUMENTS, ApiError
from anki_miner.cli.api.readiness import manga_ocr_problem, video_ocr_problem
from anki_miner.cli.api.runfolder import CANCEL, CancelWatcher, ProgressFile
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.gui.utils.resource_setup import apply_download_summary
from anki_miner.gui.workers.resource_download_worker import (
    ResourceDownloadResult,
    ResourceDownloadSummary,
    ResourcePhase,
    ResourceProgress,
    install_resource,
    phase_reporter,
    pinned_slot,
)
from anki_miner.languages.registry import config_language, get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services import language_pack_installer as packs
from anki_miner.services import mokuro_installer
from anki_miner.services._sqlite_index import StoreFamily, validate_index_schema_cached
from anki_miner.services.asr import (
    _engine,
    asr_pack_installer,
    cuda_pack_installer,
    ggml_model_installer,
    model_manager,
    onnx_pack_installer,
)
from anki_miner.services.ocr import model_installer
from anki_miner.services.ocr.recognizers import RECOGNIZERS
from anki_miner.services.resource_catalog import ResourceSpec
from anki_miner.services.video_ocr import runtime
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.mokuro_resolver import managed_mokuro_installed

logger = logging.getLogger(__name__)

#: kind -> (the config field holding its root, its index family)
_FAMILIES: dict[str, tuple[str, StoreFamily]] = {
    "dict": ("dicts_root", "dictionary"),
    "freq": ("freqs_root", "frequency"),
    "pitch": ("pitch_root", "pitch"),
}
#: The panel's words for a build that cannot download the engine (subtitles_settings_panel._apply_engine_state).
_NO_ENGINE_HERE = "Local transcription is not available for this build."
_NO_ENGINE_SOURCE = 'The speech engine is not installed. Install it with: pip install "anki-miner[asr]"'


@dataclass(frozen=True)
class Extras:
    """What setup installs besides the wizard's downloads: ``--speech-model [--model NAME]`` and ``--ocr``."""

    speech_model: bool = False
    #: The model to install and save as the profile's asr_model; None: the profile's own.
    model: str | None = None
    ocr: bool = False

    def items(self) -> tuple[str, ...]:
        """Their ``result`` keys and progress items, in install order."""
        return ("speech_model",) * self.speech_model + ("ocr", "video_ocr") * self.ocr


NO_EXTRAS = Extras()


@dataclass(frozen=True)
class SetupOutcome:
    result: dict[str, object]
    cancelled: bool
    failed: bool


@dataclass
class _Item:
    """One line of ``result``."""

    status: str = "not_attempted"
    message: str | None = None

    def done(self, status: str, message: str | None = None) -> None:
        self.status, self.message = status, message


class _Progress(ProgressFile):
    """``--progress FILE``: ProgressFile's throttled atomic writes, naming the item in hand instead of a run.

    Stages count the language pack, each resource, then the speech model, Manga
    OCR and Video OCR when asked; done/total are bytes while downloading. While
    installing they are the counted pass of a frequency or pitch list (files or
    terms), and for a dictionary the entries written, with total 0: the reporter
    knows no entry total ahead. A step that reports no count (the Whisper
    model's download, mokuro's uv steps) shows 0/0.
    """

    def __init__(self, path: Path, stages: int) -> None:
        super().__init__(path.parent, "", path=path)
        self._stages = stages
        self._state = {"schema": 1, "item": None, "stage": 0, "stages": stages, "done": 0, "total": 0}
        self._write(force=True)

    def stage(self, index: int, item: str) -> None:
        self._state["item"] = item
        self.on_stage(index, self._stages, item)

    def count(self, done: int, total: int) -> None:
        if total != self._state["total"]:
            self.on_start(total, "")
        self.on_progress(done, "")

    def download(self, done: int, total: int, _message: str) -> None:
        """An installer's ``(downloaded, total, message)`` callback."""
        self.count(done, total)

    def uncounted(self) -> None:
        """A step with no count of its own starts: 0/0 (written only when the last step had one)."""
        if self._state["done"] or self._state["total"]:
            self.on_start(0, "")

    def resource(self, event: ResourceProgress) -> None:
        if event.phase is ResourcePhase.DOWNLOADING:
            self.count(event.downloaded, event.total_bytes or 0)
        elif event.entries is not None:  # only a dictionary counts entries
            self.count(event.entries, 0)
        else:
            self.count(event.step or 0, event.steps or 0)


def run_setup(
    language: str,
    profile_id: str | None,
    progress_path: Path,
    cancel: threading.Event,
    extras: Extras = NO_EXTRAS,
) -> SetupOutcome:
    """Install *language*'s pack and resources, switched on for *profile_id* (None: the active profile), then *extras*.

    A ``cancel`` file beside *progress_path* stops it as *cancel* (a signal) does.
    """
    if not progress_path.parent.is_dir():
        raise ApiError(BAD_ARGUMENTS, f"The folder for --progress does not exist: {progress_path.parent}")
    # An earlier call's progress and unconsumed cancel; a cancel created from here on stops this call.
    progress_path.unlink(missing_ok=True)
    (progress_path.parent / CANCEL).unlink(missing_ok=True)
    stored = settings.load_profile_config(profile_id)
    config = settings.with_language(stored, language, code=BAD_ARGUMENTS)
    if language != stored.language and language not in stored.language_stash:
        # The window treats a language with no stash entry as a first visit and then
        # offers its setup and deck checklist (language_switch, main_window); parking
        # these resources would create the entry and spend that visit, as the
        # wizard's _single_switch avoids.
        where = f"profile {profile_id!r}" if profile_id is not None else "the active profile"
        raise ApiError(
            BAD_ARGUMENTS,
            f"{language!r} has never been used in {where}. Run setup on a profile that mines {language!r}, "
            f"e.g. one made with settings-import FILE --language {language} --name NAME (FILE may be {{}}).",
        )
    # The wizard's selected_specs: the catalogue minus the other regional variety.
    specs = [s for s in get_profile(language).catalog if not s.variant or s.variant == config.script_variant]
    needs_pack = packs.load_pack(language) is not None
    model = extras.model or config.asr_model
    bar = _Progress(progress_path, stages=len(specs) + int(needs_pack) + len(extras.items()))
    pack = _Item()
    items = [_Item() for _ in specs]
    added = {name: _Item() for name in extras.items()}
    summary = ResourceDownloadSummary(
        requested_count=len(specs),
        dicts_root=config.dicts_root,
        freqs_root=config.freqs_root,
        pitch_root=config.pitch_root,
    )
    model_ready = False
    with CancelWatcher(progress_path.parent, cancel) as stop:

        def halted() -> bool:
            # The watcher passes a signal on at its next poll; between items it counts at once.
            return cancel.is_set() or stop.is_set()

        if not halted():
            if needs_pack:
                bar.stage(1, "language_pack")
            _language_pack(language, pack, bar, stop)
        download_dir = Path(tempfile.mkdtemp(prefix="anki_miner_dl_"))
        try:
            for index, (spec, item) in enumerate(zip(specs, items, strict=True), start=int(needs_pack) + 1):
                if halted():
                    break
                bar.stage(index, spec.id)
                _resource(spec, item, config, language, summary, download_dir, bar, stop)
        finally:
            shutil.rmtree(download_dir, ignore_errors=True)
        for index, name in enumerate(added, start=int(needs_pack) + len(specs) + 1):
            if halted():
                break
            bar.stage(index, name)
            if name == "speech_model":
                model_ready = _speech_model(config, model, added[name], bar, stop)
            elif name == "ocr":
                _manga_ocr(config, added[name], bar, stop)
            else:
                _video_ocr(config, added[name], bar, stop)
    # --model is the profile's model from now on, once that model is on disk (as a resource is switched on once in).
    switched = model_ready and model != config.asr_model
    if switched:
        config = replace(config, asr_model=model)
    written = (
        _save(profile_id, stored, config, summary)
        if summary.succeeded or switched
        else profile_id or settings.active_profile_id()
    )
    result: dict[str, object] = {
        "language": language,
        "profile": written,
        "language_pack": {"status": pack.status, "message": pack.message},
        "resources": [
            {"id": s.id, "kind": s.kind, "name": s.display_name, "status": i.status, "message": i.message}
            for s, i in zip(specs, items, strict=True)
        ],
    }
    for name, item in added.items():
        named = {"name": model} if name == "speech_model" else {}
        result[name] = {**named, "status": item.status, "message": item.message}
    failed = any(i.status == "failed" for i in (pack, *items, *added.values()))
    log_summary(
        logger,
        "API setup",
        language=language,
        profile=written,
        language_pack=pack.status,
        resources=[f"{s.id}={i.status}" for s, i in zip(specs, items, strict=True)],
        extras=[f"{name}={item.status}" for name, item in added.items()],
        cancelled=halted(),
    )
    return SetupOutcome(result=result, cancelled=halted(), failed=failed)


def _language_pack(code: str, item: _Item, bar: _Progress, cancel: threading.Event) -> None:
    """The wizard's language page: download the pack when the language cannot mine without it."""
    probe = get_profile(code).unavailable_reason
    reason = probe() if probe is not None else None
    if packs.load_pack(code) is None:
        if reason:
            item.done("failed", reason)
        else:
            item.done("not_needed")
        return
    if not reason:
        item.done("already_installed")
        return
    if not packs.pack_supported(code) or packs.is_installed(code):
        # Nothing this machine can download, or a pack installed yet unusable (a broken install).
        item.done("failed", reason)
        return
    try:
        packs.install_language_pack(
            code,
            packs.language_pack_root(code),
            progress=bar.download,
            cancelled_check=cancel.is_set,
        )
    except OperationCancelled:
        return
    except SetupError as exc:
        item.done("failed", str(exc))
        return
    # Before any resource: a word-count list is lemmatised with this language's tagger.
    packs.ensure_language_packs_on_syspath()
    item.done("installed")


def _speech_model(config: AnkiMinerConfig, name: str, item: _Item, bar: _Progress, cancel: threading.Event) -> bool:
    """Settings -> Transcription & Alignment on this machine, in its order: the engine, model *name*, its GPU part.

    The engine pack only where the build downloads it (a frozen bundle on a
    supported platform without the CT2 engine); the GPU part as the panel offers
    it: the CUDA pack for an NVIDIA GPU, the whisper.cpp files where the device
    routes there. Returns whether the model is on disk.
    """
    root = config.asr_models_root
    installed = False
    model_ready = False
    try:
        if not _engine.available():
            if not (getattr(sys, "frozen", False) and asr_pack_installer.asr_pack_supported()):
                item.done("failed", _NO_ENGINE_HERE if getattr(sys, "frozen", False) else _NO_ENGINE_SOURCE)
                return False
            asr_pack_installer.install_asr_pack(
                asr_pack_installer.asr_pack_root(), progress=bar.download, cancelled_check=cancel.is_set
            )
            # Before the model: its download goes through the engine (the panel's own order, app._connect_asr_pack_download).
            asr_pack_installer.ensure_asr_pack_on_syspath()
            installed = True
        if not model_manager.is_downloaded(name, root):
            bar.uncounted()  # one blocking download with no byte count, cancelled only before and after it
            model_manager.download(name, root, cancel_event=cancel)
            if cancel.is_set():  # nothing was promoted
                return False
            installed = True
        model_ready = True
        if (
            cuda_pack_installer.cuda_pack_supported()
            and _engine.cuda_device_count() > 0
            and not cuda_pack_installer.is_installed(config.cuda_libs_root)
        ):
            cuda_pack_installer.install_cuda_pack(config.cuda_libs_root, progress=bar.download, cancel_event=cancel)
            installed = True
        if _routes_to_whisper_cpp(config.asr_device):
            if not ggml_model_installer.is_ggml_downloaded(name, root):
                ggml_model_installer.install_ggml_model(name, root, progress=bar.download, cancel_event=cancel)
                installed = True
            if not ggml_model_installer.is_vad_downloaded(root):
                ggml_model_installer.install_vad_model(root, progress=bar.download, cancel_event=cancel)
                installed = True
    except SetupError as exc:  # OperationCancelled too: then the item stays not_attempted
        if not cancel.is_set():
            item.done("failed", str(exc))
        return model_ready
    if not cancel.is_set():
        item.done("installed" if installed else "already_installed")
    return model_ready


def _routes_to_whisper_cpp(device: str) -> bool:
    """The transcriber's device cascade before its file checks (transcriber._use_whisper_cpp_engine).

    ``vulkan``, or ``auto`` without a CUDA GPU (CUDA wins), with the whisper.cpp
    backend and a Vulkan device; ``cpu`` and ``cuda`` never.
    """
    if device not in ("vulkan", "auto") or (device == "auto" and _engine.cuda_device_count() > 0):
        return False
    return _engine.whisper_cpp_available() and _engine.vulkan_device_count() > 0


def _manga_ocr(config: AnkiMinerConfig, item: _Item, bar: _Progress, cancel: threading.Event) -> None:
    """Utilities -> Manga OCR's install for the language (install_worker.mokuro_install_task).

    mokuro, then for a language read by a PP-OCR recogniser onnxruntime in
    mokuro's environment and the recogniser's model.
    """
    problem = manga_ocr_problem(config)
    if problem is None:
        item.done("already_installed")
        return
    profile = get_profile(config_language(config))
    if "manga_ocr" not in profile.capabilities:
        item.done("failed", problem)
        return
    recognizer = RECOGNIZERS[profile.ocr_recognizer] if profile.ocr_recognizer else None
    try:
        if not managed_mokuro_installed(config.uv_root):
            mokuro_installer.install_mokuro(
                config.bin_root,
                config.uv_root,
                # uv's own steps report status lines, not a count.
                status=lambda _line: bar.uncounted(),
                progress=bar.download,
                cancel_event=cancel,
            )
        if recognizer is not None:
            if not mokuro_installer.onnxruntime_installed(config.uv_root):
                mokuro_installer.add_onnxruntime(
                    config.bin_root,
                    config.uv_root,
                    status=lambda _line: bar.uncounted(),
                    progress=bar.download,
                    cancel_event=cancel,
                )
            model_installer.install_models(
                config.video_ocr_models_root, (recognizer.model,), progress=bar.download, cancel_event=cancel
            )
    except SetupError as exc:
        if not cancel.is_set():
            item.done("failed", str(exc))
        return
    if not cancel.is_set():
        item.done("installed")


def _video_ocr(config: AnkiMinerConfig, item: _Item, bar: _Progress, cancel: threading.Event) -> None:
    """Utilities -> Video OCR's install (install_worker.video_ocr_install_task): its runtime when missing, its models."""
    problem = video_ocr_problem(config)
    if problem is None:
        item.done("already_installed")
        return
    profile = get_profile(config_language(config))
    if "video_ocr" not in profile.capabilities:
        item.done("failed", problem)
        return
    try:
        if not onnx_pack_installer.onnxruntime_importable(config.onnx_pack_root):
            onnx_pack_installer.install_onnx_pack(config.onnx_pack_root, progress=bar.download, cancel_event=cancel)
        model_installer.install_models(
            config.video_ocr_models_root,
            runtime.required_models(profile),
            progress=bar.download,
            cancel_event=cancel,
        )
    except SetupError as exc:
        if not cancel.is_set():
            item.done("failed", str(exc))
        return
    if not cancel.is_set():
        item.done("installed")


def _resource(
    spec: ResourceSpec,
    item: _Item,
    config: AnkiMinerConfig,
    language: str,
    summary: ResourceDownloadSummary,
    download_dir: Path,
    bar: _Progress,
    cancel: threading.Event,
) -> None:
    slot = pinned_slot(spec)
    if slot is not None and _installed(spec, slot, config):
        item.done("already_installed")
        if not _chained(spec, slot, config):
            summary.results.append(_present(spec, slot))
        return
    result = install_resource(
        spec,
        dicts_root=config.dicts_root,
        freqs_root=config.freqs_root,
        pitch_root=config.pitch_root,
        download_dir=download_dir,
        language=language,
        reporter=phase_reporter(spec, bar.resource),
        cancelled=cancel.is_set,
    )
    if result is None:  # cancelled: stays not_attempted
        return
    summary.results.append(result)
    if result.ok:
        item.done("installed")
    else:
        item.done("failed", result.detail)


def _installed(spec: ResourceSpec, slot: str, config: AnkiMinerConfig) -> bool:
    """Whether *slot* already holds a current index (an outdated one is downloaded again)."""
    root_field, family = _FAMILIES[spec.kind]
    index = getattr(config, root_field) / slot / "index.sqlite"
    return index.is_file() and validate_index_schema_cached(index, family)


def _chained(spec: ResourceSpec, slot: str, config: AnkiMinerConfig) -> bool:
    """Whether the profile already uses *slot*: in its chain AND switched on (a disabled entry is switched back on)."""
    if spec.kind == "dict":
        return any(entry.dict_id == slot and entry.enabled for entry in config.dictionary_chain)
    chain = config.frequency_chain if spec.kind == "freq" else config.pitch_chain
    return any(entry.source_id == slot and entry.enabled for entry in chain)


def _present(spec: ResourceSpec, slot: str) -> ResourceDownloadResult:
    """An installed resource the profile does not use yet, for apply_download_summary to switch on."""
    return ResourceDownloadResult(
        spec_id=spec.id,
        kind=spec.kind,
        display_name=spec.display_name,
        url=spec.url,
        ok=True,
        detail="",
        dict_id=slot if spec.kind == "dict" else None,
        source_id=None if spec.kind == "dict" else slot,
    )


def _save(
    profile_id: str | None, stored: AnkiMinerConfig, config: AnkiMinerConfig, summary: ResourceDownloadSummary
) -> str | None:
    updated = apply_download_summary(config, summary)
    if updated.language != stored.language:
        # The profile keeps mining its own language; these resources wait in the other's settings.
        updated = switch_language(updated, stored.language)
    return settings_write.save_profile(profile_id, updated)
