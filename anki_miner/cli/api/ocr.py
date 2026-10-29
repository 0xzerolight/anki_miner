"""ocr: mokuro on manga volumes, ready for mine (API.md, "ocr").

Per episode, what Utilities -> Manga OCR does for one volume (a folder of page
images, a ``.cbz`` or a ``.zip``): mokuro writes ``<volume>.mokuro`` beside
it, replacing one already there. ``ocr-<n>.json`` names that file and the
pages it holds; the run keeps no ``ocr-<n>/`` folder. Nothing goes to Anki and
no lock is held: a run writes only its run folder, the ``.mokuro`` and
mokuro's page cache (``_ocr/`` beside the volume).
"""

from __future__ import annotations

import json
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath

from anki_miner.cli.api import files, settings
from anki_miner.cli.api.contract import BAD_RUN_FILE, CANCELLED, OCR_FAILED, SETUP_ERROR, ApiError
from anki_miner.cli.api.files import JobsFile
from anki_miner.cli.api.jobs import Run, job_runs
from anki_miner.cli.api.readiness import manga_ocr_problem
from anki_miner.cli.api.runfolder import ProgressFile
from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import SetupError
from anki_miner.exceptions.mokuro import MokuroError, MokuroNotFoundError
from anki_miner.services.mokuro_runner import MokuroOptions, MokuroRunnerService, MokuroStatus
from anki_miner.services.mokuro_volumes import (
    MOKURO_ARCHIVE_EXTENSIONS,
    MOKURO_IMAGE_EXTENSIONS,
    MokuroVolume,
    mokuro_output_path,
)
from anki_miner.services.reading._util import MAX_MOKURO_JSON_BYTES, read_text_capped
from anki_miner.utils.file_utils import is_junk_path

_CANCELLED = "The run was cancelled."
_NOT_A_VOLUME = "{where}.volume must be an existing folder of page images, .cbz or .zip: {path}"


@dataclass(frozen=True)
class OcrEpisode:
    run_id: str
    volume: Path


def _ocr_episode(raw: object, where: str) -> OcrEpisode:
    obj = files.json_object(raw, where)
    files.check_keys(obj, frozenset({"run_id", "volume"}), frozenset({"run_id", "volume"}), where)
    volume = files.abs_path(files.text(obj["volume"], f"{where}.volume"))
    if not (volume.is_dir() or (volume.suffix.lower() in MOKURO_ARCHIVE_EXTENSIONS and volume.is_file())):
        raise files.bad(_NOT_A_VOLUME.format(where=where, path=volume))
    return OcrEpisode(files.valid_run_id(obj["run_id"], f"{where}.run_id"), volume)


def parse_ocr_file(data: object) -> JobsFile[OcrEpisode]:
    return files.parse_jobs_file(data, "ocr", _ocr_episode)


def call(ocr_file: Path, cancel_all: threading.Event) -> list[dict[str, object]]:
    """``ocr OCR_FILE``: the file checked, then its runs."""
    return ocr_runs(parse_ocr_file(files.read_json_file(ocr_file)), cancel_all)


def ocr_runs(job: JobsFile[OcrEpisode], cancel_all: threading.Event) -> list[dict[str, object]]:
    """Every volume of *job* read in turn; an ocr-<n>.json for each whose ``.mokuro`` was written."""
    return job_runs(job, "ocr", cancel_all, partial(_prepare, job), _ocr_one)


def _prepare(job: JobsFile[OcrEpisode]) -> AnkiMinerConfig:
    config = settings.with_language(settings.load_profile_config(job.profile), job.language, code=BAD_RUN_FILE)
    if (problem := manga_ocr_problem(config)) is not None:
        raise ApiError(SETUP_ERROR, problem)
    return config


def _ocr_one(config: AnkiMinerConfig, episode: OcrEpisode, run: Run) -> dict[str, object]:
    source = episode.volume
    output = mokuro_output_path(source)
    redo = output.is_file()
    # The tab's options: its GPU box is the profile's mokuro_use_gpu. A volume read before is
    # redone from its pages, as with the tab's Redo box; a new one resumes mokuro's page cache.
    options = MokuroOptions(force_cpu=not config.mokuro_use_gpu, no_cache=redo)
    run.progress.on_stage(1, 1, "ocr")
    run.progress.on_start(100, "")
    try:
        result = MokuroRunnerService(config).process_volume(
            MokuroVolume(source=source, output=output, already_processed=redo),
            options,
            progress_cb=_percent(run.progress),
            cancel_event=run.cancel,
        )
    except MokuroNotFoundError as exc:  # a MokuroError: caught first
        raise ApiError(SETUP_ERROR, str(exc)) from exc
    except MokuroError as exc:
        raise ApiError(OCR_FAILED, str(exc)) from exc
    if result.status is MokuroStatus.CANCELLED:
        raise ApiError(CANCELLED, _CANCELLED)
    pages = _pages_written(output)
    return {"mokuro_file": str(output), "pages": pages, "pages_failed": max(0, page_images(source) - pages)}


def _percent(progress: ProgressFile) -> Callable[[str, float | None], None]:
    """mokuro's page count as done/total in percent; model loading and downloads have none."""

    def report(_message: str, fraction: float | None) -> None:
        if fraction is not None:
            progress.on_progress(round(max(0.0, min(1.0, fraction)) * 100), "")

    return report


def _pages_written(mokuro_file: Path) -> int:
    try:
        data = json.loads(read_text_capped(mokuro_file, MAX_MOKURO_JSON_BYTES, ".mokuro file"))
    except (SetupError, OSError, ValueError) as exc:
        raise ApiError(OCR_FAILED, f"mokuro wrote {mokuro_file.name}, but it cannot be read: {exc}") from exc
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list):
        raise ApiError(OCR_FAILED, f"mokuro wrote {mokuro_file.name} without its pages.")
    return len(pages)


def page_images(volume: Path) -> int:
    """The page images mokuro reads in *volume* (``Volume.get_img_paths``: one per path without its
    suffix), OS junk aside; 0 when it cannot be listed."""
    try:
        if volume.is_dir():
            names = [path.relative_to(volume).as_posix() for path in volume.rglob("*") if path.is_file()]
        else:
            with zipfile.ZipFile(volume) as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return 0
    pages = (PurePosixPath(name) for name in names if not is_junk_path(name))
    return len({page.with_suffix("") for page in pages if page.suffix.lower() in MOKURO_IMAGE_EXTENSIONS})
