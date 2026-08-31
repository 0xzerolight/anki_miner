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
those roots on ``sys.path`` so a plain ``import jieba`` resolves. Because the
root IS the ``sys.path`` entry, a wheel's top-level sibling modules
(``_kiwipiepy.abi3.so``) are promoted there beside the package dir — see
``ArtifactSpec.root_members``.

A component is skipped when it is already satisfied — by an extracted directory
whose sentinels are all present, or by a package importable from outside the
pack roots (a pip install with the language's extra needs no pack at all). That
is what keeps the download proportional: a bundled Korean user fetches the
model, not the engine that shipped beside it.

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
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from functools import cache
from importlib.util import find_spec
from pathlib import Path, PurePosixPath
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


def _pack_roots(code: str) -> tuple[Path, ...]:
    """Return every directory this module may have put *code*'s packages in."""
    roots = [language_pack_root(code)]
    if code == _LEGACY_KO_CODE:
        roots.append(legacy_ko_model_root())
    return tuple(roots)


def _resolved(path: Path) -> Path:
    """Resolve *path* for comparison, tolerating an unresolvable one."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - resolve() is non-strict; only exotic FS errors land here
        return path


def _spec_locations(spec: object) -> Iterator[Path]:
    """Yield the filesystem locations *spec* would import from.

    ``origin`` is None for a namespace package — exactly what a pack dir whose
    ``__init__.py`` went missing becomes — so its search locations are read too,
    and they are what identifies it. Built-in and frozen modules yield nothing,
    which is right: they are never pack-provided.
    """
    origin = getattr(spec, "origin", None)
    if isinstance(origin, str) and origin not in ("built-in", "frozen"):
        yield Path(origin)
    for entry in getattr(spec, "submodule_search_locations", None) or ():
        if isinstance(entry, str):
            yield Path(entry)


def _importable_outside_packs(code: str, name: str) -> bool:
    """Return True when *name* imports from somewhere this module does not own.

    The pip tier: a source install with the language's extra has the package in
    site-packages and needs no pack at all. A resolution that lands INSIDE a pack
    root is not an answer, because the pack root is on ``sys.path`` as soon as
    one component is complete — see :func:`component_satisfied`. Nothing is
    imported; ``find_spec`` only locates.
    """
    try:
        spec = find_spec(name)
    except (ImportError, ValueError):
        return False
    if spec is None:
        return False
    roots = [_resolved(root) for root in _pack_roots(code)]
    for location in _spec_locations(spec):
        resolved = _resolved(location)
        if any(resolved == root or root in resolved.parents for root in roots):
            return False
    return True


def _sentinels_present(directory: Path, sentinels: tuple[str, ...]) -> bool:
    """Return True when every sentinel of an extracted component is on disk.

    Every sentinel must be present: an engine that loads all of them treats a
    directory missing one as a crash at parse time, not a degraded start.
    ``reconcile_dir`` first, so a crash inside ``atomic_replace_dir`` that left
    only a ``.bak-`` sibling is recovered rather than reported as missing.
    """
    reconcile_dir(directory)
    return all((directory / name).is_file() for name in sentinels)


def _root_members_for(comp: PackComponent) -> tuple[str, ...]:
    """Return the root-member prefixes *comp*'s artifact declares here.

    Empty when no artifact resolves for this platform/Python: an already-extracted
    pack from another interpreter must not be judged against prefixes we cannot
    read off a spec.
    """
    spec = _artifact_for(comp)
    return () if spec is None else spec.root_members


def _root_member_present(base: Path, prefix: str) -> bool:
    """Return True when at least one file under *base* matches *prefix*.

    Prefix-matched rather than named: one manifest entry (``_kiwipiepy.``) covers
    ``.abi3.so`` and ``.pyd``, and any auditwheel sibling that shares the stem.
    """
    pure = PurePosixPath(prefix)
    parent = base / pure.parent
    try:
        return any(entry.is_file() and entry.name.startswith(pure.name) for entry in parent.iterdir())
    except OSError:
        return False


def _component_complete(base: Path, comp: PackComponent) -> bool:
    """Return True when *base* holds a usable extraction of *comp*.

    Both halves must be there: the package dir with every sentinel, AND one file
    per declared root-member prefix beside it. A kiwipiepy dir without
    ``_kiwipiepy.abi3.so`` passes its sentinels and raises ModuleNotFoundError on
    import, so counting it as installed would be an unrepairable state — nothing
    would ever download the missing half.
    """
    if not _sentinels_present(base / comp.import_name, comp.sentinels):
        return False
    return all(_root_member_present(base, prefix) for prefix in _root_members_for(comp))


def _candidate_bases(code: str, import_name: str) -> Iterator[Path]:
    """Yield the pack roots that could hold *import_name*, best first."""
    yield language_pack_root(code)
    if code == _LEGACY_KO_CODE and import_name == _LEGACY_KO_COMPONENT:
        yield legacy_ko_model_root()


def _installed_dir(code: str, comp: PackComponent) -> Path | None:
    """Return the extracted directory providing *comp*, or None."""
    for base in _candidate_bases(code, comp.import_name):
        if _component_complete(base, comp):
            return base / comp.import_name
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


def component_satisfied(code: str, comp: PackComponent, root: Path | None = None) -> bool:
    """Return True when *comp* needs no download into *root*.

    For the canonical root (``root=None``, or *root* equal to
    :func:`language_pack_root`) the full ladder applies: a complete extracted
    component (the pack, or the legacy ``ko_model/`` tier) OR a package importable
    from OUTSIDE both of those directories (a pip install with the language's
    extra).

    The outside qualifier is load-bearing. :func:`ensure_language_packs_on_syspath`
    puts the pack root on ``sys.path`` as soon as any one component is complete,
    so from then on ``find_spec`` resolves the pack's OWN copy of every package it
    holds. An importable tier that accepted that would report a damaged component
    — an antivirus quarantine of ``_kiwipiepy.abi3.so``, a half-deleted package
    dir — as installed, disabling the one button that would repair it. A
    pack-provided package therefore has to pass :func:`_component_complete` like
    any other; a site-packages one is unaffected.

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
        return _installed_dir(code, comp) is not None or _importable_outside_packs(code, comp.import_name)
    return _component_complete(root, comp)


def is_installed(code: str) -> bool:
    """Return True when every required component of *code*'s pack is satisfied.

    The CANONICAL root only — this is the app's own "is Korean ready?" question,
    and a seeded directory somewhere else is not an answer to it. False for a
    language with no pack manifest: there is nothing to install and nothing
    installed.
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

    Components already satisfied in *root* are skipped (see
    :func:`component_satisfied` for what that means for a non-canonical root), so
    a bundled install that ships an engine downloads only what it lacks. Each
    remaining component is downloaded to a ``.part`` file inside *root*,
    sha256-verified, extracted into a fresh staging dir and atomically
    ``os.replace``d onto ``root/<import_name>``, with any declared root members
    promoted beside it. The ``.part`` artifact is always removed. A cancellation
    or any failure leaves nothing partial promoted; components installed earlier
    in the same call stay.

    Args:
        code: Language code with a ``languages/<code>/pack.py`` manifest.
        root: Managed directory for the pack; created if missing. Usually
            :func:`language_pack_root`; the release CI seeds a scratch directory
            instead, which is why every check here reads *root* rather than the
            canonical location.
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
        # Satisfaction is read from the root being filled, so seeding a
        # non-canonical directory neither skips everything nor re-downloads what
        # a previous seed into it already put there.
        if component_satisfied(code, comp, root):
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
    sdist's ``PKG-INFO``) and for anything an ``exclude`` entry matches. An
    entry ending in ``/`` is a directory prefix and takes the whole subtree;
    every other entry matches that EXACT relative path and nothing else.

    Plain prefix matching also swallowed each excluded file's neighbours a
    suffix away: jieba's ``finalseg/prob_start.p`` exclude took
    ``finalseg/prob_start.py`` with it — the table CPython imports — so the
    installed pack died on ``import jieba``.
    """
    if not name.startswith(spec.member_prefix):
        return None
    relative = name[len(spec.member_prefix) :]
    excluded = any(relative.startswith(entry) if entry.endswith("/") else relative == entry for entry in spec.exclude)
    if not relative or excluded:
        return None
    return relative


def _wanted_root(name: str, spec: ArtifactSpec) -> str | None:
    """Return the pack-root-relative path for a declared root member, or None.

    Matched against the RAW archive member name, and the member keeps that path
    under the pack root. For a wheel — the only kind that has these in practice —
    the archive root is the layout site-packages would get, so
    ``_kiwipiepy.abi3.so`` lands directly beside ``kiwipiepy/``.
    """
    if not any(name.startswith(prefix) for prefix in spec.root_members):
        return None
    return name


def _member_destination(name: str, spec: ArtifactSpec, pkg_dir: Path, root_stage: Path) -> tuple[Path, bool] | None:
    """Return where archive member *name* is staged and whether it is a root member."""
    relative = _wanted(name, spec)
    if relative is not None:
        return _safe_member_path(pkg_dir, relative), False
    root_relative = _wanted_root(name, spec)
    if root_relative is not None:
        return _safe_member_path(root_stage, root_relative), True
    return None


def _extract_wheel(part_path: Path, pkg_dir: Path, root_stage: Path, spec: ArtifactSpec) -> int:
    """Stream the wheel's wanted members out; return the PACKAGE member count.

    Root members are staged too but not counted: the gate on them is per declared
    prefix, checked on the staged tree.
    """
    package_count = 0
    with zipfile.ZipFile(part_path) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            target = _member_destination(name, spec, pkg_dir, root_stage)
            if target is None:
                continue
            dest, is_root = target
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Streamed: a wheel member can be tens of MB and reading it whole
            # would spike the resident set of a GUI process.
            with zf.open(name) as source, dest.open("wb") as out:
                shutil.copyfileobj(source, out)
            if not is_root:
                package_count += 1
    return package_count


def _extract_sdist(part_path: Path, pkg_dir: Path, root_stage: Path, spec: ArtifactSpec) -> int:
    """Stream the sdist's wanted members out; return the PACKAGE member count."""
    package_count = 0
    with tarfile.open(part_path, mode="r:gz") as tf:
        for member in tf:
            if not member.isfile():
                continue
            target = _member_destination(member.name, spec, pkg_dir, root_stage)
            if target is None:
                continue
            dest, is_root = target
            dest.parent.mkdir(parents=True, exist_ok=True)
            source = tf.extractfile(member)
            if source is None:  # pragma: no cover - isfile() already excludes these
                continue
            with source, dest.open("wb") as out:
                shutil.copyfileobj(source, out)
            if not is_root:
                package_count += 1
    return package_count


def _promote_root_members(root_stage: Path, root: Path) -> None:
    """Move every staged root member into the pack root, keeping its path."""
    for staged in sorted(root_stage.rglob("*")):
        if not staged.is_file():
            continue
        dest = root / staged.relative_to(root_stage)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, dest)


def _extract_component(part_path: Path, root: Path, comp: PackComponent, spec: ArtifactSpec) -> None:
    """Extract one component's package tree and atomically promote it.

    Members under ``spec.member_prefix`` are written into a fresh staging dir
    with their relative structure preserved and the prefix stripped; packaging
    metadata and excluded subtrees are skipped. Members matching
    ``spec.root_members`` are staged separately, because they belong at the pack
    root rather than inside the package dir.

    Promotion order is root members first, package dir last: the package dir is
    what satisfaction keys on, so it becomes visible only once its siblings are
    already in place.
    """
    staging = Path(tempfile.mkdtemp(prefix=f".staging-pack-{comp.import_name}-", dir=root))
    pkg_dir = staging / comp.import_name
    # A sibling of pkg_dir inside the same staging dir, so a root member named
    # after the package cannot collide with the package tree mid-extraction.
    root_stage = staging / "__root__"
    try:
        try:
            if spec.kind == "wheel":
                extracted = _extract_wheel(part_path, pkg_dir, root_stage, spec)
            else:
                extracted = _extract_sdist(part_path, pkg_dir, root_stage, spec)
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
        # A repackaged wheel that moved or dropped the extension module must
        # refuse here, not promote a package dir whose import raises.
        missing += [prefix + "*" for prefix in spec.root_members if not _root_member_present(root_stage, prefix)]
        if missing:
            raise SetupError(f"the {comp.import_name} archive is missing {', '.join(missing)}")

        _promote_root_members(root_stage, root)
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
            if any(_component_complete(root, comp) for comp in pack.components):
                appended |= _append_to_syspath(root)

        legacy_root = legacy_ko_model_root()
        legacy_pack = load_pack(_LEGACY_KO_CODE)
        legacy_comp = (
            next((c for c in legacy_pack.components if c.import_name == _LEGACY_KO_COMPONENT), None)
            if legacy_pack is not None
            else None
        )
        if legacy_comp is not None and _component_complete(legacy_root, legacy_comp):
            appended |= _append_to_syspath(legacy_root)

        if appended:
            importlib.invalidate_caches()
    except MemoryError:
        raise  # never degrade a real allocation failure (service_factory.py policy)
    except Exception as exc:  # noqa: BLE001  (best-effort; a path problem must not abort boot)
        logger.debug("Language pack syspath injection skipped: exc=%s", type(exc).__name__)
