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

KNOWN_MODELS: frozenset[str] = frozenset({"large-v3", "small"})
DEFAULT_MODEL: str = "large-v3"


def is_downloaded(name: str, models_root: Path) -> bool:
    """Return True if the model *name* is present in *models_root*.

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Directory that contains downloaded model subdirectories;
            typically ``config.asr_models_root``.

    Returns:
        ``True`` if the model files are already present, ``False`` otherwise.

    Raises:
        NotImplementedError: Wave B fills the body.
    """
    raise NotImplementedError


def download(name: str, models_root: Path, cancel_event=None) -> None:
    """Download model *name* into *models_root*.

    Args:
        name: Model identifier (must be in ``KNOWN_MODELS``).
        models_root: Target directory; created if it does not exist.
        cancel_event: Optional ``threading.Event``; the download is aborted
            cooperatively when the event is set.

    Raises:
        NotImplementedError: Wave B fills the body.
    """
    raise NotImplementedError
