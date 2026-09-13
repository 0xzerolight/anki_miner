"""In-app installer for the per-language dependency packs.

Stateless, GUI-free service. The PyInstaller bundle cannot carry every mining
language's engine and model — the Korean model alone is ~88 MB — so a language
whose dependencies stay out of the bundle declares them in a
``languages/<code>/pack.py`` manifest and this module fetches them on demand.
Japanese has no manifest: its engine is bundled.

The install machinery itself — artifact resolution, the satisfaction ladder,
download/verify/staged-extract/atomic-promote, the ``sys.path`` append — lives
in ``services/pack_installer.py``, shared with the ASR engine pack
(``services/asr/asr_pack_installer.py``). This module is the language adapter:
one directory per language, ``language_packs/<code>/``, holds one extracted
top-level package per component (``language_packs/ko/kiwipiepy_model/``,
``language_packs/zh/jieba/``), and :func:`ensure_language_packs_on_syspath`
puts those roots on ``sys.path`` so a plain ``import jieba`` resolves.

A component is skipped when it is already satisfied — by an extracted directory
whose sentinels are all present, or by a package importable from outside the
pack roots (a pip install with the language's extra needs no pack at all). That
is what keeps the download proportional: a bundled Korean user fetches the
model, not the engine that shipped beside it.

``ko_model/`` is a READ-ONLY legacy tier: installs that downloaded the Korean
model before packs existed keep working, and nothing is ever written there
again. Fresh downloads land in ``language_packs/ko/``.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Iterator
from functools import cache
from importlib.util import find_spec
from pathlib import Path

from anki_miner.config import paths
from anki_miner.exceptions import SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.languages import AVAILABLE_LANGUAGES, SHARED_PACK_CODES
from anki_miner.languages.pack_spec import LanguagePack, PackComponent
from anki_miner.services.pack_installer import (
    append_to_syspath,
    artifact_for,
    component_complete,
    components_supported,
    importable_outside,
    install_components,
    installed_dir,
)
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = [
    "combined_download_mb",
    "component_path",
    "component_satisfied",
    "ensure_language_packs_on_syspath",
    "install_language_pack",
    "is_installed",
    "language_pack_root",
    "legacy_ko_model_root",
    "load_pack",
    "pack_codes",
    "pack_supported",
    "requirement_satisfied",
]

#: The one component with a pre-pack home on disk (``ko_model/``), read as a
#: fallback so an install that downloaded the Korean model before packs existed
#: is not asked to download it again.
_LEGACY_KO_CODE = "ko"
_LEGACY_KO_COMPONENT = "kiwipiepy_model"


def language_pack_root(code: str) -> Path:
    """Return the managed directory holding *code*'s downloaded packages.

    Sits in the app home beside ``asr_pack/``, ``cuda_libs/`` and
    ``onnx_pack/``. The home is read from ``config.paths`` at CALL time rather
    than snapshotted at import, so the test-home isolation fixtures redirect it
    like every other managed directory.
    """
    return paths.ANKI_MINER_HOME / "language_packs" / code


def legacy_ko_model_root(config_dir: Path | None = None) -> Path:
    """Return the pre-pack Korean model directory (read-only).

    The directory the retired ``ko_model_installer`` wrote. Nothing writes here
    any more — the bundle smoke seeds ``language_packs/<code>/`` like an install
    would — and it is consulted only so an existing 88 MB download from before
    packs existed keeps counting as installed.

    Args:
        config_dir: Optional override for the app home; defaults to
            ``ANKI_MINER_HOME``.
    """
    base = paths.ANKI_MINER_HOME if config_dir is None else Path(config_dir)
    return base / "ko_model"


@cache
def load_pack(code: str) -> LanguagePack | None:
    """Return *code*'s pack manifest, or None when the language ships none.

    Cached: the manifests are frozen module-level data, and the availability
    probes call this on every refresh. ``None`` covers both "this language needs
    no pack" (ja) and any code without an importable manifest.
    """
    package_name = f"anki_miner.languages.{code}"
    module_name = f"{package_name}.pack"
    try:
        # The package is probed before its ``.pack`` leaf: ``find_spec`` on a
        # leaf of an absent package RAISES, which the branch below would log as
        # a broken manifest on every boot for a shared engine pack not yet landed.
        if find_spec(package_name) is None or find_spec(module_name) is None:
            # A clean absence: this language ships no pack (ja), the code is not
            # one of ours, or a shared engine pack's package has not landed yet.
            # Nothing is wrong, so nothing is warned about.
            logger.debug("Language pack manifest absent: code=%s", code)
            return None
        module = importlib.import_module(module_name)
    except (ImportError, ValueError, TypeError) as exc:
        # A RAISING probe is the other diagnosis: the manifest is there and
        # unimportable, which silently downgrades an installed pack to "not
        # installed" everywhere this None is consumed.
        log_summary(
            logger,
            "Language module probe failed",
            level=logging.WARNING,
            module=module_name,
            exc=f"{type(exc).__name__}: {exc}",
        )
        return None
    pack = getattr(module, "PACK", None)
    return pack if isinstance(pack, LanguagePack) else None


def pack_codes() -> tuple[str, ...]:
    """Every code a pack consumer walks: the mining languages, then the shared engine packs."""
    return (*AVAILABLE_LANGUAGES, *SHARED_PACK_CODES)


def pack_supported(code: str) -> bool:
    """Return True when every REQUIRED component of *code*'s pack resolves here.

    A pack that ``requires`` another is supported only when each requirement is
    available too: already satisfied, or downloadable here.
    """
    pack = load_pack(code)
    if pack is None:
        return False
    return components_supported(pack.components) and all(_requirement_available(req) for req in pack.requires)


def _requirement_available(code: str) -> bool:
    """Return True when this machine has *code*'s required components, or can download them."""
    pack = load_pack(code)
    if pack is None:
        return False
    return all(
        component_satisfied(code, comp) or artifact_for(comp) is not None for comp in pack.components if comp.required
    )


def _pack_roots(code: str) -> tuple[Path, ...]:
    """Return every directory this module may have put *code*'s packages in."""
    roots = [language_pack_root(code)]
    if code == _LEGACY_KO_CODE:
        roots.append(legacy_ko_model_root())
    return tuple(roots)


def _candidate_bases(code: str, import_name: str) -> Iterator[Path]:
    """Yield the pack roots that could hold *import_name*, best first."""
    yield language_pack_root(code)
    if code == _LEGACY_KO_CODE and import_name == _LEGACY_KO_COMPONENT:
        yield legacy_ko_model_root()


def _installed_dir(code: str, comp: PackComponent) -> Path | None:
    """Return the extracted directory providing *comp*, or None."""
    return installed_dir(comp, tuple(_candidate_bases(code, comp.import_name)))


def component_path(code: str, import_name: str) -> Path | None:
    """Return the on-disk directory providing *import_name*, or None.

    The DISK tier alone — an importable package of the same name is not an
    answer here, because the callers that need a path (Kiwi's ``model_path``)
    need one the pack actually owns. Nothing is imported or loaded.
    """
    pack = load_pack(code)
    if pack is None:
        return None
    comp = next((c for c in pack.components if c.import_name == import_name), None)
    return None if comp is None else _installed_dir(code, comp)


def component_satisfied(code: str, comp: PackComponent, root: Path | None = None) -> bool:
    """Return True when *comp* needs no download into *root*.

    For the canonical root (``root=None``, or *root* equal to
    :func:`language_pack_root`) the full ladder applies: a complete extracted
    component (the pack, or the legacy ``ko_model/`` tier) OR a package importable
    from OUTSIDE both of those directories (a pip install with the language's
    extra) — see ``pack_installer.importable_outside`` for why "outside" is
    load-bearing.

    For any OTHER root the answer comes from that directory alone — no
    ``find_spec``, no legacy tier. Two reasons, both about the same caller:
    ``install_language_pack`` is asked to FILL a directory (CI seeds one under
    ``$RUNNER_TEMP`` for the release smokes), and a runner that pip-installed the
    language extra would otherwise report every component satisfied and seed an
    empty tree. Answering from the given root also makes the skip contract hold:
    a second seed into the same directory downloads nothing.

    *root* is compared as given, not resolved: an equal-but-differently-spelled
    canonical path falls to the disk-only branch, which at worst downloads
    something a pip install already provided.
    """
    if root is None or root == language_pack_root(code):
        return _installed_dir(code, comp) is not None or importable_outside(comp.import_name, _pack_roots(code))
    return component_complete(root, comp)


def is_installed(code: str) -> bool:
    """Return True when every required component of *code*'s pack is satisfied.

    The CANONICAL root only — this is the app's own "is Korean ready?" question,
    and a seeded directory somewhere else is not an answer to it. False for a
    language with no pack manifest: there is nothing to install and nothing
    installed. Every pack the manifest ``requires`` must be satisfied too.
    """
    pack = load_pack(code)
    if pack is None:
        return False
    own = all(component_satisfied(code, comp) for comp in pack.components if comp.required)
    return own and all(requirement_satisfied(req) for req in pack.requires)


def requirement_satisfied(code: str, root: Path | None = None) -> bool:
    """Return True when every REQUIRED component of *code*'s pack needs no download into *root*.

    Per component (:func:`component_satisfied`), never :func:`pack_supported`:
    an engine pack pins a CPython ABI, and an importable engine from a pip
    install must satisfy it on any interpreter.
    """
    pack = load_pack(code)
    if pack is None:
        return False
    return all(component_satisfied(code, comp, root) for comp in pack.components if comp.required)


def combined_download_mb(code: str) -> int:
    """Return the size one download button fetches: this pack plus each requirement still missing."""
    pack = load_pack(code)
    if pack is None:
        return 0
    total = pack.approx_download_mb
    for req in pack.requires:
        req_pack = load_pack(req)
        if req_pack is not None and not requirement_satisfied(req):
            total += req_pack.approx_download_mb
    return total


def _requirement_root(code: str, requirement: str, root: Path) -> Path:
    """Return where *requirement* is installed when *code* is installed into *root*.

    Its canonical home when *root* is *code*'s canonical home; otherwise a
    sibling of *root*, the seeder's ``<dest>/<code>/`` layout.
    """
    return language_pack_root(requirement) if root == language_pack_root(code) else root.parent / requirement


def _relabelled(progress: DownloadProgressFn | None, requirement: str, code: str) -> DownloadProgressFn | None:
    """Report a prerequisite's progress under the requesting pack's label.

    The settings row runs one task per button, and its progress lines are
    prefixed with the requesting code; ``_SPACY pack`` would reach the user.
    """
    if progress is None:
        return None
    old, new = f"{requirement.upper()} ", f"{code.upper()} "

    def _progress(downloaded: int, total: int, message: str) -> None:
        progress(downloaded, total, new + message[len(old) :] if message.startswith(old) else message)

    return _progress


def install_language_pack(
    code: str,
    root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancelled_check: Callable[[], bool] | None = None,
) -> Path:
    """Download, verify, and install *code*'s missing pack components into *root*.

    Components already satisfied in *root* are skipped (see
    :func:`component_satisfied` for what that means for a non-canonical root), so
    a bundled install that ships an engine downloads only what it lacks. The
    download/verify/extract/promote contract is ``pack_installer.install_components``'.

    Every code the pack ``requires`` is installed first — into its canonical
    home when *root* is this pack's canonical home, or as a sibling of a seed
    root — and a requirement still unsatisfied afterwards refuses the install.

    Args:
        code: Language code with a ``languages/<code>/pack.py`` manifest.
        root: Managed directory for the pack; created if missing. Usually
            :func:`language_pack_root`; the release CI seeds a scratch directory
            instead, which is why every check here reads *root* rather than the
            canonical location.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancelled_check: Optional zero-arg predicate.

    Returns:
        The *root* path.

    Raises:
        SetupError: When the language has no pack, when a required component has
            no artifact for this platform/Python, on download failure, sha256
            mismatch, or a bad/empty archive, or when a required pack is still
            not installed after its own install.
        OperationCancelled: When *cancelled_check* returns True.
    """
    pack = load_pack(code)
    if pack is None:
        raise SetupError(f"{code} has no downloadable language pack.")
    for requirement in pack.requires:
        requirement_root = _requirement_root(code, requirement, root)
        if not requirement_satisfied(requirement, requirement_root):
            install_language_pack(
                requirement,
                requirement_root,
                progress=_relabelled(progress, requirement, code),
                cancelled_check=cancelled_check,
            )
        if not requirement_satisfied(requirement, requirement_root):
            raise SetupError(f"The {code} language pack needs the {requirement} pack, which is not installed.")
    return install_components(
        code,
        pack.components,
        root,
        # The noun the user-facing errors name: byte-identical to the strings
        # this function raised before the core moved out.
        display_noun=f"{code} language pack",
        # Satisfaction is read from the root being filled, so seeding a
        # non-canonical directory neither skips everything nor re-downloads what
        # a previous seed into it already put there.
        satisfied=lambda comp: component_satisfied(code, comp, root),
        progress=progress,
        cancelled_check=cancelled_check,
    )


def ensure_language_packs_on_syspath() -> None:
    """Make every installed language pack importable, once, at boot.

    Walks the mining languages and the shared engine packs (:func:`pack_codes`).
    A pack root holding at least one sentinel-complete component is appended to
    ``sys.path`` so ``import jieba`` / ``import kiwipiepy`` resolve against the
    extracted copy; the legacy ``ko_model/`` directory is appended on the same
    terms. Idempotent, and best-effort: a path problem must never be what stops
    the app from starting, so nothing here raises.
    """
    appended = False
    # The root under consideration when something goes wrong: without it the
    # failure names no subject, and "an installed pack is not importable" is
    # unanswerable without knowing which folder was being injected.
    current_root: Path | None = None
    try:
        for code in pack_codes():
            current_root = None
            pack = load_pack(code)
            if pack is None or not pack_supported(code):
                continue
            root = language_pack_root(code)
            current_root = root
            # Spelled out with the module-bound ``component_complete`` (not the
            # core's ``root_has_complete_component``): the failure-logging test
            # patches this module's name to make the loop raise.
            if any(component_complete(root, comp) for comp in pack.components):
                appended |= append_to_syspath(root)

        legacy_root = legacy_ko_model_root()
        current_root = legacy_root
        legacy_pack = load_pack(_LEGACY_KO_CODE)
        legacy_comp = (
            next((c for c in legacy_pack.components if c.import_name == _LEGACY_KO_COMPONENT), None)
            if legacy_pack is not None
            else None
        )
        if legacy_comp is not None and component_complete(legacy_root, legacy_comp):
            appended |= append_to_syspath(legacy_root)

        if appended:
            importlib.invalidate_caches()
    except MemoryError:
        raise  # never degrade a real allocation failure (service_factory.py policy)
    except Exception as exc:  # noqa: BLE001 — bucket: best-effort boot; a path problem must not abort startup
        # WARNING, not DEBUG: every downstream symptom of this is the language
        # reporting itself uninstalled after the user installed it, and the
        # injection is the only step between the two.
        log_summary(
            logger,
            "Language pack syspath injection failed",
            level=logging.WARNING,
            root=current_root,
            exc=f"{type(exc).__name__}: {exc}",
        )
