"""In-app installer for the Korean (kiwipiepy) language model.

Stateless, GUI-free service. The PyInstaller bundle ships the kiwipiepy ENGINE
but not its ~88 MB model: bundling the model grew the release artifacts by a
fifth on Linux and by nearly a third on Windows, past the size gate's tolerance,
for a language most users never mine. The model therefore follows the
cuDNN/cuBLAS and onnxruntime precedent and arrives as an on-demand pack, so a
bundled-installer user can enable Korean without a rebundle. A pip install with
the ``[ko]`` extra already pulls ``kiwipiepy-model``, so the pack is bundle-only
in practice — ``languages.ko.tokenizer`` prefers the installed package and falls
back to this pack.

The artifact is the PyPI sdist (the only form ``kiwipiepy-model`` publishes): a
``.tar.gz`` whose ``kiwipiepy_model/`` directory IS the model. That directory is
extracted whole, structure preserved, into ``<root>/kiwipiepy_model/``, and
``Kiwi(model_path=...)`` is pointed at it.

Placement mirrors the atomic-staging idiom in ``onnx_pack_installer`` and
``cuda_pack_installer``: members are extracted into a private staging dir
*inside* ``root`` (same filesystem), then ``os.replace`` promotes the model dir
so no partial model is ever visible. The downloaded ``.part`` sdist is always
removed (success, failure, or cancel).

Bumping the model version means updating ``KO_MODEL_VERSION``, ``KO_MODEL_URL``
and ``KO_MODEL_SHA256`` together (alass-style); the sha is what the resume key is
derived from, so a bump can never continue the old partial into the new archive.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from anki_miner.config import paths
from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.services._install_common import cleanup_part, sweep_stale, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.atomic_io import atomic_replace_dir, reconcile_dir
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = [
    "KO_MODEL_SHA256",
    "KO_MODEL_URL",
    "KO_MODEL_VERSION",
    "MODEL_DIR_NAME",
    "install_ko_model",
    "is_installed",
    "ko_model_path",
    "ko_model_root",
]

#: The sdist is ~88 MB; cap well below resource_downloader's 600 MB default so a
#: wrong or oversized download fails fast instead of filling the disk.
_MAX_SDIST_BYTES = 200 * 1024 * 1024

#: Pinned kiwipiepy-model release. url + sha256 move together, never apart.
KO_MODEL_VERSION = "0.23.0"
KO_MODEL_URL = (
    "https://files.pythonhosted.org/packages/77/59/"
    "28403890c5f757254bf2068ff321fb3e656fb2e5658a3de8bfc092e4fd83/"
    "kiwipiepy_model-0.23.0.tar.gz"
)
KO_MODEL_SHA256 = "498a22f5585e6c4a162423d7557eb3ee3f71cddc6e0aeb2650c50467e85933e2"

#: Directory the model lands in, under the pack root. Named after the package it
#: replaces so a support log reads the same either way.
MODEL_DIR_NAME = "kiwipiepy_model"

#: sdist members under this prefix are the model; everything else (PKG-INFO,
#: MANIFEST.in) is packaging metadata Kiwi never reads.
_MEMBER_PREFIX = f"kiwipiepy_model-{KO_MODEL_VERSION}/{MODEL_DIR_NAME}/"

#: Files ``Kiwi()`` cannot start without. All three must be present for the pack
#: to count as installed, so a half-written directory is never mistaken for one.
_MODEL_SENTINELS = ("sj.morph", "default.dict", "combiningRule.txt")


def ko_model_root(config_dir: Path | None = None) -> Path:
    """Return the managed directory for the downloaded Korean model.

    Sits in the app home beside ``asr_models/``, ``cuda_libs/`` and
    ``onnx_pack/``. The home is read from ``config.paths`` at CALL time rather
    than snapshotted at import, so the test-home isolation fixtures redirect it
    like every other managed directory (``utils.ytdlp_resolver`` does the same).

    Args:
        config_dir: Optional override for the app home; defaults to
            ``ANKI_MINER_HOME``.
    """
    base = paths.ANKI_MINER_HOME if config_dir is None else Path(config_dir)
    return base / "ko_model"


def ko_model_path(root: Path) -> Path:
    """Return the directory to hand ``Kiwi(model_path=...)`` for pack *root*."""
    return root / MODEL_DIR_NAME


def is_installed(root: Path) -> bool:
    """Return True if a usable Korean model is present in the pack *root*.

    Cheap: three existence checks on the extracted model dir. Every sentinel must
    be present — ``Kiwi()`` loads all of them, and a directory missing one is a
    crash at parse time rather than a degraded start.
    """
    model = ko_model_path(root)
    reconcile_dir(model)
    return all((model / name).is_file() for name in _MODEL_SENTINELS)


def install_ko_model(
    root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancel_event=None,
) -> Path:
    """Download, verify, and install the Korean model into *root*.

    Downloads the pinned sdist to a ``.part`` file inside *root*, verifies its
    sha256, extracts the ``kiwipiepy_model/`` tree into a fresh staging dir, and
    atomically ``os.replace``s it onto ``root/kiwipiepy_model`` (replacing any
    existing copy). The ``.part`` sdist is always removed. A cancellation or any
    failure leaves nothing partial promoted.

    Args:
        root: Managed directory for the pack; created if missing. Typically
            :func:`ko_model_root`.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancel_event: Optional ``threading.Event``. Checked before each heavy
            step (download, verify, extract); on cancellation no partial model is
            promoted and ``OperationCancelled`` is raised.

    Returns:
        The *root* path.

    Raises:
        SetupError: On cancellation, download failure, sha256 mismatch, or a
            bad/empty archive.
    """
    logger.info(
        "KO model install: host=%s version=%s",
        urlsplit(KO_MODEL_URL).hostname or "-",
        KO_MODEL_VERSION,
    )

    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled("Korean model installation cancelled")

    root.mkdir(parents=True, exist_ok=True)
    # Reclaim orphans from a previous crashed/killed install (a hard kill between
    # download and os.replace leaves an 88 MB .part sdist and/or a .staging-* dir
    # behind). is_installed only inspects kiwipiepy_model/, so these cannot
    # false-positive a partial install — they just accumulate.
    sweep_stale(root)
    cancelled_check = cancel_event.is_set if cancel_event is not None else None

    def _on_progress(downloaded: int, total: int, _msg: str) -> None:
        if progress is not None:
            progress(downloaded, total, "Korean model: downloading")

    part_path = download_to_temp(
        KO_MODEL_URL,
        dest_dir=root,
        progress=_on_progress if progress is not None else None,
        cancelled_check=cancelled_check,
        max_bytes=_MAX_SDIST_BYTES,
        # Keyed on the pinned checksum, so the key names exactly the bytes it
        # stands for: a version bump changes the sha and therefore the key, and a
        # stale partial from the old sdist is never resumed into the new one.
        resume_key=f"ko-model-{KO_MODEL_SHA256[:16]}",
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Korean model installation cancelled")

        verify_sha256(part_path, KO_MODEL_SHA256, "Korean model download")

        if cancel_event is not None and cancel_event.is_set():
            raise OperationCancelled("Korean model installation cancelled")

        _extract_model(part_path, root)
    finally:
        cleanup_part(part_path)

    target = ko_model_path(root)
    byte_count = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    log_summary(
        logger,
        "KO model install done",
        installed=target,
        bytes=byte_count,
        version=KO_MODEL_VERSION,
    )
    return root


def _safe_member_path(base: Path, member: str) -> Path:
    """Resolve *member* under *base*, rejecting path traversal (tar slip)."""
    base_resolved = base.resolve()
    dest = (base / member).resolve()
    if base_resolved != dest and base_resolved not in dest.parents:
        raise SetupError(f"unsafe path in the Korean model archive: {member}")
    return dest


def _extract_model(part_path: Path, root: Path) -> None:
    """Extract the ``kiwipiepy_model/`` tree and atomically promote it.

    Members under :data:`_MEMBER_PREFIX` are streamed into a fresh staging dir
    with their relative structure preserved (the sdist's packaging metadata is
    skipped — Kiwi never reads it). The staged model dir then replaces
    ``root/kiwipiepy_model``.
    """
    target = ko_model_path(root)
    staging = Path(tempfile.mkdtemp(prefix=".staging-ko-model-", dir=root))
    try:
        try:
            with tarfile.open(part_path, mode="r:gz") as tf:
                extracted = 0
                for member in tf:
                    if not member.isfile() or not member.name.startswith(_MEMBER_PREFIX):
                        continue
                    relative = member.name[len(_MEMBER_PREFIX) :]
                    dest = _safe_member_path(staging / MODEL_DIR_NAME, relative)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    source = tf.extractfile(member)
                    if source is None:  # pragma: no cover - isfile() already excludes these
                        continue
                    # Streamed: the largest member is ~75 MB and reading it whole
                    # would spike the resident set of a GUI process.
                    with source, dest.open("wb") as out:
                        shutil.copyfileobj(source, out)
                    extracted += 1
                if not extracted:
                    raise SetupError("the Korean model archive contained no kiwipiepy_model/ payload")
        except tarfile.TarError as exc:
            logger.warning("KO model install failed: stage=extract exc=%s", type(exc).__name__)
            raise SetupError(f"the Korean model download is not a valid archive: {exc}") from exc

        atomic_replace_dir(staging / MODEL_DIR_NAME, target)
    finally:
        # Best-effort cleanup on success or an already-failing extraction path.
        shutil.rmtree(staging, ignore_errors=True)
