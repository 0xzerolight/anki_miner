"""ASR model management — download and presence checks.

Stateless functions that take ``models_root`` explicitly so they have no
coupling to the global config. Callers are responsible for passing
``config.asr_models_root``.

Worker signal contracts (Task 4 / Task 5 — Wave B implements these):

``SubtitleGenWorker(CancellableWorker)`` (Task 4):
    - ``file_started(int)``                       — index of the file being processed
    - ``file_progress(int, int, str)``            — (idx, pct 0-100, message)
    - ``file_finished(int, object, object)``      — (idx, out_path|None, error_str|None)
    - ``queue_finished()``                        — emitted once when the whole queue is done
    The tab stores the worker on ``self.worker_thread``.

``AsrModelDownloadWorker(CancellableWorker)`` (Task 5):
    - ``status(str)``                             — informational status message
    - ``finished(bool, str)``                     — (ok, message)
    HF download progress is indeterminate; no fake percentage is emitted.
"""

from pathlib import Path

from anki_miner.services.asr import _engine

KNOWN_MODELS: frozenset[str] = frozenset({"large-v3", "small"})
DEFAULT_MODEL: str = "large-v3"


def is_downloaded(name: str, models_root: Path) -> bool:
    """Return True if the model *name* is present in *models_root*.

    Checks recursively for a ``model.bin`` file whose containing path includes
    *name* in at least one ancestor directory name (relative to *models_root*).
    faster-whisper uses an HF-cache layout
    ``models--<org>--faster-whisper-<name>/snapshots/<rev>/model.bin``; matching
    on *name* in the path is robust to org-prefix changes while still being
    model-specific (so ``is_downloaded("large-v3", root)`` is False when only
    ``small`` is present, and vice-versa).

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Directory that contains downloaded model subdirectories;
            typically ``config.asr_models_root``.

    Returns:
        ``True`` if the model files are already present, ``False`` otherwise.
    """
    if not models_root.exists():
        return False
    # Walk all subdirectories looking for model.bin whose path corresponds to
    # the requested model name.  faster-whisper uses an HF-cache layout:
    #   models--<org>--faster-whisper-<name>/snapshots/<rev>/model.bin
    # so we accept a model.bin only when *name* appears in at least one of its
    # ancestor directory names (relative to models_root).  This is deliberately
    # kept robust: we never hardcode the org prefix.
    for candidate in models_root.rglob("model.bin"):
        if candidate.parent == models_root:
            # model.bin sitting directly in models_root itself — not a valid layout
            continue
        rel_parts = candidate.relative_to(models_root).parts
        if any(name in part for part in rel_parts):
            return True
    return False


def download(name: str, models_root: Path, cancel_event=None) -> None:
    """Download model *name* into *models_root*.

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Target directory; created if it does not exist.
        cancel_event: Optional ``threading.Event``; the download is aborted
            cooperatively when the event is set.
    """
    if cancel_event is not None and cancel_event.is_set():
        return
    models_root.mkdir(parents=True, exist_ok=True)
    _engine.get_download_fn()(name, download_root=models_root)
