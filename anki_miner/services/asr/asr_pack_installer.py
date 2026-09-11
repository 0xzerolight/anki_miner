"""In-app installer for the ASR engine pack (faster-whisper + CTranslate2 + PyAV).

Stateless, GUI-free adapter over ``services/pack_installer.py`` — the same
core the language packs use. The frozen bundle excludes the whole CTranslate2
stack (~80 MB compressed) so a first-time user downloads that much less; a
user who generates subtitles fetches it once from Settings -> Transcription &
Alignment. The pack lands in ``ANKI_MINER_HOME/asr_pack/``, one extracted
top-level package per component (plus the ``<pkg>.libs/`` trees the Linux and
Windows wheels carry beside their package), and
:func:`ensure_asr_pack_on_syspath` appends that root to ``sys.path`` — at boot,
right after the language packs, and again after an in-session install, before
the engine is re-probed.

``_engine.available()`` stays ``find_spec``-based and is the ONLY availability
probe: once the root is on ``sys.path`` the engine is importable exactly as it
was when bundled. A pip install with the ``[asr]`` extra satisfies every
component from site-packages (``pack_installer.importable_outside``) and never
downloads anything.

Not supported (``asr_pack_supported`` False): any interpreter other than the
bundle's CPython 3.12 (ctranslate2/yaml pin cp312 wheels; on 3.11/3.13 pip
users install the extra instead), and any platform outside the release matrix.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from pathlib import Path

from anki_miner.config import paths
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.languages.pack_spec import PackComponent
from anki_miner.services.asr.asr_pack import PACK
from anki_miner.services.pack_installer import (
    append_to_syspath,
    component_complete,
    components_supported,
    importable_outside,
    install_components,
    installed_dir,
    root_has_complete_component,
)
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = [
    "PACK",
    "asr_pack_root",
    "asr_pack_supported",
    "component_satisfied",
    "ensure_asr_pack_on_syspath",
    "install_asr_pack",
    "is_installed",
]


def asr_pack_root() -> Path:
    """Return the managed directory holding the downloaded engine packages.

    Sits in the app home beside ``onnx_pack/``, ``cuda_libs/`` and
    ``language_packs/<code>/``. Read from ``config.paths`` at CALL time, not
    snapshotted at import, so the test-home isolation fixtures redirect it like
    every other managed directory.
    """
    return paths.ANKI_MINER_HOME / "asr_pack"


def asr_pack_supported() -> bool:
    """Return True when every component of the pack resolves an artifact here."""
    return components_supported(PACK.components)


def component_satisfied(comp: PackComponent, root: Path | None = None) -> bool:
    """Return True when *comp* needs no download into *root*.

    For the canonical root (``root=None`` or :func:`asr_pack_root`): a complete
    extracted component, OR a package importable from OUTSIDE the pack root (a
    pip ``[asr]`` install). For any other root — the release CI seeds a scratch
    directory for the bundle smoke — the answer comes from that directory alone,
    so a runner that pip-installed the extra still seeds a full tree.
    """
    canonical = asr_pack_root()
    if root is None or root == canonical:
        return installed_dir(comp, (canonical,)) is not None or importable_outside(comp.import_name, (canonical,))
    return component_complete(root, comp)


def is_installed() -> bool:
    """Return True when every component is satisfied in the canonical root."""
    return all(component_satisfied(comp) for comp in PACK.components)


def install_asr_pack(
    root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancelled_check: Callable[[], bool] | None = None,
) -> Path:
    """Download, verify, and install every missing component into *root*.

    The download/verify/extract/promote contract is
    ``pack_installer.install_components``'; progress lines arrive as
    ``"ASR pack (i/n): downloading"`` for the GUI task to relabel.

    Raises:
        SetupError: Unsupported platform/Python, download failure, sha256
            mismatch, or a bad/empty archive.
        OperationCancelled: When *cancelled_check* returns True.
    """
    return install_components(
        PACK.name,
        PACK.components,
        root,
        display_noun="ASR engine pack",
        satisfied=lambda comp: component_satisfied(comp, root),
        progress=progress,
        cancelled_check=cancelled_check,
    )


def ensure_asr_pack_on_syspath() -> None:
    """Make an installed (or partly installed) engine pack importable.

    Appends the root when it holds at least one complete component and
    invalidates the import caches, so a ``find_spec`` that answered None a
    moment ago answers from the pack. Called once at boot after the language
    packs and again after an in-session install, BEFORE the engine re-probe.

    The caches are invalidated whenever the root is on ``sys.path`` — not only
    on the call that appended it: a cancelled install leaves a partial root
    that boot already appended, and the resumed in-session install adds new
    package dirs under that same entry, which the path finder's per-directory
    cache would otherwise keep answering None for. Idempotent and best-effort:
    a path problem must never stop the app.
    """
    try:
        root = asr_pack_root()
        if root_has_complete_component(root, PACK.components):
            append_to_syspath(root)
            importlib.invalidate_caches()
    except MemoryError:
        raise  # never degrade a real allocation failure (service_factory.py policy)
    except Exception as exc:  # noqa: BLE001 — bucket: best-effort boot; a path problem must not abort startup
        # WARNING, not DEBUG: the symptom downstream is the engine reporting
        # itself missing after the user downloaded it.
        log_summary(
            logger,
            "ASR pack syspath injection failed",
            level=logging.WARNING,
            exc=f"{type(exc).__name__}: {exc}",
        )
