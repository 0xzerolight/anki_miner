"""In-app installer for the per-language dependency packs.

Stateless, GUI-free service. The PyInstaller bundle cannot carry every mining
language's engine and model — the Korean model alone is ~88 MB — so a language
whose dependencies stay out of the bundle declares them in a
``languages/<code>/pack.py`` manifest and this module fetches them on demand.
Japanese has no manifest: its engine is bundled.

This generalises two predecessors it replaces the reasoning of. From
``asr/onnx_pack_installer`` it takes wheel extraction and the platform/ABI gate;
from the retired ``ko_model_installer`` it takes sdist extraction and the model
sentinels. One directory per language, ``language_packs/<code>/``, holds one
extracted top-level package per component (``language_packs/ko/kiwipiepy_model/``,
``language_packs/zh/jieba/``), and :func:`ensure_language_packs_on_syspath` puts
those roots on ``sys.path`` so a plain ``import jieba`` resolves.

A component is skipped when it is already satisfied — by an importable package
(a pip install with the language's extra needs no pack at all) or by an
extracted directory whose sentinels are all present. That is what keeps the
download proportional: a bundled Korean user fetches the model, not the engine
that shipped beside it.

``ko_model/`` is a READ-ONLY legacy tier: installs that downloaded the Korean
model before packs existed keep working, and nothing is ever written there
again. Fresh downloads land in ``language_packs/ko/``.

Placement mirrors the atomic-staging idiom of both predecessors: members are
extracted into a private staging dir *inside* the pack root (same filesystem),
then ``os.replace`` promotes the package dir, so no partial package is ever
visible. The downloaded ``.part`` artifact is always removed (success, failure,
or cancel).
"""

from __future__ import annotations

import importlib
import logging
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from functools import cache
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

from anki_miner.config import paths
from anki_miner.exceptions import OperationCancelled, SetupError
from anki_miner.interfaces.progress import DownloadProgressFn
from anki_miner.languages import AVAILABLE_LANGUAGES
from anki_miner.languages.pack_spec import ArtifactSpec, LanguagePack, PackComponent
from anki_miner.services._install_common import cleanup_part, sweep_stale, verify_sha256
from anki_miner.services.resource_downloader import download_to_temp
from anki_miner.utils.atomic_io import atomic_replace_dir, reconcile_dir
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

__all__ = [
    "component_path",
    "component_satisfied",
    "ensure_language_packs_on_syspath",
    "install_language_pack",
    "is_installed",
    "language_pack_root",
    "legacy_ko_model_root",
    "load_pack",
    "pack_supported",
]

#: The largest pinned artifact is the ~88 MB Korean model; cap well below
#: resource_downloader's 600 MB default so a wrong or oversized download fails
#: fast instead of filling the disk.
_MAX_ARTIFACT_BYTES = 200 * 1024 * 1024

#: The one component with a pre-pack home on disk (``ko_model/``), read as a
#: fallback so an install that downloaded the Korean model before packs existed
#: is not asked to download it again.
_LEGACY_KO_CODE = "ko"
_LEGACY_KO_COMPONENT = "kiwipiepy_model"


def language_pack_root(code: str) -> Path:
    """Return the managed directory holding *code*'s downloaded packages.

    Sits in the app home beside ``asr_models/``, ``cuda_libs/`` and
    ``onnx_pack/``. The home is read from ``config.paths`` at CALL time rather
    than snapshotted at import, so the test-home isolation fixtures redirect it
    like every other managed directory.
    """
    return paths.ANKI_MINER_HOME / "language_packs" / code


def legacy_ko_model_root(config_dir: Path | None = None) -> Path:
    """Return the pre-pack Korean model directory (read-only).

    The directory the retired ``ko_model_installer`` wrote, and the one the
    bundle smoke still seeds. Nothing writes here any more; it is consulted so
    an existing 88 MB download keeps counting as installed.

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
    module_name = f"anki_miner.languages.{code}.pack"
    try:
        if find_spec(module_name) is None:
            return None
        module = importlib.import_module(module_name)
    except (ImportError, ValueError, TypeError):
        return None
    pack = getattr(module, "PACK", None)
    return pack if isinstance(pack, LanguagePack) else None


def _artifact_for(comp: PackComponent) -> ArtifactSpec | None:
    """Return the artifact to download for *comp* here, or None.

    ``None`` when the component pins a CPython ABI this interpreter is not (the
    wheel would be ABI-incompatible) or no artifact is pinned for this
    platform/arch.
    """
    if comp.abi is not None and sys.version_info[:2] != comp.abi:
        return None
    if comp.universal is not None:
        return comp.universal
    if comp.per_platform is None:
        return None
    return comp.per_platform.get((sys.platform, platform.machine()))


def pack_supported(code: str) -> bool:
    """Return True when every REQUIRED component of *code*'s pack resolves here.

    Optional components are ignored: opencc has no wheel for some platforms and
    Chinese still mines without it, so their absence must not make the whole
    pack undownloadable.
    """
    pack = load_pack(code)
    if pack is None:
        return False
    return all(_artifact_for(comp) is not None for comp in pack.components if comp.required)


def _importable(name: str) -> bool:
    """Return True when *name* is importable, without importing it."""
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _sentinels_present(directory: Path, sentinels: tuple[str, ...]) -> bool:
    """Return True when every sentinel of an extracted component is on disk.

    Every sentinel must be present: an engine that loads all of them treats a
    directory missing one as a crash at parse time, not a degraded start.
    ``reconcile_dir`` first, so a crash inside ``atomic_replace_dir`` that left
    only a ``.bak-`` sibling is recovered rather than reported as missing.
    """
    reconcile_dir(directory)
    return all((directory / name).is_file() for name in sentinels)


def _candidate_dirs(code: str, import_name: str) -> Iterator[Path]:
    """Yield the directories that could hold *import_name*, best first."""
    yield language_pack_root(code) / import_name
    if code == _LEGACY_KO_CODE and import_name == _LEGACY_KO_COMPONENT:
        yield legacy_ko_model_root() / import_name


def _installed_dir(code: str, comp: PackComponent) -> Path | None:
    """Return the extracted directory providing *comp*, or None."""
    for directory in _candidate_dirs(code, comp.import_name):
        if _sentinels_present(directory, comp.sentinels):
            return directory
    return None


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


def component_satisfied(code: str, comp: PackComponent) -> bool:
    """Return True when *comp* needs no download in this install.

    Satisfied by an importable package of that name (a pip install with the
    language's extra) OR by a sentinel-complete extracted directory (the pack,
    or the legacy ``ko_model/`` tier).
    """
    return _importable(comp.import_name) or _installed_dir(code, comp) is not None


def is_installed(code: str) -> bool:
    """Return True when every required component of *code*'s pack is satisfied.

    False for a language with no pack manifest: there is nothing to install and
    nothing installed.
    """
    pack = load_pack(code)
    if pack is None:
        return False
    return all(component_satisfied(code, comp) for comp in pack.components if comp.required)


def _check_cancelled(cancelled_check: Callable[[], bool] | None, code: str) -> None:
    if cancelled_check is not None and cancelled_check():
        raise OperationCancelled(f"{code} language pack installation cancelled")


def install_language_pack(
    code: str,
    root: Path,
    *,
    progress: DownloadProgressFn | None = None,
    cancelled_check: Callable[[], bool] | None = None,
) -> Path:
    """Download, verify, and install *code*'s missing pack components into *root*.

    Components already satisfied (importable, or extracted with every sentinel
    present) are skipped, so a bundled install that ships an engine downloads
    only what it lacks. Each remaining component is downloaded to a ``.part``
    file inside *root*, sha256-verified, extracted into a fresh staging dir and
    atomically ``os.replace``d onto ``root/<import_name>``. The ``.part``
    artifact is always removed. A cancellation or any failure leaves nothing
    partial promoted; components installed earlier in the same call stay.

    Args:
        code: Language code with a ``languages/<code>/pack.py`` manifest.
        root: Managed directory for the pack; created if missing. Typically
            :func:`language_pack_root`.
        progress: Optional ``(downloaded, total, message)`` callback.
        cancelled_check: Optional zero-arg predicate. Checked before each heavy
            step (download, verify, extract); on cancellation no partial package
            is promoted and ``OperationCancelled`` is raised.

    Returns:
        The *root* path.

    Raises:
        SetupError: When the language has no pack, when a required component has
            no artifact for this platform/Python, or on download failure, sha256
            mismatch, or a bad/empty archive.
        OperationCancelled: When *cancelled_check* returns True.
    """
    pack = load_pack(code)
    if pack is None:
        raise SetupError(f"{code} has no downloadable language pack.")

    _check_cancelled(cancelled_check, code)

    # Resolved up front so an unsupported platform refuses before any bytes are
    # fetched, rather than half-installing and failing on the last component.
    plan: list[tuple[PackComponent, ArtifactSpec]] = []
    for comp in pack.components:
        if component_satisfied(code, comp):
            continue
        spec = _artifact_for(comp)
        if spec is None:
            if comp.required:
                raise SetupError(
                    f"The {code} language pack is not supported on this platform/Python "
                    f"({sys.platform}/{platform.machine()}/"
                    f"{sys.version_info[0]}.{sys.version_info[1]})."
                )
            logger.info("Language pack %s: no %s artifact for this platform; skipping", code, comp.import_name)
            continue
        plan.append((comp, spec))

    root.mkdir(parents=True, exist_ok=True)
    # Reclaim orphans from a previous crashed/killed install (a hard kill between
    # download and os.replace leaves a .part artifact and/or a .staging-* dir).
    # Promoted package dirs are never touched, so is_installed is unaffected.
    sweep_stale(root)

    total = len(plan)
    for index, (comp, spec) in enumerate(plan, start=1):
        logger.info(
            "Language pack install: code=%s component=%s host=%s",
            code,
            comp.import_name,
            urlsplit(spec.url).hostname or "-",
        )
        _check_cancelled(cancelled_check, code)

        # The code, not a translated name: this service is GUI-free and the
        # caller relabels. i/n counts what this run actually downloads.
        label = f"{code.upper()} pack ({index}/{total}): downloading"

        def _on_progress(downloaded: int, artifact_total: int, _msg: str, label: str = label) -> None:
            if progress is not None:
                progress(downloaded, artifact_total, label)

        part_path = download_to_temp(
            spec.url,
            dest_dir=root,
            progress=_on_progress if progress is not None else None,
            cancelled_check=cancelled_check,
            max_bytes=_MAX_ARTIFACT_BYTES,
            # Keyed on the pinned checksum, so the key names exactly the bytes it
            # stands for: a pin bump changes the sha and therefore the key, and a
            # stale partial from the old artifact is never resumed into the new
            # one (D16-C).
            resume_key=f"pack-{code}-{comp.import_name}-{spec.sha256[:16]}",
        )
        try:
            _check_cancelled(cancelled_check, code)
            verify_sha256(part_path, spec.sha256, f"{comp.import_name} download")
            _check_cancelled(cancelled_check, code)
            _extract_component(part_path, root, comp, spec)
        finally:
            cleanup_part(part_path)

    byte_count = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    log_summary(
        logger,
        "Language pack install done",
        code=code,
        installed=root,
        components=total,
        bytes=byte_count,
    )
    return root


def _safe_member_path(base: Path, member: str) -> Path:
    """Resolve *member* under *base*, rejecting path traversal (zip/tar slip)."""
    base_resolved = base.resolve()
    dest = (base / member).resolve()
    if base_resolved != dest and base_resolved not in dest.parents:
        raise SetupError(f"unsafe path in the {base.name} archive: {member}")
    return dest


def _wanted(name: str, spec: ArtifactSpec) -> str | None:
    """Return the package-relative path for archive member *name*, or None.

    None for anything outside ``member_prefix`` (a wheel's ``.dist-info``, an
    sdist's ``PKG-INFO``) and for anything under an ``exclude`` prefix.
    """
    if not name.startswith(spec.member_prefix):
        return None
    relative = name[len(spec.member_prefix) :]
    if not relative or any(relative.startswith(excluded) for excluded in spec.exclude):
        return None
    return relative


def _extract_wheel(part_path: Path, pkg_dir: Path, spec: ArtifactSpec) -> int:
    """Stream the wheel's package members into *pkg_dir*; return the count."""
    extracted = 0
    with zipfile.ZipFile(part_path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            relative = _wanted(name, spec)
            if relative is None:
                continue
            dest = _safe_member_path(pkg_dir, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Streamed: a wheel member can be tens of MB and reading it whole
            # would spike the resident set of a GUI process.
            with zf.open(name) as source, dest.open("wb") as out:
                shutil.copyfileobj(source, out)
            extracted += 1
    return extracted


def _extract_sdist(part_path: Path, pkg_dir: Path, spec: ArtifactSpec) -> int:
    """Stream the sdist's package members into *pkg_dir*; return the count."""
    extracted = 0
    with tarfile.open(part_path, mode="r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            relative = _wanted(member.name, spec)
            if relative is None:
                continue
            dest = _safe_member_path(pkg_dir, relative)
            dest.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:  # pragma: no cover - isfile() already excludes these
                continue
            with source, dest.open("wb") as out:
                shutil.copyfileobj(source, out)
            extracted += 1
    return extracted


def _extract_component(part_path: Path, root: Path, comp: PackComponent, spec: ArtifactSpec) -> None:
    """Extract one component's package tree and atomically promote it.

    Members under ``spec.member_prefix`` are written into a fresh staging dir
    with their relative structure preserved and the prefix stripped; packaging
    metadata and excluded subtrees are skipped. The staged package dir then
    replaces ``root/<import_name>``.
    """
    staging = Path(tempfile.mkdtemp(prefix=f".staging-pack-{comp.import_name}-", dir=root))
    pkg_dir = staging / comp.import_name
    try:
        try:
            if spec.kind == "wheel":
                extracted = _extract_wheel(part_path, pkg_dir, spec)
            else:
                extracted = _extract_sdist(part_path, pkg_dir, spec)
        except (zipfile.BadZipFile, tarfile.TarError) as exc:
            logger.warning(
                "Language pack install failed: stage=extract component=%s exc=%s",
                comp.import_name,
                type(exc).__name__,
            )
            raise SetupError(f"the {comp.import_name} download is not a valid archive: {exc}") from exc

        if not extracted:
            raise SetupError(f"the {comp.import_name} archive contained no {spec.member_prefix} payload")

        missing = [name for name in comp.sentinels if not (pkg_dir / name).is_file()]
        if missing:
            raise SetupError(f"the {comp.import_name} archive is missing {', '.join(missing)}")

        atomic_replace_dir(pkg_dir, root / comp.import_name)
    finally:
        # Best-effort cleanup on success or an already-failing extraction path.
        shutil.rmtree(staging, ignore_errors=True)


def _append_to_syspath(directory: Path) -> bool:
    """Append *directory* to ``sys.path`` if absent; return True if appended.

    Append, never insert: a pack root holds only the packages the pack
    installed, so it never needs to win priority, and appending means it cannot
    shadow a same-named module already on the path.
    """
    entry = str(directory)
    if entry in sys.path:
        return False
    sys.path.append(entry)
    return True


def ensure_language_packs_on_syspath() -> None:
    """Make every installed language pack importable, once, at boot.

    A pack root holding at least one sentinel-complete component is appended to
    ``sys.path`` so ``import jieba`` / ``import kiwipiepy`` resolve against the
    extracted copy; the legacy ``ko_model/`` directory is appended on the same
    terms. Idempotent, and best-effort: a path problem must never be what stops
    the app from starting, so nothing here raises.
    """
    appended = False
    try:
        for code in AVAILABLE_LANGUAGES:
            pack = load_pack(code)
            if pack is None or not pack_supported(code):
                continue
            root = language_pack_root(code)
            if any(_sentinels_present(root / comp.import_name, comp.sentinels) for comp in pack.components):
                appended |= _append_to_syspath(root)

        legacy_root = legacy_ko_model_root()
        legacy_pack = load_pack(_LEGACY_KO_CODE)
        legacy_comp = (
            next((c for c in legacy_pack.components if c.import_name == _LEGACY_KO_COMPONENT), None)
            if legacy_pack is not None
            else None
        )
        if legacy_comp is not None and _sentinels_present(legacy_root / _LEGACY_KO_COMPONENT, legacy_comp.sentinels):
            appended |= _append_to_syspath(legacy_root)

        if appended:
            importlib.invalidate_caches()
    except MemoryError:
        raise  # never degrade a real allocation failure (service_factory.py policy)
    except Exception as exc:  # noqa: BLE001  (best-effort; a path problem must not abort boot)
        logger.debug("Language pack syspath injection skipped: exc=%s", type(exc).__name__)
