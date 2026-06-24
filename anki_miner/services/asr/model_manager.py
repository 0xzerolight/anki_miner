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

    Checks recursively for a ``model.bin`` file anywhere under *models_root*
    (excluding *models_root* itself). faster-whisper places model files in a
    snapshot directory several levels deep under ``download_root``; treating
    ``model.bin`` presence as the signal is robust to layout changes.

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Directory that contains downloaded model subdirectories;
            typically ``config.asr_models_root``.

    Returns:
        ``True`` if the model files are already present, ``False`` otherwise.
    """
    if not models_root.exists():
        return False
    # Walk all subdirectories (not models_root itself) looking for model.bin.
    # Presence of model.bin anywhere below models_root is the signal.
    return any(candidate.parent != models_root for candidate in models_root.rglob("model.bin"))


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
