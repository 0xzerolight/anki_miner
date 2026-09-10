"""Run mokuro on one manga volume — the engine behind Utilities → Manga OCR.

One ``run_supervised`` subprocess per volume (``FileQueueWorker`` item). mokuro
(python-fire CLI, ``mokuro/run.py``) is awkward to supervise:

* it ALWAYS exits 0 — a bad path, "no volumes", and a volume that raised are
  all just log lines. Success here is its own receipt line
  ``Processed successfully: 1/1`` plus the ``.mokuro`` file actually existing;
* it prompts ``Continue? [yes/no]`` unless ``--disable_confirmation`` (stdin is
  DEVNULL, so the prompt would EOF);
* per-page progress is a tqdm bar redrawn with ``\\r`` — hence
  ``treat_cr_as_newline=True``;
* flags are passed as explicit ``--flag=Value`` AFTER the positional path so
  fire never consumes the path as a flag value;
* ``--legacy_html=False`` (no ``.html``, no in-place unzip of archives) and
  ``--ignore_errors=True`` (a bad page must not fail the volume) always.

The child runs with ``PYTHONUTF8=1``/``PYTHONIOENCODING=utf-8`` so Japanese
paths in its output decode under the supervisor's UTF-8 reader on Windows.
"""

from __future__ import annotations

import collections
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions.mokuro import MokuroError, MokuroNotFoundError
from anki_miner.services.mokuro_volumes import MokuroVolume
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.mokuro_resolver import resolve_mokuro, scrubbed_python_env
from anki_miner.utils.process_supervisor import SupervisedState, run_supervised

logger = logging.getLogger(__name__)

#: A few hundred pages on CPU, plus on the very first run the ~550 MB of models
#: mokuro downloads itself. Generous on purpose; Cancel is the fast path out.
#: The TIMED_OUT MokuroError names this in hours; change both together.
_VOLUME_TIMEOUT_S = 6 * 60 * 60

# Stays a plain module constant: a module-level QCoreApplication.translate would
# evaluate at import, before the app installs the translator, and cache English.
MOKURO_MISSING_HINT = (
    "mokuro is not installed." " Install it in Settings → Transcription & Alignment → Manga OCR, or set its path there."
)

_PAGES_RE = re.compile(r"Processing pages\.\.\.:\s*\d+%\|[^|]*\|\s*(\d+)/(\d+)")
_PROCESSED_RE = re.compile(r"Processed successfully: (\d+)/(\d+)")
_DEVICE_RE = re.compile(r"Initializing text detector, using device (\w+)")
_MODEL_DOWNLOAD_RE = re.compile(r"Downloading https?://\S+")
_VOLUME_ERROR_RE = re.compile(r"Error while processing ")
_BAD_INPUT_RE = re.compile(r"Invalid path: |Found no paths to process")


def is_progress_only(line: str) -> bool:
    """True for a tqdm page tick — kept out of the failure tail (see media_downloader)."""
    return "Processing pages..." in line and "%|" in line


class MokuroStatus(Enum):
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class MokuroOptions:
    """One run's resolved options; the tab maps its widgets to this."""

    force_cpu: bool = False
    no_cache: bool = False


@dataclass(frozen=True)
class MokuroResult:
    status: MokuroStatus
    output_path: Path | None


class MokuroRunnerService:
    """Run mokuro on one volume. Stateless."""

    def __init__(self, config: AnkiMinerConfig) -> None:
        self._config = config

    def process_volume(
        self,
        volume: MokuroVolume,
        options: MokuroOptions,
        progress_cb: Callable[[str, float | None], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> MokuroResult:
        """OCR *volume* in place, writing ``volume.output``.

        Raises:
            MokuroNotFoundError: the executable cannot be spawned.
            MokuroError: timeout, nonzero exit, mokuro's own error lines, or no
                ``.mokuro`` written despite a clean exit.
        """
        logger.info("mokuro run starting: %s -> %s", volume.source, volume.output)
        tail: collections.deque[str] = collections.deque(maxlen=50)
        seen: dict[str, object] = {"processed": None, "volume_error": False, "bad_input": False}

        def handle_line(line: str) -> None:
            if not line.strip():
                return
            if not is_progress_only(line):
                tail.append(line)
            pages = _PAGES_RE.search(line)
            if pages is not None:
                if progress_cb is not None:
                    done, total = int(pages.group(1)), int(pages.group(2))
                    progress_cb(
                        tr_format(QCoreApplication.translate("MokuroRunner", "Page %1 of %2"), done, total),
                        (done / total) if total else None,
                    )
                return
            if _MODEL_DOWNLOAD_RE.search(line):
                if progress_cb is not None:
                    progress_cb(
                        QCoreApplication.translate("MokuroRunner", "Downloading OCR models (first run only)"), None
                    )
                return
            device = _DEVICE_RE.search(line)
            if device is not None:
                if progress_cb is not None:
                    progress_cb(
                        tr_format(QCoreApplication.translate("MokuroRunner", "Loading models (%1)"), device.group(1)),
                        None,
                    )
                return
            processed = _PROCESSED_RE.search(line)
            if processed is not None:
                seen["processed"] = (int(processed.group(1)), int(processed.group(2)))
                return
            if _VOLUME_ERROR_RE.search(line):
                seen["volume_error"] = True
            if _BAD_INPUT_RE.search(line):
                seen["bad_input"] = True

        result = run_supervised(
            self._build_cmd(volume, options),
            timeout_s=_VOLUME_TIMEOUT_S,
            cancel=cancel_event,
            env=self._child_env(),
            line_callback=handle_line,
            combine_stderr=True,
            retain_output=False,
            op="mokuro",
            noise_filter=is_progress_only,
            treat_cr_as_newline=True,
        )

        if isinstance(result.error, FileNotFoundError):
            raise MokuroNotFoundError(MOKURO_MISSING_HINT) from result.error
        if result.state is SupervisedState.CANCELLED:
            return MokuroResult(MokuroStatus.CANCELLED, None)
        if result.state is SupervisedState.TIMED_OUT:
            raise MokuroError(f"mokuro timed out after 6 hours on {volume.source.name}")
        if result.state is SupervisedState.FAILED:
            if result.returncode is None and result.error is not None:
                raise MokuroError(f"mokuro process failed: {result.error}") from result.error
            raise MokuroError(self._failure_message(volume, tail, result.returncode))
        # Exit 0 is not success: mokuro returns 0 after logging its own errors.
        processed = seen["processed"]
        ok = (
            isinstance(processed, tuple)
            and processed[0] >= 1
            and not seen["volume_error"]
            and not seen["bad_input"]
            and volume.output.is_file()
        )
        if not ok:
            raise MokuroError(self._failure_message(volume, tail, result.returncode))
        logger.info("mokuro run complete: %s", volume.output)
        return MokuroResult(MokuroStatus.DONE, volume.output)

    def _build_cmd(self, volume: MokuroVolume, options: MokuroOptions) -> list[str]:
        cmd = [
            resolve_mokuro(self._config),
            str(volume.source),
            "--disable_confirmation=True",
            "--ignore_errors=True",
            "--legacy_html=False",
        ]
        if options.force_cpu:
            cmd.append("--force_cpu=True")
        if options.no_cache:
            cmd.append("--no_cache=True")
        return cmd

    @staticmethod
    def _child_env() -> dict[str, str]:
        env = scrubbed_python_env()
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "NO_COLOR": "1"})
        return env

    @staticmethod
    def _failure_message(volume: MokuroVolume, tail: collections.deque[str], returncode: int | None) -> str:
        detail = "\n".join(tail).strip()
        head = f"mokuro did not produce {volume.output.name}"
        if returncode not in (0, None):
            head += f" (exit code {returncode})"
        return f"{head}.\n{detail}" if detail else f"{head}."
