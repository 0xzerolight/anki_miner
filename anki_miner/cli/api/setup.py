"""setup: the setup wizard's downloads for one mining language, without the window (API.md, "setup").

The language pack when the language needs one, then the language's
recommended resources (the wizard's Recommended Resources page), switched on
the way the wizard's activation switches them on (apply_download_summary). The
caller holds the run lock with no window open: the downloads replace indexes a
window may hold open, and a window would undo the settings write.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from anki_miner.cli.api import settings, settings_write
from anki_miner.cli.api.contract import BAD_ARGUMENTS, ApiError
from anki_miner.cli.api.runfolder import ProgressFile
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
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.services import language_pack_installer as packs
from anki_miner.services._sqlite_index import StoreFamily, validate_index_schema_cached
from anki_miner.services.resource_catalog import ResourceSpec
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

#: kind -> (the config field holding its root, its index family)
_FAMILIES: dict[str, tuple[str, StoreFamily]] = {
    "dict": ("dicts_root", "dictionary"),
    "freq": ("freqs_root", "frequency"),
    "pitch": ("pitch_root", "pitch"),
}


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

    Stages count the language pack and each resource; done/total are bytes
    while downloading, then steps while installing.
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

    def resource(self, event: ResourceProgress) -> None:
        if event.phase is ResourcePhase.DOWNLOADING:
            self.count(event.downloaded, event.total_bytes or 0)
        else:
            self.count(event.step or 0, event.steps or 0)


def run_setup(language: str, profile_id: str | None, progress_path: Path, cancel: threading.Event) -> SetupOutcome:
    """Install *language*'s pack and resources, switched on for *profile_id* (None: the active profile)."""
    if not progress_path.parent.is_dir():
        raise ApiError(BAD_ARGUMENTS, f"The folder for --progress does not exist: {progress_path.parent}")
    stored = settings.load_profile_config(profile_id)
    config = settings.with_language(stored, language, code=BAD_ARGUMENTS)
    # The wizard's selected_specs: the catalogue minus the other regional variety.
    specs = [s for s in get_profile(language).catalog if not s.variant or s.variant == config.script_variant]
    needs_pack = packs.load_pack(language) is not None
    bar = _Progress(progress_path, stages=len(specs) + int(needs_pack))
    pack = _Item()
    items = [_Item() for _ in specs]
    if not cancel.is_set():
        if needs_pack:
            bar.stage(1, "language_pack")
        _language_pack(language, pack, bar, cancel)
    summary = ResourceDownloadSummary(
        requested_count=len(specs),
        dicts_root=config.dicts_root,
        freqs_root=config.freqs_root,
        pitch_root=config.pitch_root,
    )
    download_dir = Path(tempfile.mkdtemp(prefix="anki_miner_dl_"))
    try:
        for index, (spec, item) in enumerate(zip(specs, items, strict=True), start=int(needs_pack) + 1):
            if cancel.is_set():
                break
            bar.stage(index, spec.id)
            _resource(spec, item, config, language, summary, download_dir, bar, cancel)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)
    written = (
        _save(profile_id, stored, config, summary) if summary.succeeded else profile_id or settings.active_profile_id()
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
    failed = any(i.status == "failed" for i in (pack, *items))
    log_summary(
        logger,
        "API setup",
        language=language,
        profile=written,
        language_pack=pack.status,
        resources=[f"{s.id}={i.status}" for s, i in zip(specs, items, strict=True)],
        cancelled=cancel.is_set(),
    )
    return SetupOutcome(result=result, cancelled=cancel.is_set(), failed=failed)


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
            progress=lambda done, total, _message: bar.count(done, total),
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
